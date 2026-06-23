# int8 — deferred (non-conflicting slot)

int8 quantization is **not** built into any APK yet, on purpose. Our measurement
(see `../../_quant_measure.py`) showed int8 is ~4× smaller and faster but shifts
embeddings ~2% and erodes the same-person margin slightly — and crucially we have
**not** validated the *impostor / false-accept* side, which needs a **multi-identity**
face set. fp32 ships forever; fp16 ships as a second flavor now.

This folder is outside `app/src/`, so it is **never compiled into a build** — a safe
place to park int8 work until it's validated.

## When ready to add int8 (after validating on diverse faces)
1. Generate a properly-calibrated int8 model from a representative, multi-identity
   calibration set (not the single-person debug images). Save it as
   `app/src/int8/assets/w600k_r50.onnx`.
2. Add an `int8` flavor in `app/build.gradle.kts` next to fp32/fp16:
   ```kotlin
   create("int8") {
       dimension = "model"
       applicationIdSuffix = ".int8"
       versionNameSuffix = "-int8"
       resValue("string", "app_name", "Face Verify i8")
   }
   ```
3. Re-measure impostor separation and, if needed, re-tune the accept threshold
   (`Config.MATCH_THRESHOLD`) for the int8 flavor before shipping.

Reference numbers (single-identity, pessimistic): fidelity ~0.98 vs fp32,
same-person mean 0.812→0.791. See the project docs for the full table.
