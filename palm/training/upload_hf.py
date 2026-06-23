"""Publish the palm models to Hugging Face so every deployment can auto-fetch them.

The CCNet ONNX (257 MB fp32 / 128 MB fp16) is too big for git, so host it on HF. After
uploading, set PALM_MODEL_HF_REPO=<your-repo> on the server/app and the engine
downloads it once on first use (palm.engine.ensure_model) — like the face model pack.

Uploads (whatever exists locally):
  * palm_ccnet.onnx        (fp32, primary — what the engine fetches by default)
  * palm_ccnet_fp16.onnx   (fp16, ~lossless half-size, for size-constrained deploys)
  * hand_landmarker.task   (MediaPipe Hands, for ROI — convenience copy of Google's)
  * README.md + config.json (model card + the input/output contract)

Needs a Hugging Face write token (https://huggingface.co/settings/tokens):
    huggingface-cli login                 # OR: set HF_TOKEN
    python -m palm.training.upload_hf --repo <your-username>/palm-ccnet-onnx
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile

_EMBED_DIM = 2048
_ROI = 128

_MODEL_CARD = """---
license: other
tags: [palmprint, biometrics, onnx, ccnet]
library_name: onnx
---

# Palm-print encoder (CCNet) — ONNX

ONNX export of the **CCNet** palm-print feature extractor for contactless palm
recognition. Used by the face+palm verification backbone: it turns a normalised
palm ROI into an L2-normalised embedding matched by cosine similarity.

## Files
| file | precision | size | use |
|------|-----------|------|-----|
| `palm_ccnet.onnx` | fp32 | ~257 MB | servers (default fetch) |
| `palm_ccnet_fp16.onnx` | fp16 | ~128 MB | mobile / size-constrained (~lossless) |
| `hand_landmarker.task` | — | ~8 MB | MediaPipe Hands ROI detector (Google) |

## Input / output contract
- **Input**: `float32` NCHW `[1, 1, 128, 128]` — **grayscale**, pixel values in `[0, 1]`.
- **Output**: `[1, 2048]` L2-normalised embedding. Match with cosine; calibrate the
  threshold on your data (the backbone does this adaptively).

## Provenance & attribution
Exported (feature extractor only, not the close-set classifier) from the official
CCNet pretrained **Tongji** checkpoint:
- CCNet — Yang et al., *Comprehensive Competition Mechanism in Palmprint
  Recognition*, IEEE TIFS 2023. Code: https://github.com/Zi-YuanYang/CCNet
- Trained on the Tongji contactless palmprint dataset (respect its terms).

This is a domain-pretrained model; for best accuracy on your own captures, fine-tune
and re-export. You are responsible for license/dataset compliance in your use.
"""


def _write_tmp(name: str, text: str) -> str:
    path = os.path.join(tempfile.gettempdir(), name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Upload palm models to Hugging Face.")
    ap.add_argument("--repo", required=True, help="HF repo id, e.g. <user>/palm-ccnet-onnx")
    ap.add_argument("--onnx", default=os.path.join("palm", "models", "palm_ccnet.onnx"))
    ap.add_argument("--onnx-fp16", default=os.path.join("android", "app", "src", "fp16", "assets", "palm_ccnet.onnx"))
    ap.add_argument("--hand", default=os.path.join("palm", "models", "hand_landmarker.task"))
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--token", default=os.environ.get("HF_TOKEN"))
    args = ap.parse_args()

    from huggingface_hub import HfApi, create_repo
    create_repo(args.repo, repo_type="model", exist_ok=True, private=args.private, token=args.token)
    api = HfApi()

    config = {"architecture": "ccnet", "input": {"shape": [1, 1, _ROI, _ROI],
              "dtype": "float32", "channels": "grayscale", "range": [0, 1]},
              "output": {"embed_dim": _EMBED_DIM, "l2_normalised": True, "metric": "cosine"}}

    uploads = [
        (args.onnx, "palm_ccnet.onnx"),
        (args.onnx_fp16, "palm_ccnet_fp16.onnx"),
        (args.hand, "hand_landmarker.task"),
        (_write_tmp("README.md", _MODEL_CARD), "README.md"),
        (_write_tmp("palm_config.json", json.dumps(config, indent=2)), "config.json"),
    ]
    for path, name in uploads:
        if path and os.path.exists(path):
            print(f"uploading {os.path.basename(path)} -> {args.repo}/{name} ...")
            api.upload_file(path_or_fileobj=path, path_in_repo=name,
                            repo_id=args.repo, repo_type="model", token=args.token)
        else:
            print(f"skip (not found): {path}")
    print(f"\n[ok] published to https://huggingface.co/{args.repo}")
    print(f"     now set  PALM_MODEL_HF_REPO={args.repo}  on the server/app.")


if __name__ == "__main__":
    main()
