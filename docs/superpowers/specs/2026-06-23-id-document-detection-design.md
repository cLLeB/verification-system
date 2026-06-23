# Smart ID-Document Detection on Enrollment

**Date:** 2026-06-23
**Status:** Implemented (Phase 1) — 2026-06-23. Android remains committed Phase 2.
**Scope:** Phase 1 (Python `face/` engine + `/v1` service + web UI). Phase 2 (offline Android/Kotlin port) is a committed follow-on with its own spec.

---

## 1. Problem

Enrollment accepts faces in two forms: **live camera scanning** and **image uploads**. Occasionally — not usually, but it happens — a person submits an **ID document** (national card, passport) instead of a live face.

Today an ID document silently collides with the normal enrollment gates:

- The single-face gate rejects it (cards carry a main portrait **+** a faint "ghost" portrait → two faces detected). We observed exactly this: `Image.jpeg` returned *"More than one face in view."*
- If gates were relaxed, a printed/degraded photo would silently pollute a live-quality enrollment.

We want the system to **recognize when an input is an ID document and branch to a dedicated handling path**, without weakening or altering the normal live-face path. Neither path should compromise the other.

### What we learned from the validation exercise (2026-06-23)

Matching three images of the enrolled `user1` (3 anchors + 2 adaptive, threshold cosine ≥ 0.40):

| Source | Best cosine | Verdict |
|--------|-------------|---------|
| Recent passport photo (`image.png`) | 0.668 | MATCH |
| Year-old photo (`me.jpeg`) | 0.663 | MATCH |
| Official ID card (`Image.jpeg`), largest face | 0.534 | MATCH |

Conclusions that drive this design:
- The matching engine **already works** across live photos, year-old photos, and printed ID cards. No change to matching is needed.
- **We detect the document, not the face.** A tightly-cropped passport headshot is indistinguishable from a selfie — and that case is harmless (it is a real face of the right person), so it correctly stays on the normal path.
- ID cards expose strong, cheap signals: a ghost portrait (two faces), a small face within a larger card, card edges, printed text / MRZ, and (on the live path) failed liveness.

---

## 2. Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Behavior when an ID is detected at enrollment | **Auto-branch** to a dedicated ID handling path (no user confirmation) |
| 2 | Where detection applies | **Enrollment only.** Verify/identify keep liveness — an ID at verification stays a rejected spoof |
| 3 | Detection mechanism | **Heuristic signal-stack**, no new model (works offline, explainable, tunable, no training data) |
| 4 | Provenance | **FT2 storage format**: 1-byte source tag per embedding (`0=live`, `1=id`); FT1 still readable |
| 5 | Scope/phasing | **Phase 1 server/web now**; Android Kotlin port is committed Phase 2 ("everywhere" in the end) |

---

## 3. Architecture

### 3.1 New module — `face/id_document.py`

A single, focused, pure module. It performs **no** model loading of its own — it consumes the faces already produced by one detector run, so enrollment never runs detection twice.

**Public interface:**

```python
@dataclass(frozen=True)
class IdSignals:
    ghost_portrait: float     # 0..1
    small_face_ratio: float   # 0..1
    card_rectangle: float     # 0..1
    text_mrz_density: float   # 0..1
    not_live: float           # 0..1  (live-scan path only; 0 when unavailable)

@dataclass(frozen=True)
class IdAssessment:
    is_id: bool
    confidence: float         # 0..1 weighted combination
    signals: IdSignals
    primary_face_index: int   # index of the largest/main face to use for embedding

def assess(image: np.ndarray,
           faces: list,                 # results of a single detector run
           cfg: FaceConfig = CONFIG,
           live_score: Optional[float] = None) -> IdAssessment: ...
```

**Signals (each independent, 0..1):**

