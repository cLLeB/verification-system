"""Export a trained CCNet palm-print checkpoint to ONNX for the server/Android.

CCNet (https://github.com/Zi-YuanYang/CCNet) is PyTorch-only with no public ONNX, so
this converts an official pretrained checkpoint (Tongji / IITD) — or your own
fine-tuned one — into the ONNX the palm engine loads.

What it exports: the **feature extractor** (``getFeatureCode``), NOT the close-set
ArcMargin classifier head. Output is a 2048-d L2-normalised embedding matched by
cosine — exactly what open-set verification needs. Input is the same as the palm
engine serves: grayscale, 128x128, [0,1], NCHW.

Usage (run offline, where torch is installed):
    python -m palm.training.export_ccnet_onnx \
        --checkpoint net_params_tongji.pth --num-classes 600 --weight 0.8 \
        --out palm/models/palm_ccnet.onnx

num-classes per dataset (only affects the discarded classifier head): Tongji 600,
IITD 460, PolyU 378, Multi-Spec 500. Then set PALM_EMBED_DIM=2048 and recalibrate
the threshold with palm/training/eval_eer.py.
"""

from __future__ import annotations

import argparse
import os

import torch
import torch.nn as nn

from .ccnet_model import ccnet

_SIZE = 128
_EMBED_DIM = 2048


class _FeatureExtractor(nn.Module):
    """Wrap ccnet so the ONNX returns ONLY the normalised embedding."""

    def __init__(self, net: ccnet) -> None:
        super().__init__()
        self.net = net

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net.getFeatureCode(x)


def _strip_module_prefix(state: dict) -> dict:
    """Checkpoints trained with DataParallel carry a 'module.' prefix; drop it."""
    if any(k.startswith("module.") for k in state):
        return {k[len("module."):]: v for k, v in state.items()}
    return state


def export(checkpoint: str, out: str, num_classes: int, weight: float) -> str:
    net = ccnet(num_classes=num_classes, weight=weight)
    raw = torch.load(checkpoint, map_location="cpu")
    state = raw.get("state_dict", raw) if isinstance(raw, dict) else raw
    net.load_state_dict(_strip_module_prefix(state), strict=False)
    net.eval()

    model = _FeatureExtractor(net).eval()
    dummy = torch.randn(1, 1, _SIZE, _SIZE)
    with torch.no_grad():
        out_dim = model(dummy).shape[-1]
    if out_dim != _EMBED_DIM:
        print(f"[warn] feature dim is {out_dim}, expected {_EMBED_DIM}; "
              f"set PALM_EMBED_DIM={out_dim}.")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    torch.onnx.export(
        model, dummy, out,
        input_names=["input"], output_names=["embedding"],
        dynamic_axes={"input": {0: "n"}, "embedding": {0: "n"}},
        opset_version=17,
    )
    print(f"[ok] exported {out}  (input 1x1x{_SIZE}x{_SIZE} [0,1], output dim {out_dim})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Export CCNet checkpoint to ONNX.")
    ap.add_argument("--checkpoint", required=True, help="path to the .pth checkpoint")
    ap.add_argument("--out", default=os.path.join("palm", "models", "palm_ccnet.onnx"))
    ap.add_argument("--num-classes", type=int, default=600, help="Tongji 600, IITD 460, ...")
    ap.add_argument("--weight", type=float, default=0.8, help="channel-competition weight")
    args = ap.parse_args()
    export(args.checkpoint, args.out, args.num_classes, args.weight)


if __name__ == "__main__":
    main()
