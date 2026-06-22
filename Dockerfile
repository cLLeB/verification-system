# Face Verification Backbone — production container (CPU, no GPU).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Minimal native libs for opencv-headless + onnxruntime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt

# Pre-download the ArcFace model pack so the first request isn't slow.
RUN python -c "from insightface.app import FaceAnalysis; \
a=FaceAnalysis(name='buffalo_l', allowed_modules=['detection','landmark_3d_68','recognition']); \
a.prepare(ctx_id=-1, det_size=(480,480))"

# App code (fingerprint stack is intentionally NOT copied — see .dockerignore).
COPY face ./face
COPY face_service ./face_service
COPY templates ./templates
COPY static ./static
COPY app.py manage_keys.py manage_admins.py bulk_enroll.py ./

EXPOSE 5000

# Single worker keeps one model in memory; threads handle concurrency (the engine
# serializes inference internally). Terminate TLS at a reverse proxy in front.
#   docker run -p 5000:5000 -e FACE_DB_KEY=... -e FACE_SIGNING_SECRET=... <image>
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:5000", "--timeout", "120", "app:app"]