| Signal | Computation | Fires on IDs because |
|--------|-------------|----------------------|
| `ghost_portrait` | A 2nd face whose box area ≪ primary's **and** cosine similarity to primary ≥ a "same identity" floor | Cards carry a faint duplicate portrait of the same person |
| `small_face_ratio` | `1 - clamp(primary_face_area / image_area / expected_selfie_ratio)` | A face embedded in a larger card occupies a small fraction of the frame |
| `card_rectangle` | Largest 4-corner convex quadrilateral contour (OpenCV `findContours` + `approxPolyDP`) covering a large share of the frame | The physical card outline |
| `text_mrz_density` | Density of small text-like contours; bonus for a horizontal monospaced band near an edge (MRZ `<<<`) | Documents are dense with print; passports have an MRZ |
| `not_live` | `1 - live_score` from passive anti-spoof, **only** when a live score is available | A flat, printed card is not a live face |

**Combination:** weighted sum normalized to 0..1, compared against `cfg.id_confidence_threshold` (default `0.5`, conservative). Weights are constants in the module (e.g. ghost 0.30, card_rectangle 0.25, text_mrz 0.20, small_face 0.15, not_live 0.10), tuned against fixtures. No single weak signal can cross the threshold alone; an ID typically lights up several.

**Ghost vs. two real people (critical disambiguation):**
- Secondary face **much smaller** than primary **and** **same identity** (high cosine to primary) → ghost portrait → contributes to `is_id`.
- Two **comparably sized**, **different** identities → genuine multi-person → `assess` does **not** classify as ID; the existing `multiple_faces` rejection stands.

### 3.2 Config additions — `face/config.py`

```python
id_detection_enabled: bool = True          # env FACE_ID_DETECTION
id_confidence_threshold: float = 0.50       # env FACE_ID_CONFIDENCE
id_min_face_px: int = 40                     # min usable face on a card
id_match_threshold: Optional[float] = None   # optional relaxed accept for id-sourced compares; None = reuse match_threshold
```

All overridable by environment variable, following the existing `_apply_env` pattern.

---

## 4. Data flow — `enroll()`

`face/api.py::enroll()` and the `/v1` enroll endpoint gain an optional `source` parameter:
`"auto"` (default) | `"live"` | `"id"`.

```
enroll(user_id, image, source="auto"):
  1. faces = engine.detect_all(image)          # ONE detector run, returns every face
  2. route:
       source == "live"           -> NORMAL path
       source == "id"             -> ID path
       source == "auto" and id_detection_enabled:
           a = id_document.assess(image, faces, cfg, live_score=...)
           a.is_id ? ID path : NORMAL path
       else                       -> NORMAL path
  3a. NORMAL path  (UNCHANGED): _engine.embed(image)  -> existing gates
  3b. ID path:
        - take faces[a.primary_face_index] (largest)
        - SKIP single-face, frontal-pose, liveness gates (a card is expected to be flat)
        - require face_px >= id_min_face_px else clear failure
        - KEEP duplicate-person guard + self-consistency check
        - store embedding with provenance = "id"
  4. return rich result:
        { success, code, user_id, samples, samples_target,
          source: "live" | "id_document",
          id_confidence: <float when assessed>,
          signals: {...} (id path only),
          det_score, quality }
```

**Engine support:** add `engine.detect_all(image, cfg) -> list[FaceDetection]` (the current `detect()` already finds all faces then enforces gates; factor out the "find all valid faces" step so both `detect()` and the ID path share it). This keeps `detect()`/`embed()` behavior byte-for-byte identical for the normal path.

`verify`, `identify`, `verify_live`: **no change.**

---

## 5. Storage — FT1 → FT2 (`face/storage.py`)

Add per-embedding provenance.

- **New magic** `FT2`. Header gains the layout for a trailing **source-byte array** (one `uint8` per embedding row, in anchors-then-adaptive order; `0=live`, `1=id`).
- `_pack` writes FT2 with source bytes; `_unpack` reads FT2.
- **Backward compatibility:** `_unpack` detects the `FT1` magic and reads old blobs, defaulting every source to `0=live`. Existing `_deserialize_legacy_json` path likewise defaults to `live`.
- `FaceTemplate` gains parallel source metadata:
  ```python
  anchor_sources: List[str]    # "live" | "id", aligned with anchors
  adaptive_sources: List[str]  # aligned with adaptive
  ```
  Construction stays immutable (new template returned on add), per project style.
