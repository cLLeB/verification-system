# Face Verification Backbone - production container (CPU, no GPU).
# Runs as a non-root user and listens on $PORT (default 7860) so it works on
# Hugging Face Spaces AND a normal server/compose deployment unchanged.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Native libs for OpenCV + onnxruntime. insightface pulls in full opencv-python
# (not just headless), which needs these GUI/X libs even on a server.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgomp1 libgl1 libsm6 libxext6 libxrender1 libxcb1 \
        libegl1 libgles2 \

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
# isn't slow and the container works offline after build. Bake BOTH packs: the
# full buffalo_l (default) and the small buffalo_s (FACE_MODEL_NAME=buffalo_s on a
# memory-tight host). Baking buffalo_s matters on such a host - otherwise it is
# re-downloaded on every cold start, inside the same startup window where memory
# is already tight, which is exactly when it must not be.
ARG FACE_PREFETCH_MODELS="buffalo_l buffalo_s"
RUN python -c "import sys; from insightface.app import FaceAnalysis; \
[FaceAnalysis(name=n, allowed_modules=['detection','recognition']).prepare(ctx_id=-1, det_size=(320,320)) \
for n in '${FACE_PREFETCH_MODELS}'.split()]"

# App code (fingerprint stack is intentionally NOT copied - see .dockerignore).
COPY --chown=user biometric ./biometric
COPY --chown=user face ./face
COPY --chown=user palm ./palm
COPY --chown=user face_service ./face_service
COPY --chown=user templates ./templates
COPY --chown=user static ./static
COPY --chown=user app.py manage_keys.py manage_admins.py bulk_enroll.py manage_templates.py openapi.yaml ./

# Bake the palm models into the image from Hugging Face (kept out of git: HF Spaces
# reject committed binaries). CCNet fp16 (~129 MB, ~lossless) + the MediaPipe hand
# detector, so the Space has everything and never re-downloads on restart.
RUN python -c "from huggingface_hub import hf_hub_download as d; import shutil, os; \
os.makedirs('palm/models', exist_ok=True); \
shutil.copyfile(d('kyereboatengcaleb/palm-ccnet-onnx','palm_ccnet_fp16.onnx'), 'palm/models/palm_ccnet.onnx'); \
shutil.copyfile(d('kyereboatengcaleb/palm-ccnet-onnx','hand_landmarker.task'), 'palm/models/hand_landmarker.task')"

# All runtime state lives under /data (writable, owned by 'user'). On compose/Oracle
# this is a mounted volume; on Hugging Face it's synced to a Dataset (see persistence.py).
# PILOT (temporary): walk-up enrolment with no operator password, and every real
# capture recorded under /data/fielddata for accuracy tuning. Set FACE_OPEN_ENROLL=0
# / FACE_FIELD_DATA=0 (Space secret or compose env) to switch either off.
# The rate limit is per IP; a room of testers behind one NAT shares it, so it's
# raised from the 120/min default.
ENV FACE_OPEN_ENROLL=1 \
    FACE_FIELD_DATA=1 \
    FACE_RATE_LIMIT=600

ENV FACE_DB_PATH=/data/face_db \
    FACE_KEYS_FILE=/data/apikeys.json \
    FACE_INVITES_FILE=/data/invites.json \
    FACE_ADMINS_FILE=/data/admins.json \
    FACE_TENANTS_FILE=/data/tenants.json \
    FACE_USAGE_FILE=/data/usage.json \
    FACE_AUDIT_DIR=/data/audit_logs \
    FACE_PERSIST_DIR=/data \
    BIO_ISSUER_KEY_DIR=/data/issuer \
    BIO_CREDENTIALS_DIR=/data/credentials

EXPOSE 7860

# Single worker keeps one model in memory; threads handle concurrency (the engine
# serializes inference internally). Put TLS in front (Caddy / HF provides it).
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT:-7860} --timeout 120 app:app"]
