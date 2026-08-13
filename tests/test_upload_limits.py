"""Request-body cap and decompression-bomb guard on the image decode path.

This is an image API, so the natural abuse is size: a body far larger than a photo,
or - subtler, and not covered by any body cap - a small file that expands into an
enormous bitmap once decoded. A solid-colour 30000x30000 PNG is 45 bytes on the wire
and ~2.7 GB in memory. OpenCV's own ceiling (~1G pixels) only applies after it has
already allocated, which is far too late on a 4 GB container.
"""

from __future__ import annotations

import base64
import struct
import zlib

import cv2
import numpy as np

from face_service import v1


def _png(width: int, height: int) -> bytes:
    """A structurally valid PNG header declaring any size, with no pixel data."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _jpeg(width: int, height: int) -> bytes:
    return cv2.imencode(".jpg", np.full((height, width, 3), 128, np.uint8))[1].tobytes()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def test_declared_pixels_reads_png_header_without_decoding():
    assert v1._declared_pixels(_png(30000, 30000)) == 900_000_000
    assert v1._declared_pixels(_png(640, 480)) == 307_200


def test_declared_pixels_reads_jpeg_header():
    assert v1._declared_pixels(_jpeg(640, 480)) == 307_200
    assert v1._declared_pixels(_jpeg(200, 100)) == 20_000


def test_declared_pixels_unknown_format_is_none():
    """Unrecognised bytes must not raise - the body-size cap governs those."""
    assert v1._declared_pixels(b"not an image at all") is None
    assert v1._declared_pixels(b"") is None


def test_decode_refuses_a_png_decompression_bomb():
    bomb = _png(30000, 30000)
    assert len(bomb) < 100                      # tiny on the wire...
    assert v1._declared_pixels(bomb) > v1._MAX_PIXELS
    assert v1._decode(_b64(bomb)) is None       # ...and never decoded


def test_decode_still_accepts_a_real_capture():
    """The guard must not touch legitimate photos: 80 MP is ~10x a flagship phone."""
    img = v1._decode(_b64(_jpeg(640, 480)))
    assert img is not None
    assert img.shape[:2] == (480, 640)


def test_decode_handles_data_url_prefix_and_garbage():
    assert v1._decode("") is None
    assert v1._decode("data:image/jpeg;base64," + _b64(_jpeg(64, 64))) is not None


def test_app_sets_a_request_body_cap():
    """Without MAX_CONTENT_LENGTH the whole body is buffered before anything can
    reject it, and rate limits do not help - they bound how OFTEN a caller asks,
    not how big one ask is."""
    import app as application

    cap = application.app.config.get("MAX_CONTENT_LENGTH")
    assert cap, "no request-body cap configured"
    assert 1 * 1024 * 1024 < cap <= 128 * 1024 * 1024, f"implausible cap: {cap}"


def test_oversized_body_returns_the_normal_json_envelope():
    """A 413 must look like every other API error, not a Werkzeug HTML page.

    Sent to /v1/devices/pair because that is the one POST with no API key (the
    pairing code is the auth). On key-guarded endpoints auth correctly rejects
    first with a 401, so they cannot exercise the size path.
    """
    import app as application

    original = application.app.config["MAX_CONTENT_LENGTH"]
    application.app.config["MAX_CONTENT_LENGTH"] = 2048
    try:
        client = application.app.test_client()
        resp = client.post("/v1/devices/pair", data=b"x" * 8192,
                           content_type="application/json")
        assert resp.status_code == 413
        body = resp.get_json()
        assert body["success"] is False
        assert body["code"] == "payload_too_large"
        assert "MB" in body["message"]          # says the limit and the fix
    finally:
        application.app.config["MAX_CONTENT_LENGTH"] = original


def test_auth_is_checked_before_the_body_is_read():
    """An unauthenticated caller must not be able to make the server buffer a large
    body at all - the key check has to come first, so a bad key costs nothing."""
    import app as application

    original = application.app.config["MAX_CONTENT_LENGTH"]
    application.app.config["MAX_CONTENT_LENGTH"] = 2048
    try:
        client = application.app.test_client()
        resp = client.post("/v1/verify", data=b"x" * 8192,
                           content_type="application/json",
                           headers={"X-API-Key": "not-a-real-key"})
        assert resp.status_code == 401
    finally:
        application.app.config["MAX_CONTENT_LENGTH"] = original
