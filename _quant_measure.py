"""Measure the COST of int8 and fp16 quantization for the ArcFace model — on our
actual model + our actual faces. fp32 stays the shipped model; this only writes
quantized copies to a temp dir and compares. Nothing in the project is changed.

Reports, per variant (fp32 / fp16 / int8):
  * model file size
  * embedding fidelity   = cosine(fp32_embedding, variant_embedding) per face  (1.0 = identical)
  * same-person scores   = pairwise cosine among our faces (all the same person)
                           -> shows how much margin above the 0.40 accept line erodes
  * inference speed       (avg ms per embed, CPU)
"""
from __future__ import annotations

import os, glob, time, tempfile
import numpy as np
import cv2
import onnxruntime as ort
from insightface.app import FaceAnalysis
from insightface.utils import face_align

FP32 = os.path.expanduser("~/.insightface/models/buffalo_l/w600k_r50.onnx")
THR = 0.40
TMP = tempfile.mkdtemp(prefix="quant_")


def preprocess(aligned_bgr):
    rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    blob = (rgb - 127.5) / 127.5
    return np.expand_dims(blob.transpose(2, 0, 1), 0)  # NCHW [1,3,112,112]


def aligned_faces():
    app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection"])
    app.prepare(ctx_id=-1, det_size=(480, 480))
    crops = []
    for p in sorted(glob.glob("debug/*.jpg")):
        img = cv2.imread(p)
        if img is None:
            continue
        faces = app.get(img)
        if not faces:
            continue
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        crops.append(face_align.norm_crop(img, landmark=f.kps, image_size=112))
    return crops


def embed_all(model_path, crops):
    so = ort.SessionOptions(); so.intra_op_num_threads = 2
    sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    embs, times = [], []
    for c in crops:
        x = preprocess(c)
        # fp16 model wants fp16 input
        if sess.get_inputs()[0].type == "tensor(float16)":
            x = x.astype(np.float16)
        t = time.perf_counter()
        out = sess.run(None, {iname: x})[0][0].astype(np.float32)
        times.append((time.perf_counter() - t) * 1000)
        embs.append(out / (np.linalg.norm(out) + 1e-10))
    return np.array(embs), float(np.mean(times))


def pairwise_same(embs):
    n = len(embs); vals = []
    for i in range(n):
        for j in range(i + 1, n):
            vals.append(float(embs[i] @ embs[j]))
    return np.array(vals)


def mb(path):
    return os.path.getsize(path) / 1e6


def main():
    print("Building aligned faces from debug/ ...", flush=True)
    crops = aligned_faces()
    print(f"  {len(crops)} faces (all the same person)\n", flush=True)

    variants = {"fp32": FP32}

    # fp16
    try:
        import onnx
        from onnxconverter_common import float16
        m = onnx.load(FP32)
        m16 = float16.convert_float_to_float16(m, keep_io_types=False)
        fp16_path = os.path.join(TMP, "w600k_r50_fp16.onnx")
        onnx.save(m16, fp16_path)
        variants["fp16"] = fp16_path
    except Exception as e:
        print("fp16 convert failed:", e)

    # int8 static (calibrated on our crops — NOTE: tiny, single-identity calib set,
    # so this is a pessimistic lower bound; a proper diverse calib set does better)
    try:
        from onnxruntime.quantization import quantize_static, CalibrationDataReader, QuantType, QuantFormat

        class Reader(CalibrationDataReader):
            def __init__(self, crops, iname):
                self.iname = iname
                self.data = iter([{iname: preprocess(c)} for c in crops])
            def get_next(self):
                return next(self.data, None)

        iname = ort.InferenceSession(FP32, providers=["CPUExecutionProvider"]).get_inputs()[0].name
        int8_path = os.path.join(TMP, "w600k_r50_int8.onnx")
        quantize_static(FP32, int8_path, Reader(crops, iname),
                        quant_format=QuantFormat.QDQ, per_channel=False,
                        weight_type=QuantType.QInt8, activation_type=QuantType.QInt8)
        variants["int8"] = int8_path
    except Exception as e:
        print("int8 quantize failed:", e)

    # measure
    fp32_embs = None
    rows = []
    for name, path in variants.items():
        embs, ms = embed_all(path, crops)
        if name == "fp32":
            fp32_embs = embs
        fidelity = np.array([float(embs[i] @ fp32_embs[i]) for i in range(len(embs))])
        same = pairwise_same(embs)
        rows.append((name, mb(path), fidelity.mean(), fidelity.min(),
                     same.mean(), same.min(), (same >= THR).mean() * 100, ms))

    print(f"{'variant':6} {'size MB':>8} {'fidelity(mean/min)':>20} {'same-person(mean/min)':>22} {'>=0.40':>7} {'ms':>6}")
    for n, size, fmean, fmin, smean, smin, pct, ms in rows:
        fid = "—" if n == "fp32" else f"{fmean:.4f}/{fmin:.4f}"
        print(f"{n:6} {size:8.1f} {fid:>20} {smean:.3f}/{smin:.3f}{'':>6} {pct:5.0f}% {ms:6.1f}")
    print("\nfidelity = cosine(variant, fp32) per face (1.0 = identical embedding)")
    print("same-person = pairwise cosine among our faces; all should stay well above 0.40")


if __name__ == "__main__":
    main()
