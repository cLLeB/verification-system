# Face Verification Backbone — production container (CPU, no GPU).
# Runs as a non-root user and listens on $PORT (default 7860) so it works on
# Hugging Face Spaces AND a normal server/compose deployment unchanged.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Native libs for OpenCV + onnxruntime. insightface pulls in full opencv-python
# (not just headless), which needs these GUI/X libs even on a server.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgomp1 libgl1 libsm6 libxext6 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user \
    && mkdir -p /data && chown user:user /data

USER user
ENV HOME=/home/user \
    PATH="/home/user/.local/bin:$PATH"
WORKDIR /home/user/app

COPY --chown=user requirements-service.txt .
RUN pip install --no-cache-dir --upgrade -r requirements-service.txt

# Pre-download the ArcFace model pack (into the user's cache) so the first request
# isn't slow and the container works offline after build.
RUN python -c "from insightface.app import FaceAnalysis; \
a=FaceAnalysis(name='buffalo_l', allowed_modules=['detection','landmark_3d_68','recognition']); \
a.prepare(ctx_id=-1, det_size=(480,480))"

# App code (fingerprint stack is intentionally NOT copied — see .dockerignore).
COPY --chown=user face ./face
COPY --chown=user face_service ./face_service
COPY --chown=user templates ./templates
COPY --chown=user static ./static
COPY --chown=user app.py manage_keys.py manage_admins.py bulk_enroll.py openapi.yaml ./

# Default state lives under the app dir (writable). In production (compose/Oracle)
# the FACE_* env vars redirect this onto the mounted /data volume instead.
ENV FACE_DB_PATH=/home/user/app/face_db

EXPOSE 7860

# Single worker keeps one model in memory; threads handle concurrency (the engine
# serializes inference internally). Put TLS in front (Caddy / HF provides it).
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT:-7860} --timeout 120 app:app"]
