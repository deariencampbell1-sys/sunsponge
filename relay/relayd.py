"""relay/relayd.py — SunSponge hub-link relay daemon.

The relay is a single-file Python service.  Run with::

    python3 -m relay.relayd                # foreground (development)
    python3 relay/relayd.py                # foreground
    systemctl start sunsponge-relay       # production (systemd)

Configuration is read from environment variables (so the systemd
unit's ``EnvironmentFile=`` works out of the box):

  PORT                TCP port to listen on (default 3478)
  BIND                bind address (default 0.0.0.0)
  REGISTRY_TTL        seconds a hub registration is valid (default 3600)
  MAX_CONNECTIONS     hard cap on simultaneous connections (default 4096)
  MAX_CHANNELS        hard cap on simultaneous channels (default 16384)
  MAX_DATA_BYTES      max payload size of a data frame (default 65536)
  LOG_LEVEL           one of debug|info|warn|error (default info)
  RELAY_KEY_PATH      path to a long-term Ed25519 key (default
                      ``/etc/sunsponge-relay/relay-ed25519.key``);
                      if missing, a fresh one is generated and saved
                      with mode 0600

The relay's Ed25519 key is used to sign *its own* receipts
(``register_ok``, ``incoming_channel``, ``peer_hub_id_signature``).
It is **NOT** part of the mTLS trust chain — the hub pins its peer's
self-signed cert, not the relay's.  A compromise of the relay key
therefore can NOT enable message forgery; it can only forge relay
control frames (which the hub must verify against a hard-coded
relay pubkey for the audit log; or accept with a visible banner).

For the deployment described in
``docs/plans/HUB-RELAY-DESIGN.md``, we use a *transient* relay key
by default; the deploy script can pin a long-lived key into
``/etc/sunsponge-relay/relay-ed25519.key`` if non-repudiation
across restarts is required (it usually isn't — relay receipt
forgery has no impact on confidentiality or authenticity of the
hub-to-hub traffic).
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import signal
import socket
import sys
import threading
import traceback

# Make the package importable when run as `python3 relay/relayd.py`
if __package__ in (None, ""):
    sys.path.insert(
        0,
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    __package__ = "relay"

from . import metrics
from .registry import ChannelRegistry, Registry
from .session import HubSession


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_PORT = 3478
DEFAULT_BIND = "0.0.0.0"
DEFAULT_REGISTRY_TTL = 3600.0
DEFAULT_MAX_CONNECTIONS = 4096
DEFAULT_MAX_CHANNELS = 16384
DEFAULT_MAX_DATA_BYTES = 64 * 1024

# Reasonable floor for the registry TTL — 30 s.  Shorter values would
# create a stampede of re-registrations on any hub that pauses for
# more than a second to send a message.
MIN_REGISTRY_TTL = 30.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        metrics.warn("bad_env_int", name=name, value=raw)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        metrics.warn("bad_env_float", name=name, value=raw)
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else raw


# ---------------------------------------------------------------------------
# Long-term relay signing key
# ---------------------------------------------------------------------------

def _load_or_create_relay_key(path: str):
    """Return an Ed25519 private key, persisting to ``path`` if needed.

    The key is PEM-encoded (PKCS8, unencrypted).  File mode is
    forced to 0600 on write.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        load_pem_private_key,
    )
    if os.path.exists(path):
        with open(path, "rb") as fh:
            pem = fh.read()
        try:
            return load_pem_private_key(pem, password=None)
        except Exception as exc:
            metrics.error("relay_key_load_failed",
                          path=path, error=str(exc))
            sys.exit(2)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(pem)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    metrics.info("relay_key_generated", path=path)
    return key


def _relay_pubkey_fingerprint(key) -> str:
    """Return the short fingerprint of the relay's signing key (for log audit)."""
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )
    raw = key.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Sweeper — evicts expired bindings
# ---------------------------------------------------------------------------

def _sweeper_loop(registry: Registry, stop: threading.Event) -> None:
    while not stop.wait(timeout=5.0):
        try:
            evicted = registry.sweep()
            for hub_id in evicted:
                metrics.info("hub_lease_expired", hub_id=hub_id)
        except Exception as exc:
            metrics.error("sweeper_error", error=str(exc))


# ---------------------------------------------------------------------------
# Listener / accept loop
# ---------------------------------------------------------------------------

