"""Entry point for a Hugging Face **Gradio** Space.

Free accounts can no longer create Docker Spaces, but a Gradio Space just runs
``python <app_file>`` and publishes whatever binds port 7860 — so the real Flask
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

PORT = int(os.environ.get("PORT", "7860"))
THREADS = int(os.environ.get("WEB_THREADS", "8"))


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