- `add_embedding` / `add_many` accept a `source: str = "live"` argument and persist it.
- Adaptive learning unchanged in mechanism; an adaptive update inherits `source="live"` (adaptation happens from live verifies, never from an ID).

Migration is read-on-the-fly (no batch rewrite needed): FT1 rows are upgraded to FT2 the next time a template is written.

---

## 6. Service & UI

- **`/v1` enroll** (`face_service/v1.py`): accept optional `source` field (validated: auto/live/id, default auto). Echo `source`, `id_confidence`, and `signals` in the response envelope. Audit-log the provenance regardless (in addition to FT2). Sandbox short-circuit unchanged.
- **Web UI** (`static/app.js`, admin enroll): when the response `source == "id_document"`, show a distinct, friendly note — e.g. *"Enrolled from an ID document (confidence 0.87). For best accuracy, add a live capture too."* Uses the existing iris-violet feedback styling; no new design language.

---

## 7. Error handling

| Situation | Behavior |
|-----------|----------|
| ID detected, but largest face `< id_min_face_px` or low det_score | Fail clearly: *"Detected an ID, but the photo on it is too unclear — upload a clearer image or enroll a live face."* |
| `id_document.assess()` raises | **Fail open** → fall back to NORMAL path; log a warning. Detection can never break enrollment. |
| `source="id"` forced but no face found at all | Existing "no face detected" failure |
| Genuine two-person frame on `auto` | Not classified as ID → existing `multiple_faces` rejection |
| Duplicate person / inconsistent capture on ID path | Same guards as normal path (kept on purpose) |

---

## 8. Testing

Fixtures: `Image.jpeg` (real ID card), `image.png` & `me.jpeg` (real selfies). Add a synthetic/borrowed two-person image fixture for the disambiguation test.

**Unit — `tests/test_id_document.py`:**
- `assess()` → `is_id=True` for the ID card; `is_id=False` for both selfies.
- Each signal scores higher on the card than on the selfies.
- Ghost vs. two-people: two distinct identities of comparable size → `is_id=False`.

**Integration — `tests/test_enroll_id.py`:**
- `enroll(auto)` with the card → success, `source="id_document"`, FT2 row tagged `id`.
- `enroll(auto)` with a selfie → success, `source="live"` (normal path).
- `source="live"` forced on the card → hits the normal gate (proves override works).

**Storage — extend `tests/`:**
- FT2 round-trips embeddings + source tags.
- FT1 blob reads back with all sources defaulted to `live` (backward compatibility).

**Security/regression:**
- An ID at **verify** is still rejected (liveness intact).
- The full existing enroll/verify/identify suite stays green (normal path provably untouched).

---

## 9. Out of scope (this spec)

- **Android/Kotlin port** of the heuristics — committed **Phase 2**, separate spec. End state: ID detection runs everywhere, including fully offline on-device.
- Document **authenticity** verification (hologram/MRZ checksum/OCR field extraction) — a separate, non-face problem; not part of identity matching.
- Per-source threshold auto-tuning — `id_match_threshold` is wired but defaults to reusing `match_threshold` until more ID samples exist.

---

## 10. Component summary

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `face/id_document.py` | Decide is-this-an-ID from one detector run; expose signals | numpy, OpenCV, engine face objects |
| `face/engine.py` (`detect_all`) | Return all valid faces (shared find-all step) | insightface |
| `face/api.py` (`enroll`) | Route auto/live/id; ID branch extraction + guards | id_document, engine, storage, index |
| `face/storage.py` (FT2) | Persist embeddings + per-row provenance; read FT1 | crypto, sqlite |
| `face/config.py` | ID detection flags/thresholds | env |
| `face_service/v1.py` | `source` param, provenance in response + audit | api, audit |
| `static/app.js` | Friendly ID-sourced enrollment feedback | — |
