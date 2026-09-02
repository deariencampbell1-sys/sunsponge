"""relay/metrics.py — structured JSON event logging.

The relay writes one JSON object per line to stdout (which systemd
or supervisord captures into journald).  Fields:

  ts         ISO-8601 UTC timestamp with microseconds
  level      "info" | "warn" | "error" | "debug"
  event      short snake_case event name
  ...        event-specific fields

The event log is the operator's window into relay behaviour.  It is
NOT persisted anywhere except the journal; a hub's identity or hub-to-
hub message contents NEVER appear in the log (the relay doesn't know
either).  What appears:

  * ``hub_connect`` / ``hub_disconnect`` — peer TCP endpoints, no
    hub_id until the hub registers.
  * ``hub_register`` — hub_id (received from the hub), pubkey
    fingerprint (sha256 of raw pubkey, first 16 hex chars), ttl.
  * ``hub_register_error`` — failure reason (bad sig, wrong hub_id,
    parse error).
  * ``channel_open`` — channel_id, hub_a, hub_b, mode
    ("immediate" | "parked-matched").
  * ``channel_close`` — channel_id, reason.
  * ``relay_start`` / ``relay_stop`` — server bind addr, pid, version.

This is enough for ops debugging and a 6-month audit window in
journald by default.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import threading
from typing import Any

_LOCK = threading.Lock()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def _emit(level: str, event: str, **fields: Any) -> None:
    obj = {
        "ts": _now(),
        "level": level,
        "event": event,
        "pid": os.getpid(),
    }
    obj.update(fields)
    line = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    with _LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def info(event: str, **fields: Any) -> None:
    _emit("info", event, **fields)


def warn(event: str, **fields: Any) -> None:
    _emit("warn", event, **fields)


def error(event: str, **fields: Any) -> None:
    _emit("error", event, **fields)


def debug(event: str, **fields: Any) -> None:
    _emit("debug", event, **fields)


def pubkey_fingerprint(public_key_pem: str) -> str:
    """Stable short fingerprint of a public key PEM (first 16 hex chars
    of SHA-256 of the raw public key bytes).  Mirrors the
    ``fingerprint`` field on ``rhobear.HubIdentity``."""
    import hashlib
    try:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
            load_pem_public_key,
        )
        pub = load_pem_public_key(public_key_pem.encode("ascii"))
        raw = pub.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    except Exception:
        # Fallback: hash the PEM string itself; still useful for log grep.
        raw = public_key_pem.encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16]
