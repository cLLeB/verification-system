# `face/` — recognition core

Pure-Python, framework-agnostic face recognition. No web/HTTP concerns — the web
service (`face_service/`) and `app.py` use this as a library. Tuning here is mirrored
by the Android app (`android/.../Config.kt`).

## Modules
| File | Purpose |
|------|---------|
| `config.py` | All tunables: thresholds, sample caps, liveness angles, model name/providers. Read once; env-overridable via `load_config()`. |
| `engine.py` | InsightFace (ONNX) wrapper. `warm()` loads models; `detect()` → embedding + pose + box (quality-gated, pose-agnostic); `detect_pose()` → fast pose-only for liveness frames; `embed()` → frontal-gated + optional passive liveness. |
| `matcher.py` | `cosine()`, `best_score()`, `verify()` (1:1), `identify()` (1:N with runner-up margin). |
| `liveness_active.py` | Head-turn challenge: `new_challenge()`, `valid_token()`, `analyze(frames)` (real 3D turn + same-person across frames). |
| `liveness.py` | Passive single-shot anti-spoof (MiniFASNet ONNX). `available()`, `warm()`, `real_score()`. Off unless `FACE_LIVENESS=1`. |
| `storage.py` | Encrypted SQLite store. Compact binary template format (`FT1` magic), anchors + adaptive, monotonic `seq`, bulk `add_many`, tombstone delete, legacy-JSON reader. |
| `index.py` | Build-once cached match index. `_NumpyBackend` (exact, default) / `_HnswBackend` (ANN, opt-in). Encrypted on disk; `load_or_build()` replays only changed rows on restart. Per-tenant cache. |
| `crypto.py` | Fernet encryption-at-rest; key from `FACE_DB_KEY` (PBKDF2 + salt) or a generated `.key`. |
| `api.py` | High-level orchestration → plain dicts: `enroll`, `verify`, `identify`, `verify_live`, adaptive folding, and rich actionable feedback (`_fail`, `_quality`, `_HINTS`). |
| `errors.py` | `FaceError` (code + message) for detect/quality/liveness failures. |
| `models/` | Local anti-spoof ONNX models (ArcFace `buffalo_l` is cached under `~/.insightface`). |

## Key invariants
- Embeddings are 512-d, **L2-normalised**; matching is cosine (dot).
- **Anchors are permanent**; adaptive is a capped rolling set — the anti-drift design.
- Person score = **max** cosine over their stored embeddings.
- Templates are **encrypted at rest**; the index is encrypted too (`index/`).

See [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for data flow.
