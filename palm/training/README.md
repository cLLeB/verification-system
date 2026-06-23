# Palm encoder — getting good weights (fine-tune + evaluate)

Per-user adaptation is automatic (confident verifies fold into a user's template).
Making the **encoder itself** better is a deliberate, offline step — it is *not*
automatic. This folder is the recipe.

## 0. Start with pretrained (no training)
Download a CCNet checkpoint, export to ONNX (see `../models/README_MODEL.md`), drop
it in `palm/models/palm_ccnet.onnx`. Good baseline (sub-1% EER on Tongji/PolyU/IITD).
Run the eval below to set your threshold, and ship.

## 1. Fine-tune on mobile data (best real-world accuracy)
1. **Get data** — request academic access to **MPD**, **NTU-CP-v1**, **XJTU-UP**, or
   collect your own *consented* palm captures (best for your devices/population).
2. **Preprocess identically to serving** — extract ROIs with the SAME pipeline
   (`palm.roi`: MediaPipe Hands → normalized 128×128). Train/serve mismatch is the
   #1 accuracy killer.
3. **Transfer-learn** — start from the pretrained CCNet and continue training with
   its competition / ArcFace-style loss over identities (GPU + PyTorch; a few hours).
4. **Validate** — embed a held-out split with the new model and run the eval below.
5. **Export** — to ONNX (`../models/README_MODEL.md`) + TFLite for Android.

## 2. Evaluate + calibrate the threshold
```bash
# embeddings.npy: (N, D) float32 from palm.engine ; labels.npy: (N,) identity ids
python -m palm.training.eval_eer embeddings.npy labels.npy
# -> EER=0.0123  threshold=0.31  (put threshold into palm/calibration.json)
```
`eval_eer.py` builds genuine vs. impostor cosine distributions, reports the **EER**
(lower is better), and the **threshold** at that operating point — copy it into
`palm/calibration.json` as `match_threshold` (exactly how face's `0.40` was chosen).

## 3. Retrain cadence (not automatic — on purpose)
For biometrics, prefer a **periodic, human-reviewed retrain** over silent continual
learning: accumulate consented data, retrain on a schedule, re-run the eval, and only
then promote the new weights. This avoids drift, poisoning, and consent problems.