class RelayDaemon:
    """The relay itself.  One daemon per process."""

    def __init__(self) -> None:
        self.bind = _env_str("BIND", DEFAULT_BIND)
        self.port = _env_int("PORT", DEFAULT_PORT)
        self.registry_ttl = max(MIN_REGISTRY_TTL, _env_float("REGISTRY_TTL",
                                                             DEFAULT_REGISTRY_TTL))
        self.max_connections = _env_int("MAX_CONNECTIONS",
                                        DEFAULT_MAX_CONNECTIONS)
        self.max_channels = _env_int("MAX_CHANNELS", DEFAULT_MAX_CHANNELS)
        self.max_data_bytes = _env_int("MAX_DATA_BYTES", DEFAULT_MAX_DATA_BYTES)
        self.key_path = _env_str(
            "RELAY_KEY_PATH",
            "/etc/sunsponge-relay/relay-ed25519.key",
        )
        # In dev / tests, the default key path is unwritable.  Use a
        # local file under the cwd if /etc/sunsponge-relay doesn't exist.
        if not os.path.isdir(os.path.dirname(self.key_path)):
            self.key_path = os.path.join(
                os.getcwd(), ".relay-ed25519.key",
            )

        self._key = _load_or_create_relay_key(self.key_path)
        self._registry = Registry(self.registry_ttl)
        self._channels = ChannelRegistry()
        self._active_sessions = 0
        self._active_lock = threading.Lock()
        self._stop = threading.Event()
        self._listener: socket.socket | None = None

    # ----------------------------------------------------------------- helpers

    def _on_session_disconnect(self, _session) -> None:
        with self._active_lock:
            if self._active_sessions > 0:
                self._active_sessions -= 1

    def _accept_loop(self) -> None:
        assert self._listener is not None
        self._listener.listen(128)
        while not self._stop.is_set():
            try:
                sock, peer = self._listener.accept()
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                if self._stop.is_set():
                    return
                metrics.error("accept_error", error=str(exc))
                continue
            with self._active_lock:
                if self._active_sessions >= self.max_connections:
                    metrics.warn("connection_cap_hit",
                                 active=self._active_sessions,
                                 cap=self.max_connections)
                    with contextlib.suppress(OSError):
                        sock.close()
                    continue
                self._active_sessions += 1
            session = HubSession(
                sock=sock,
                peer=peer,
                registry=self._registry,
                channel_registry=self._channels,
                relay_private_key=self._key,
                max_payload_bytes=self.max_data_bytes,
                on_disconnect=self._on_session_disconnect,
            )
            try:
                session.start()
            except Exception as exc:
                metrics.error("session_start_failed",
                              peer=f"{peer[0]}:{peer[1]}", error=str(exc))
                with self._active_lock:
                    self._active_sessions -= 1

    # ----------------------------------------------------------------- lifecycle

    def serve(self) -> None:
        """Bind, accept, and serve forever (until ``stop()``)."""
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Tighten keepalive: hubs behind aggressive NAT / power-save may
        # otherwise sit on a half-open TCP that doesn't surface as a
        # disconnect until the next read.
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:
            self._listener.bind((self.bind, self.port))
        except OSError as exc:
            metrics.error("bind_failed",
                          bind=self.bind, port=self.port, error=str(exc))
            sys.exit(2)
        # Run the TTL sweeper in the background.
        sweeper = threading.Thread(
            target=_sweeper_loop,
            args=(self._registry, self._stop),
            name="relay-sweeper",
            daemon=True,
        )
        sweeper.start()
        # Install signal handlers for graceful shutdown.
        def _sig(_signo, _frame):
            self.stop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _sig)
            except (ValueError, OSError):
                # Signal handlers can only be installed in the main thread.
                pass
        metrics.info(
            "relay_start",
            bind=self.bind,
            port=self.port,
            registry_ttl=self.registry_ttl,
            max_connections=self.max_connections,
            max_channels=self.max_channels,
            max_data_bytes=self.max_data_bytes,
            relay_pubkey_fingerprint=_relay_pubkey_fingerprint(self._key),
        )
        try:
            self._accept_loop()
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._listener is not None:
            with contextlib.suppress(OSError):
                self._listener.close()
        metrics.info("relay_stop",
                     active_sessions=self._active_sessions,
                     channels=self._channels.size(),
                     registry_size=self._registry.size())


# When imported by tests, don't auto-serve.  Only run if invoked as
# the script entry point.
def main() -> int:
    try:
        RelayDaemon().serve()
    except KeyboardInterrupt:
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        metrics.error("relay_crash", error=str(exc),
                      trace=traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
