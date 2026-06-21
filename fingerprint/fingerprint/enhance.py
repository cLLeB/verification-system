"""Camera image -> clean binary ridge map.

Contactless captures have low, uneven contrast and the ridges are not as crisp
as on a contact sensor. We normalise, boost local contrast, then run Gabor
ridge enhancement (fingerprint_enhancer) which also normalises ridge frequency
(so inter-minutiae distances become comparable across captures of different
finger-to-camera distance).
"""

from __future__ import annotations

import warnings

import cv2
import numpy as np

import fingerprint_enhancer as _fe

from . import _fast_enhance as _fast
from .config import Config, CONFIG

# Vectorise the enhancer's slow per-pixel loops (~5x faster, output ~identical).
# Safe no-op if the library internals differ from what the patch expects.
_fast.install()


def to_grayscale(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("Empty image passed to to_grayscale")
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _resize_to_height(gray: np.ndarray, cfg: Config) -> np.ndarray:
    h, w = gray.shape[:2]
    if h == 0 or w == 0:
        raise ValueError("Zero-sized grayscale image")
    scale = cfg.normalize_height / float(h)
    if abs(scale - 1.0) > 1e-3:
        new_w = max(1, int(round(w * scale)))
        gray = cv2.resize(gray, (new_w, cfg.normalize_height), interpolation=cv2.INTER_CUBIC)
    return gray


def normalize(gray: np.ndarray, cfg: Config = CONFIG) -> np.ndarray:
    """Resize to a canonical height and make contrast exposure-invariant.

    Global intensity normalisation removes brightness/exposure differences
    between captures (a big cause of the same finger failing to match itself),
    then CLAHE lifts faint ridges out of camera noise.
    """
    gray = _resize_to_height(gray, cfg)
    # Stretch global intensity so exposure/gamma differences between captures
    # don't change the downstream ridge map.
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    gray = cv2.bilateralFilter(gray, d=5, sigmaColor=35, sigmaSpace=35)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def measure_sharpness(image: np.ndarray, cfg: Config = CONFIG) -> float:
    """Device-invariant focus score on the resized grayscale ROI.

    Returns the variance of the Laplacian NORMALISED by the image's intensity
    variance. The raw Laplacian variance depends heavily on a camera's contrast
    and resolution (a sharp capture from a low-contrast phone scores far lower
    than from a high-contrast one), so an absolute threshold is not portable
    across phones. The normalised ratio is ~stable for equally-sharp captures
    regardless of camera contrast/exposure, so ONE threshold works across
    devices. Low = blurry / out of focus.
    """
    gray = to_grayscale(image)
    gray = _resize_to_height(gray, cfg).astype(np.float64)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return lap_var / (float(gray.var()) + 1e-6)


def segment_finger(image: np.ndarray, out_shape: tuple) -> "np.ndarray | None":
    """Mask of the fingertip within a colour photo, sized to `out_shape` (h, w).

    Contactless photos include background (desk, etc.) whose texture the Gabor
    step turns into spurious 'ridges'. We segment the finger as the largest
    bright skin-coloured region and return a 0/255 mask so the caller can blank
    the background BEFORE enhancement. Returns None for grayscale input (e.g.
    sensor prints, which have no background) or when no plausible finger is found
    (caller then uses the whole image).
    """
    if image is None or image.ndim != 3:
        return None
    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = ycrcb[:, :, 0], ycrcb[:, :, 1], ycrcb[:, :, 2]
    skin = ((cr >= 137) & (cr <= 178) & (cb >= 80) & (cb <= 128) & (y >= 90)).astype(np.uint8) * 255
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    cnts, _ = cv2.findContours(skin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    full = image.shape[0] * image.shape[1]
    if cv2.contourArea(c) < 0.12 * full:        # no finger filling the frame
        return None
    mask = np.zeros(image.shape[:2], np.uint8)
    cv2.drawContours(mask, [c], -1, 255, -1)     # the finger's own outline (not hull)
    mask = cv2.resize(mask, (out_shape[1], out_shape[0]), interpolation=cv2.INTER_NEAREST)
    mask = cv2.erode(mask, np.ones((11, 11), np.uint8))   # stay clear of the edge
    cov = float((mask > 0).mean())
    if cov < 0.15 or cov > 0.98:                 # implausible -> use whole image
        return None
    return mask


def _gabor(norm: np.ndarray, cfg: Config) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            ridges_bool = _fe.enhance_fingerprint(norm, resize=cfg.enhancer_resize)
        except Exception as exc:  # enhancer can fail on featureless input
            raise RuntimeError(f"Ridge enhancement failed: {exc}") from exc
    # The library returns a boolean array (True == ridge). The downstream
    # minutiae extractor thresholds with `img > 128`, so we MUST scale to 0/255.
    ridges = (np.asarray(ridges_bool).astype(np.uint8)) * 255
    if ridges.max() == 0:
        raise RuntimeError("Enhancement produced an empty ridge map")
    return ridges


def enhance(image: np.ndarray, cfg: Config = CONFIG) -> np.ndarray:
    """Return a binary ridge map (uint8, ridges=255, background=0)."""
    return _gabor(normalize(to_grayscale(image), cfg), cfg)


def process(image: np.ndarray, cfg: Config = CONFIG):
    """Return (normalised grayscale, binary ridge map) in one pass.

    The normalised grayscale feeds SourceAFIS; the ridge map feeds our minutiae
    extractor. For colour photos the background is segmented out and blanked so
    neither matcher picks up desk/wood texture as fingerprint features.
    """
    norm = normalize(to_grayscale(image), cfg)
    mask = segment_finger(image, norm.shape)
    if mask is not None:
        norm = norm.copy()
        norm[mask == 0] = 255                    # blank background -> no features
    ridges = _gabor(norm, cfg)
    if mask is not None:
        if ridges.shape != mask.shape:
            mask = cv2.resize(mask, (ridges.shape[1], ridges.shape[0]),
                              interpolation=cv2.INTER_NEAREST)
        ridges = ridges.copy()
        ridges[mask == 0] = 0
    return norm, ridges


def ridge_area_ratio(binary_ridges: np.ndarray) -> float:
    """Fraction of the image occupied by ridge pixels (a coarse quality cue)."""
    if binary_ridges.size == 0:
        return 0.0
    return float((binary_ridges > 0).mean())
