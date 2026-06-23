# Palm modality model assets (Android)

Palm needs **one required** asset (the hand detector) and has **one optional**
asset (a trained encoder — an accuracy upgrade). Palm recognition itself works with
**no trained model** via the built-in Gabor encoder (`PalmGabor`), mirroring the
server. `PalmEngine.available()` tracks only the hand detector; without it the app
runs face-only and nothing breaks.

## 1. `hand_landmarker.task` — MediaPipe Hands  (REQUIRED for palm)
Used to find the palm and its 21 landmarks for ROI extraction (`PalmRoi`). This is
the only asset palm needs to function. Download the bundled model from Google:

    https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task

Drop the file here as `hand_landmarker.task`.

## 2. `palm_ccnet.onnx` — palm-print encoder  (OPTIONAL accuracy upgrade)
Without this file, palm recognition runs on the built-in `PalmGabor` encoder (no
training needed). Add a trained CCNet-family encoder exported to ONNX (`PalmEmbedder`)
to raise accuracy. Same export contract as the server (see `palm/models/README_MODEL.md`):

- **Input:** float32 NCHW, RGB, pixel values in `[0, 1]`, size = `PalmConfig.ROI_SIZE`
  (128×128).
- **Output:** an `[N, D]` embedding; `D` must equal `PalmConfig.EMBED_DIM` (128).
  The encoder L2-normalises and matches with cosine.

Export it once offline (PyTorch + the CCNet repo), then copy `palm_ccnet.onnx` here.
For best mobile accuracy, fine-tune on smartphone palm datasets (MPD / NTU-CP)
before export.

> Keep both files out of version control if they're large; ship them in the APK's
> assets at build time. The face model (`w600k_r50.onnx`) is documented separately
> in `README_MODEL.md`.
