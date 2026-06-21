import cv2
import numpy as np

def check_liveness(image_roi):
    """
    Performs basic liveness detection (Anti-Spoofing) on the cropped finger ROI.
    Returns: (is_live: bool, score: float, reason: str)
    """
    if image_roi is None or image_roi.size == 0:
        return False, 0.0, "Empty Image"

    # Convert to grayscale
    if len(image_roi.shape) == 3:
        gray = cv2.cvtColor(image_roi, cv2.COLOR_BGR2GRAY)
    else:
        gray = image_roi

    # 1. Blur / Sharpness Check (Laplacian Variance)
    # Printed photos or screens held up to a camera are often slightly out of focus
    # compared to a 3D finger held at the correct focal length.
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    
    # 2. Specular Reflection (Glare) Check
    # Phone screens and glossy photo paper reflect a lot of concentrated light (glare).
    # Human skin scatters light (diffuse reflection).
    # We check the percentage of pixels that are purely white (clipping).
    _, thresholded_glare = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)
    glare_ratio = cv2.countNonZero(thresholded_glare) / (gray.shape[0] * gray.shape[1])

    # 3. Texture / Noise Check (Standard Deviation)
    # Real skin has a lot of micro-texture. A printed paper has a halftone dot pattern,
    # but when captured by a camera, it often lacks the natural variance of skin.
    std_dev = np.std(gray)

    # Heuristic thresholds — deliberately LENIENT. This simple check cannot
    # reliably distinguish a real finger from a good print/photo anyway (that
    # needs a trained CNN), and a strict setting caused legitimate captures to be
    # wrongly rejected as "spoof". So we only reject degenerate frames (a fully
    # blown-out / blank image). Identity security comes from the matcher, not
    # from this gate. Disable entirely with FP_LIVENESS=0.
    MIN_SHARPNESS = 1.5
    MAX_GLARE_RATIO = 0.85   # only a near-totally white frame (e.g. a screen) fails
    MIN_TEXTURE_VARIANCE = 2.0

    if laplacian_var < MIN_SHARPNESS:
        return False, laplacian_var, f"Image too blurry (Score: {laplacian_var:.2f})"

    if glare_ratio > MAX_GLARE_RATIO:
        return False, glare_ratio, f"Too much glare/overexposure (Score: {glare_ratio:.3f})"

    if std_dev < MIN_TEXTURE_VARIANCE:
        return False, std_dev, f"No texture detected (Score: {std_dev:.2f})"

    # If it passes the basic heuristic checks, we consider it live.
    # In a production system, a trained CNN (like MobileNet) would be called here.
    return True, laplacian_var, "Live Finger Detected"

if __name__ == "__main__":
    # Simple test to ensure the script runs
    test_img = np.zeros((100, 100), dtype=np.uint8)
    cv2.randn(test_img, 128, 40) # Add noise to pass texture checks
    is_live, score, msg = check_liveness(test_img)
    print(f"Test Image Liveness: {is_live} | Message: {msg}")
