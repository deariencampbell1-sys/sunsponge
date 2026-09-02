"""relay/protocol.py — frame codec + Ed25519 helpers.

Wire format
-----------
Every frame on the wire is a length-prefixed JSON object:

    [4-byte big-endian length][UTF-8 JSON body]

The JSON body is canonicalised (sorted keys, no whitespace) for
signing — same convention as ``rhobear/hub_link.py`` so the hub can
use its existing ``_sign_bootstrap_payload`` helper.

Every frame that needs authentication carries a ``signature`` field
(base64 Ed25519) computed over the canonical body with the
``signature`` key removed.  The relay's own signing key is only used
for *its own* receipts (``register_ok``, ``incoming_channel``) — it is
NOT part of the mTLS trust chain that protects application traffic.

The hub_id derivation mirrors ``rhobear/hub_identity._compute_hub_id``
exactly so the relay and the hub agree byte-for-byte.
"""

from __future__ import annotations

import base64
import json
import struct
from typing import Any, Final

# Maximum size of a single frame the relay is willing to read or write.
# TLS 1.3 records can be up to 2^14 + 256 = 16640 bytes; we round up to
# 64 KiB to leave headroom for application-level envelopes that may be
# slightly larger than a single TLS record.  Anything bigger is rejected.
MAX_FRAME_BYTES: Final[int] = 64 * 1024

# Frame types the relay understands (server-side).  Frames with an
# unknown type are closed silently (fail-closed).
TYPE_REGISTER_HELLO: Final[str] = "register_hello"
TYPE_REGISTER_OK: Final[str] = "register_ok"
TYPE_REGISTER_ERROR: Final[str] = "register_error"
TYPE_OPEN_CHANNEL: Final[str] = "open_channel"
TYPE_OPEN_CHANNEL_OK: Final[str] = "open_channel_ok"
TYPE_OPEN_CHANNEL_PARKED: Final[str] = "open_channel_parked"
TYPE_OPEN_CHANNEL_TIMEOUT: Final[str] = "open_channel_timeout"
TYPE_OPEN_CHANNEL_ERROR: Final[str] = "open_channel_error"
TYPE_INCOMING_CHANNEL: Final[str] = "incoming_channel"
TYPE_DATA: Final[str] = "data"
TYPE_CLOSE_CHANNEL: Final[str] = "close_channel"
TYPE_PING: Final[str] = "ping"
TYPE_PONG: Final[str] = "pong"


# ---------------------------------------------------------------------------
# Frame codec
# ---------------------------------------------------------------------------

def canonical_json(obj: dict[str, Any]) -> bytes:
    """Deterministic JSON encoding for signing.

    Same convention as the rest of Rhobear: ``sort_keys=True``,
    ``separators=(",", ":")`` so the byte string is stable.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")


def encode_frame(obj: dict[str, Any]) -> bytes:
    """Encode a dict as ``[length][body]``."""
    body = canonical_json(obj)
    if len(body) > MAX_FRAME_BYTES:
        raise ValueError(f"frame body too large: {len(body)} > {MAX_FRAME_BYTES}")
    return struct.pack(">I", len(body)) + body


def decode_frame_header(header: bytes) -> int:
    """Decode a 4-byte big-endian length.  Raises ValueError on bad input."""
    if len(header) != 4:
        raise ValueError(f"frame header must be 4 bytes, got {len(header)}")
    n = struct.unpack(">I", header)[0]
    if n <= 0 or n > MAX_FRAME_BYTES:
        raise ValueError(f"frame body length out of range: {n}")
    return n


def decode_frame_body(body: bytes) -> dict[str, Any]:
    """Decode a frame body.  Raises ValueError on bad JSON or non-object."""
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"bad frame body: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("frame body must be a JSON object")
    return obj


# ---------------------------------------------------------------------------
# Ed25519 signing + verification
# ---------------------------------------------------------------------------

def sign_payload(payload: dict[str, Any], private_key) -> str:
    """Sign a payload dict (base64-encoded Ed25519 signature).

    The ``signature`` key is removed before signing so the same payload
    can be re-signed and the signature verified.
    """
    signable = {k: v for k, v in payload.items() if k != "signature"}
    sig = private_key.sign(canonical_json(signable))
    return base64.b64encode(sig).decode("ascii")


def verify_payload(payload: dict[str, Any], public_key) -> bool:
    """Verify a payload dict's ``signature`` field against a public key."""
    sig_b64 = payload.get("signature", "")
    if not sig_b64:
        return False
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except Exception:
        return False
    signable = {k: v for k, v in payload.items() if k != "signature"}
    try:
        public_key.verify(sig, canonical_json(signable))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# hub_id derivation
# ---------------------------------------------------------------------------

def hub_id_from_pem(public_key_pem: str) -> str:
    """Derive a hub_id from an Ed25519 SubjectPublicKeyInfo PEM.

    Mirrors ``rhobear.hub_identity._compute_hub_id`` byte-for-byte:

        hub_id = base64url(raw_pubkey).rstrip("=")[:32]

    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        load_pem_public_key,
    )
    pub = load_pem_public_key(public_key_pem.encode("ascii"))
    raw = pub.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")[:32]


def parse_ed25519_public_pem(pem: str):
    """Load an Ed25519 SubjectPublicKeyInfo PEM.  Returns ``None`` on error."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        pub = load_pem_public_key(pem.encode("ascii"))
    except Exception:
        return None
    # The cryptography library's public_key() is typed as Any; the only
    # operation we need is .verify(), which is the Ed25519 verify entry.
    return pub


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

def is_string_field(obj: dict[str, Any], key: str) -> bool:
    """True if ``obj[key]`` is a non-empty string."""
    v = obj.get(key)
    return isinstance(v, str) and bool(v)


# Sentinel: signatures of a payload (with the ``signature`` key removed)
# never contain the byte sequence 0x00 in the first 64 bytes; we use this
# nowhere right now but is documented for future audit scripts.
NULL_SIGNATURE: Final[bytes] = b"\x00" * 64
