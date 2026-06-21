"""Speed patch for fingerprint_enhancer.

The library's Gabor ridge filter fills the output one pixel at a time in a pure
Python double loop (~100k iterations), which costs several seconds per image.
The maths is a per-pixel selection from a bank of oriented Gabor filters, so it
vectorises cleanly: convolve the image once per orientation (in C via
cv2.filter2D), then gather each pixel from the filter matching its ridge
orientation. Output is numerically identical at interior pixels (where the
original computed values); border pixels are masked to 0 exactly as before.

We monkeypatch the library method in place. If the library's internals ever
change shape, `install()` fails safe and leaves the original (slow) method.
"""

from __future__ import annotations

import numpy as np
import scipy.ndimage

try:
    import cv2
    from fingerprint_enhancer.fingerprint_image_enhancer import FingerprintImageEnhancer
    _AVAILABLE = True
except Exception:  # pragma: no cover
    _AVAILABLE = False

_INSTALLED = False


def _sep_gauss(img, gauss_col):
    """Separable 2D Gaussian convolution (cv2) == ndimage.convolve(img, g@g.T)."""
    k = gauss_col.reshape(-1, 1).astype(np.float64)
    return cv2.sepFilter2D(img, cv2.CV_64F, k, k, borderType=cv2.BORDER_REFLECT)


def _fast_ridge_orient(self) -> None:
    """Vectorised ridge-orientation field (cv2 instead of scipy ndimage.convolve)."""
    sze = np.fix(6 * self.gradient_sigma)
    if np.remainder(sze, 2) == 0:
        sze = sze + 1
    gauss = cv2.getGaussianKernel(int(sze), self.gradient_sigma)
    filter_gauss = gauss * gauss.T
    filter_grad_y, filter_grad_x = np.gradient(filter_gauss)

    gradient_x = cv2.filter2D(self._normim, cv2.CV_64F, filter_grad_x,
                              borderType=cv2.BORDER_CONSTANT)
    gradient_y = cv2.filter2D(self._normim, cv2.CV_64F, filter_grad_y,
                              borderType=cv2.BORDER_CONSTANT)

    grad_x2 = gradient_x ** 2
    grad_y2 = gradient_y ** 2
    grad_xy = gradient_x * gradient_y

    sze = int(np.fix(6 * self.block_sigma))
    gauss_b = cv2.getGaussianKernel(sze, self.block_sigma)
    grad_x2 = _sep_gauss(grad_x2, gauss_b)
    grad_y2 = _sep_gauss(grad_y2, gauss_b)
    grad_xy = 2 * _sep_gauss(grad_xy, gauss_b)

    denom = np.sqrt(grad_xy ** 2 + (grad_x2 - grad_y2) ** 2) + np.finfo(float).eps
    sin_2_theta = grad_xy / denom
    cos_2_theta = (grad_x2 - grad_y2) / denom

    if self.orient_smooth_sigma:
        sze = np.fix(6 * self.orient_smooth_sigma)
        if np.remainder(sze, 2) == 0:
            sze = sze + 1
        gauss_o = cv2.getGaussianKernel(int(sze), self.orient_smooth_sigma)
        cos_2_theta = _sep_gauss(cos_2_theta, gauss_o)
        sin_2_theta = _sep_gauss(sin_2_theta, gauss_o)

    self._orientim = np.pi / 2 + np.arctan2(sin_2_theta, cos_2_theta) / 2


def _fast_ridge_filter(self) -> None:
    norm_im = np.double(self._normim)
    rows, cols = norm_im.shape
    newim = np.zeros((rows, cols))

    freq_1d = np.reshape(self._freq, (1, rows * cols))
    ind = np.where(freq_1d > 0)
    ind = np.array(ind)[1, :]
    if ind.size == 0:
        self._binim = newim < self.ridge_filter_thresh
        return

    non_zero = np.double(np.round(freq_1d[0][ind] * 100)) / 100
    unfreq = np.unique(non_zero)

    sigmax = 1 / unfreq[0] * self.relative_scale_factor_x
    sigmay = 1 / unfreq[0] * self.relative_scale_factor_y
    sze = int(np.round(3 * np.max([sigmax, sigmay])))

    mesh_x, mesh_y = np.meshgrid(
        np.linspace(-sze, sze, (2 * sze + 1)),
        np.linspace(-sze, sze, (2 * sze + 1)),
    )
    reffilter = np.exp(
        -((mesh_x ** 2) / (sigmax * sigmax) + (mesh_y ** 2) / (sigmay * sigmay))
    ) * np.cos(2 * np.pi * unfreq[0] * mesh_x)

    angle_range = int(180 / self.angle_inc)
    filt_rows, filt_cols = reffilter.shape
    gabor_filter = np.zeros((angle_range, filt_rows, filt_cols))
    for idx in range(angle_range):
        gabor_filter[idx] = scipy.ndimage.rotate(
            reffilter, -(idx * self.angle_inc + 90), reshape=False
        )

    maxorientindex = int(np.round(180 / self.angle_inc))
    orientindex = np.round(self._orientim / np.pi * 180 / self.angle_inc).astype(int)
    orientindex[orientindex < 1] += maxorientindex
    orientindex[orientindex > maxorientindex] -= maxorientindex

    # One convolution per orientation (C-speed), then per-pixel gather.
    filtered = np.empty((angle_range, rows, cols))
    for idx in range(angle_range):
        filtered[idx] = cv2.filter2D(norm_im, cv2.CV_64F, gabor_filter[idx])

    sel = np.clip(orientindex - 1, 0, angle_range - 1)
    ii, jj = np.indices((rows, cols))
    gathered = filtered[sel, ii, jj]

    # Validity mask identical to the original (freq>0 and away from the border).
    maxsze = int(sze)
    valid = self._freq > 0
    border = np.zeros((rows, cols), dtype=bool)
    border[maxsze + 1:rows - maxsze, maxsze + 1:cols - maxsze] = True
    mask = valid & border
    newim[mask] = gathered[mask]

    self._binim = newim < self.ridge_filter_thresh


def install() -> bool:
    """Apply the speed patch. Returns True if installed."""
    global _INSTALLED
    if not _AVAILABLE or _INSTALLED:
        return _INSTALLED
    # Best-effort safety: only patch the known (name-mangled) method names.
    cls = FingerprintImageEnhancer
    if hasattr(cls, "_FingerprintImageEnhancer__ridge_filter"):
        cls._FingerprintImageEnhancer__ridge_filter = _fast_ridge_filter
        _INSTALLED = True
    if hasattr(cls, "_FingerprintImageEnhancer__ridge_orient"):
        cls._FingerprintImageEnhancer__ridge_orient = _fast_ridge_orient
    return _INSTALLED
