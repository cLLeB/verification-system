"""Publish the palm CCNet ONNX (and optionally the hand model) to Hugging Face.

The 257 MB CCNet ONNX is too big for git, so host it on HF instead. After uploading,
set PALM_MODEL_HF_REPO=<your-repo> on the server/app and the engine downloads it once
on first use (palm.engine.ensure_model) — every deployment then gets the trained
encoder automatically, like the face model pack.

Needs a Hugging Face account + a write token (https://huggingface.co/settings/tokens):
    huggingface-cli login            # or set HF_TOKEN
    python -m palm.training.upload_hf --repo your-org/palm-ccnet-onnx \
        --onnx palm/models/palm_ccnet.onnx
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="Upload palm models to Hugging Face.")
    ap.add_argument("--repo", required=True, help="HF repo id, e.g. your-org/palm-ccnet-onnx")
    ap.add_argument("--onnx", default=os.path.join("palm", "models", "palm_ccnet.onnx"))
    ap.add_argument("--hand", default=None, help="optional: also upload hand_landmarker.task")
    ap.add_argument("--private", action="store_true", help="create the repo private")
    ap.add_argument("--token", default=os.environ.get("HF_TOKEN"))
    args = ap.parse_args()

    from huggingface_hub import HfApi, create_repo
    create_repo(args.repo, repo_type="model", exist_ok=True, private=args.private, token=args.token)
    api = HfApi()
    for path, name in [(args.onnx, os.path.basename(args.onnx)),
                       (args.hand, "hand_landmarker.task" if args.hand else None)]:
        if path and os.path.exists(path):
            print(f"uploading {path} -> {args.repo}/{name} ...")
            api.upload_file(path_or_fileobj=path, path_in_repo=name,
                            repo_id=args.repo, repo_type="model", token=args.token)
    print(f"[ok] done. Now set PALM_MODEL_HF_REPO={args.repo} on the server.")


if __name__ == "__main__":
    main()
