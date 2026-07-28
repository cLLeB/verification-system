"""Base45 codec (RFC 9285) - the alphanumeric-QR-friendly encoding used by
EU DCC-style credentials. QR alphanumeric mode packs these 45 characters at
5.5 bits each, so base45 payloads fit ~30% more data per QR than base64.

Self-contained (no dependency); strict decode - any character outside the
alphabet or an invalid final chunk raises ``Base45Error``.
"""

from __future__ import annotations

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
_REVERSE = {c: i for i, c in enumerate(_ALPHABET)}


class Base45Error(ValueError):
    """Input is not valid base45."""


def encode(data: bytes) -> str:
    out = []
    for i in range(0, len(data) - 1, 2):
        n = data[i] * 256 + data[i + 1]
        c, n = divmod(n, 45 * 45)
        b, a = divmod(n, 45)
        out.append(_ALPHABET[a] + _ALPHABET[b] + _ALPHABET[c])
    if len(data) % 2:
        b, a = divmod(data[-1], 45)
        out.append(_ALPHABET[a] + _ALPHABET[b])
    return "".join(out)


def decode(text: str) -> bytes:
    if len(text) % 3 == 1:
        raise Base45Error("invalid base45 length")
    try:
        vals = [_REVERSE[c] for c in text]
    except KeyError as exc:
        raise Base45Error(f"invalid base45 character: {exc.args[0]!r}") from exc
    out = bytearray()
    for i in range(0, len(vals) - 2, 3):
        n = vals[i] + vals[i + 1] * 45 + vals[i + 2] * 45 * 45
        if n > 0xFFFF:
            raise Base45Error("invalid base45 triple")
        out += n.to_bytes(2, "big")
    if len(vals) % 3 == 2:
        n = vals[-2] + vals[-1] * 45
        if n > 0xFF:
            raise Base45Error("invalid base45 pair")
        out.append(n)
    return bytes(out)
