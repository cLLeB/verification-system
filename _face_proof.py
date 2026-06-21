"""Prove the face engine works, with numbers, before building anything.

1) Discrimination: pairwise cosine similarity between DIFFERENT people in a
   group photo must be LOW.
2) Robustness: the SAME face under realistic capture variation (brightness,
   small rotation, blur, JPEG recompression) must stay HIGH similarity to the
   original — i.e. the same person re-captured still matches.
"""
from __future__ import annotations
import cv2
import numpy as np
import insightface
from insightface.app import FaceAnalysis

app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(640, 640))   # ctx_id=-1 => CPU

img = insightface.data.get_image("t1")          # bundled group photo
faces = app.get(img)
faces = sorted(faces, key=lambda f: f.bbox[0])
print(f"detected faces: {len(faces)}")

def cos(a, b):
    return float(np.dot(a, b))                  # normed_embedding is L2-normalised

print("\n=== 1) DIFFERENT people (want LOW, < ~0.3) ===")
sims = []
for i in range(len(faces)):
    for j in range(i + 1, len(faces)):
        s = cos(faces[i].normed_embedding, faces[j].normed_embedding)
        sims.append(s)
print(f"  {len(sims)} cross-identity pairs: min={min(sims):.3f} max={max(sims):.3f} mean={float(np.mean(sims)):.3f}")

print("\n=== 2) SAME face under realistic variation (want HIGH, > ~0.5) ===")
# crop the largest face with margin and re-detect each variant
f0 = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))
x1, y1, x2, y2 = [int(v) for v in f0.bbox]
mx, my = int((x2-x1)*0.4), int((y2-y1)*0.4)
h, w = img.shape[:2]
crop = img[max(0,y1-my):min(h,y2+my), max(0,x1-mx):min(w,x2+mx)]
base = app.get(crop)
if not base:
    raise SystemExit("could not re-detect base crop")
base_emb = max(base, key=lambda f:(f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1])).normed_embedding

def variant(im, name):
    out = im.copy()
    if name == "bright":   out = cv2.convertScaleAbs(out, alpha=1.0, beta=40)
    elif name == "dark":   out = cv2.convertScaleAbs(out, alpha=0.7, beta=-10)
    elif name == "rotate":
        M = cv2.getRotationMatrix2D((out.shape[1]/2, out.shape[0]/2), 8, 1.0)
        out = cv2.warpAffine(out, M, (out.shape[1], out.shape[0]))
    elif name == "blur":   out = cv2.GaussianBlur(out, (5, 5), 0)
    elif name == "jpeg":
        ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 60]); out = cv2.imdecode(enc, 1)
    elif name == "scale":
        out = cv2.resize(out, None, fx=0.6, fy=0.6); out = cv2.resize(out, (im.shape[1], im.shape[0]))
    return out

for name in ["bright", "dark", "rotate", "blur", "jpeg", "scale"]:
    v = variant(crop, name)
    got = app.get(v)
    if not got:
        print(f"  {name:7}: no face detected"); continue
    e = max(got, key=lambda f:(f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1])).normed_embedding
    print(f"  {name:7}: self-similarity = {cos(base_emb, e):.3f}")
print("\nembedding dim:", base_emb.shape[0])
