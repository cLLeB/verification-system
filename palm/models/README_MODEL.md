# Palm-print encoder model (ONNX) — OPTIONAL accuracy upgrade

Palm recognition works **out of the box with no trained model**, using the built-in
classical Gabor encoder (`palm/classical.py`). This file is the *optional* upgrade:
drop in a trained palm-print encoder exported to **ONNX** and `palm.engine` uses it
instead of the classical one for higher accuracy. The server runs it with
`onnxruntime` (no PyTorch at serve time), exactly as the face side runs InsightFace.

So: **no file → palm still runs (classical encoder); file present → palm runs the
trained encoder.** Palm is never gated off by the absence of this file. (Palm does
require the MediaPipe hand detector for ROI extraction — a standard dependency.)

Expected file: `palm/models/palm_ccnet.onnx`
(override with `PALM_MODEL_PATH=/abs/path.onnx`)

## Hand detector (REQUIRED, bundled): `hand_landmarker.task`

Palm ROI extraction uses the MediaPipe **Tasks** HandLandmarker, which needs
`palm/models/hand_landmarker.task` (~7.8 MB, bundled in the repo; override with
`PALM_HAND_MODEL`). Re-download from Google if needed:

    https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task

This is the *only* file palm needs to run (recognition uses the built-in Gabor
encoder). The `palm_ccnet.onnx` above is the optional accuracy upgrade.

> Note: switching encoders (classical ↔ ONNX, or different `embed_dim`) changes the
> embedding space, so re-enrol palms after changing the encoder.

## Contract the ONNX must satisfy

- **Input:** `float32` NCHW, pixel values scaled to `[0, 1]`.
  - 1 channel (grayscale) or 3 channels (RGB) — the engine reads the channel count
    from the model and preprocesses the ROI to match.
  - Spatial size read from the model (e.g. 128×128); falls back to `PalmConfig.roi_size`.
- **Output:** a single `[N, D]` embedding tensor. The engine L2-normalises it and
  matches with cosine similarity.
- **`D` must equal `PalmConfig.embed_dim`** (default 128). If your export has a
  different width, set `PALM_EMBED_DIM=D`. The engine validates this on load and
  raises a clear error on mismatch.

## Recommended source: CCNet family

`CCNet` (Comprehensive Competition Network, IEEE TIFS) is the open SOTA:
- https://github.com/Zi-YuanYang/CCNet  (also CO3Net, CompNet)

### Export sketch (run offline, where PyTorch is installed)

```python
import torch
from models import ccnet                       # from the CCNet repo

net = ccnet(...).eval()
net.load_state_dict(torch.load("checkpoint.pth", map_location="cpu"))

# Wrap so the ONNX returns the EMBEDDING (penultimate features), not class logits.
class Embed(torch.nn.Module):
    def __init__(self, m): super().__init__(); self.m = m
    def forward(self, x): return self.m.getFeatureCode(x)   # repo's feature method

dummy = torch.randn(1, 1, 128, 128)             # match channels/size to training
torch.onnx.export(Embed(net), dummy, "palm_ccnet.onnx",
                  input_names=["input"], output_names=["embedding"],
                  dynamic_axes={"input": {0: "n"}, "embedding": {0: "n"}},
                  opset_version=17)
```

## Tuning to phone captures

Pretrained Tongji/PolyU weights work as a baseline. For best mobile accuracy,
fine-tune on smartphone palm datasets before export:
- **MPD** (Mobile Palmprint Dataset) — phone-captured, finger-gap annotated
- **NTU-CP-v1**, **XJTU-UP**, **BJTU_PalmV2**

Then re-run the offline accuracy check and calibrate `match_threshold` /
`identify_margin` in `palm/calibration.json`.
