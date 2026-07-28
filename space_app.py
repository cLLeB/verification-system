"""Entry point for a Hugging Face **Gradio** Space.

Free accounts can no longer create Docker Spaces, but a Gradio Space just runs
``python <app_file>`` and publishes whatever binds port 7860 - so the real Flask
app runs there unchanged. The differences a Gradio Space forces, all handled here:

  * **No image build step.** The Dockerfile bakes the ArcFace pack, the CCNet palm
    encoder and the MediaPipe hand model into the image; here they must arrive at
    boot. ``bootstrap()`` fetches them before the first request, otherwise palm
    would quietly report "unavailable" and every hand would be rejected.
  * **No gunicorn command.** We serve with waitress from Python instead: one
    process, several threads, matching the container's ``-w 1 --threads 8`` (the
    engine serialises inference internally, and one process = one copy of ~450 MB
    of models).
  * **Ephemeral disk.** State still lives under FACE_PERSIST_DIR and syncs to the
    private Dataset, exactly as on the container.

Local check (same path the Space takes):

    FACE_PERSIST_DIR=./_spacedata .\\venv\\Scripts\\python space_app.py
"""

from __future__ import annotations

import os
import sys
import time

# --- ZeroGPU registration ---------------------------------------------------
# Must sit at MODULE scope in the app_file: the runtime looks for a @spaces.GPU
# function while the Space starts, and refuses to run one that declares none.
# Importing `spaces` also has to precede any torch/CUDA import.
try:
    import spaces
except Exception:                       # local dev / container: no ZeroGPU
    class _NoGPU:
        @staticmethod
        def GPU(*dargs, **dkwargs):
            def wrap(fn):
                return fn
            return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap
    spaces = _NoGPU()


@spaces.GPU(duration=120)
def gpu_batch_embed(images_b64, modality: str = "palm") -> dict:
    """Embed a whole labelled set inside one accelerator allocation.

    The GPU-shaped job in this app: producing the (N, D) matrix
    ``palm/training/eval_eer.py`` needs for threshold calibration and encoder
    evaluation. Live verification stays on CPU on purpose - it is one small image
    per request, and a per-call GPU allocation would make it slower."""
    from space_gpu import batch_embed
    return batch_embed(images_b64, modality)


PORT = int(os.environ.get("PORT", "7860"))
THREADS = int(os.environ.get("WEB_THREADS", "8"))


def writable_state_dir() -> str:
    """A directory this Space may actually write to.

    The container images use /data (the Dockerfile creates and chowns it), but a
    Gradio Space has no such path and cannot create it - the app dies on the first
    store access with EACCES. Honour FACE_PERSIST_DIR when it works, else fall back
    to somewhere under $HOME, so a fresh Space boots without hand-set paths."""
    for candidate in (os.environ.get("FACE_PERSIST_DIR"),
                      os.path.join(os.path.expanduser("~"), "data"),
                      "/tmp/facedata"):
        if not candidate:
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".writetest")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            return candidate
        except OSError:
            continue
    raise SystemExit("no writable state directory")


def apply_state_paths(root: str) -> None:
    """Point every state file at ``root`` unless it was set explicitly elsewhere."""
    defaults = {
        "FACE_PERSIST_DIR": root,
        "FACE_DB_PATH": os.path.join(root, "face_db"),
        "FACE_KEYS_FILE": os.path.join(root, "apikeys.json"),
        "FACE_ADMINS_FILE": os.path.join(root, "admins.json"),
        "FACE_TENANTS_FILE": os.path.join(root, "tenants.json"),
        "FACE_INVITES_FILE": os.path.join(root, "invites.json"),
        "FACE_USAGE_FILE": os.path.join(root, "usage.json"),
        "FACE_AUDIT_DIR": os.path.join(root, "audit_logs"),
        "BIO_ISSUER_KEY_DIR": os.path.join(root, "issuer"),
        "BIO_CREDENTIALS_DIR": os.path.join(root, "credentials"),
    }
    for key, value in defaults.items():
        current = os.environ.get(key, "")
        # Replace anything pointing into an unwritable /data as well as the unset case.
        if not current or current == "/data" or current.startswith("/data/"):
            os.environ[key] = value
    print(f"[space] state root: {root}", flush=True)


def bootstrap() -> None:
    """Fetch the model files the container would have baked in. Fails soft: a
    modality whose model is missing reports unavailable rather than crashing, so a
    partial download still leaves a working service."""
    t0 = time.time()
    # Palm: CCNet encoder + the MediaPipe hand landmarker (the latter gates the
    # whole palm modality, so a miss here is the difference between palm working
    # and every hand being refused).
    try:
        from palm.config import CONFIG as PALM_CONFIG
        from palm import engine as palm_engine, roi as palm_roi
        print(f"[space] palm encoder: {palm_engine.ensure_model(PALM_CONFIG)}", flush=True)
        print(f"[space] palm hand model: {palm_roi.ensure_hand_model(PALM_CONFIG)}", flush=True)
    except Exception as exc:
        print(f"[space] palm bootstrap failed: {exc}", flush=True)
    # Face: insightface pulls the buffalo_l pack into its own cache on first use.
    # Warming it here moves that cost off the first person's verification.
    try:
        from insightface.app import FaceAnalysis
        fa = FaceAnalysis(name="buffalo_l",
                          allowed_modules=["detection", "landmark_3d_68", "recognition"])
        fa.prepare(ctx_id=-1, det_size=(480, 480))
        print("[space] face pack ready", flush=True)
    except Exception as exc:
        print(f"[space] face bootstrap failed: {exc}", flush=True)
    print(f"[space] bootstrap took {time.time() - t0:.0f}s", flush=True)


def main() -> int:
    import space_gpu
    print(f"[space] GPU batch embedding registered "
          f"(zerogpu={space_gpu.ON_ZERO_GPU}, providers={space_gpu.providers()})",
          flush=True)
    apply_state_paths(writable_state_dir())   # BEFORE anything reads the env
    bootstrap()
    from app import app as flask_app        # imports AFTER the models are on disk
    from waitress import serve
    print(f"[space] serving on 0.0.0.0:{PORT} ({THREADS} threads)", flush=True)
    serve(flask_app, host="0.0.0.0", port=PORT, threads=THREADS,
          # a capture burst is a big JSON body; keep the default cap from truncating it
          max_request_body_size=64 * 1024 * 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
