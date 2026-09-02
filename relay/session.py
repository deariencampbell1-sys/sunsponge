"""relay/session.py — per-connection state machine.

A ``HubSession`` owns one TCP connection from a hub.  It runs two
threads:

  * **reader** — blocks on the socket, decodes frames, dispatches
    to the appropriate handler (register, open_channel, data,
    close_channel, ping).
  * **writer** — blocks on a condition variable, drains the
    session's outbound queue (frames produced by channels that
    forward bytes from the *peer* session, plus control frames
    like ``incoming_channel`` / ``register_ok``).

Why a dedicated writer thread (instead of ``socket.makefile()``)?
Backpressure.  The writer must be able to *block* on a slow TCP
peer (waiting for the kernel send buffer to drain) without stalling
the reader's processing of other frames on the same session.  A
single-threaded design with non-blocking sends can drop frames
silently; we want clean close-with-reason instead.

The relay's session does *not* speak TLS.  The hub terminates TLS
on its end; the bytes the relay sees on the wire are already
encrypted (or they're relay-protocol frames — the relay reads the
first 4 bytes of every frame and treats the body as JSON, but
``data`` frames are opaque to the relay and are forwarded as-is).

Concurrency model:

    HubSession (one per connection)
        |
        +-- reader thread: socket.recv -> frame decode -> handle_*
        |
        +-- writer thread: condition.wait() -> queue -> socket.sendall
        |
        +-- channels: dict[channel_id, RelayChannel] the session is part of

The session is registered in the :class:`relay.registry.Registry`
after a successful registration handshake.  Until then, the session
is "unbound" — frames other than ``register_hello`` are rejected.
"""

from __future__ import annotations

import collections
import socket
import threading
import time
import uuid
from typing import Any, Final

from . import metrics
from .channel import (
    MAX_DATA_PAYLOAD,
    RelayChannel,
    encode_close_frame,
    encode_data_frame,
)
from .protocol import (
    MAX_FRAME_BYTES,
    TYPE_CLOSE_CHANNEL,
    TYPE_DATA,
    TYPE_INCOMING_CHANNEL,
    TYPE_OPEN_CHANNEL,
    TYPE_OPEN_CHANNEL_ERROR,
    TYPE_OPEN_CHANNEL_OK,
    TYPE_OPEN_CHANNEL_PARKED,
    TYPE_OPEN_CHANNEL_TIMEOUT,
    TYPE_PING,
    TYPE_PONG,
    TYPE_REGISTER_HELLO,
    TYPE_REGISTER_OK,
    decode_frame_body,
    decode_frame_header,
    encode_frame,
    hub_id_from_pem,
    is_string_field,
    parse_ed25519_public_pem,
    sign_payload,
    verify_payload,
)

# How long a hub is allowed to spend between frames while registered.
# Idle hubs are kicked so a stale TCP socket doesn't pin a binding
# forever.
READ_TIMEOUT_SECONDS: Final[float] = 120.0
# How long to wait for the peer's first frame after the server
# pushes ``incoming_channel`` — anything longer and the handshake
# has died.
OPEN_PARK_SECONDS: Final[float] = 30.0
# Bound on concurrent open-channel-parks per session.  Beyond this we
# reject with ``rate_limited``.
MAX_PARKS_PER_SESSION: Final[int] = 16

# Set by RelayDaemon at startup.  Used to find parks held on other
# sessions when a fresh hub registers (a park is per-session, but
# matches are global).
_session_registry: list = []


class _ParkedRequest:
    """A hub A is asking to reach hub B but B isn't registered yet."""

    def __init__(self, requester: HubSession, target_hub_id: str,
                 created_at: float, deadline: float,
                 requester_channel_id: str = ""):
        self.requester = requester
        self.target_hub_id = target_hub_id
        self.created_at = created_at
        self.deadline = deadline
        # The requester's local channel id (echoed back on match so
        # the requester's client can route the ``open_channel_ok``
        # to the right local RelayChannel).
        self.requester_channel_id = requester_channel_id
        self.timer: threading.Timer | None = None


class HubSession:
    """State machine for one hub's connection to the relay."""

    def __init__(self, sock: socket.socket, peer: tuple[str, int],
                 *, registry, channel_registry, relay_private_key,
                 max_payload_bytes: int = MAX_DATA_PAYLOAD,
                 on_disconnect=None) -> None:
        self.sock = sock
        self.peer = peer  # (ip, port) of the hub's TCP endpoint
        self._registry = registry
        self._channels = channel_registry
        self._relay_key = relay_private_key
        self._max_payload = int(max_payload_bytes)
        self._on_disconnect = on_disconnect

        # Hub id is set on successful registration.
        self.hub_id: str | None = None
        self.public_key_pem: str | None = None
        self.public_key: Any = None
        self.registered_at: float | None = None

        # Inbound frame thread.
        self._reader = threading.Thread(target=self._reader_loop,
                                        name=f"relay-reader-{peer[0]}:{peer[1]}",
                                        daemon=True)
        # Outbound frame thread.
        self._outbound: collections.deque[bytes] = collections.deque()
        self._outbound_lock = threading.Lock()
        self._outbound_cv = threading.Condition(self._outbound_lock)
        self._writer = threading.Thread(target=self._writer_loop,
                                        name=f"relay-writer-{peer[0]}:{peer[1]}",
                                        daemon=True)
        # Parked open_channel requests waiting for a peer to register.
        # Each park owns exactly one Timer; the session owns/cancels those
        # timers so no open-channel timeout thread can outlive the session.
        self._parks: list[_ParkedRequest] = []
        self._park_timers: set[threading.Timer] = set()
        self._parks_lock = threading.Lock()

        # Per-channel outbox of bytes the writer should push to the
        # socket.  Indexed by channel_id.
        self._channel_out: dict[str, collections.deque[bytes]] = {}

        # The session is dead as soon as ``_closed`` is set.
        self._closed = False
        self._close_reason: str | None = None
        self._lock = threading.RLock()

        # Register in the global session set so a freshly-registered
        # hub can find parks held on this session.
        _session_registry.append(self)

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._writer.start()
        self._reader.start()

    def close(self, *, reason: str = "local") -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._close_reason = reason
        self._cancel_all_park_timers(join=True)
        try:
            _session_registry.remove(self)
        except ValueError:
            pass
        # IMPORTANT: do NOT shutdown/close the socket until the writer
        # has drained the outbound queue.  This is what allows a
        # register_error / close_channel reply to actually reach the
        # client before the connection dies.  We wait for the writer
        # to drain, then close.
        self._wait_for_writer_drain(timeout=1.0)
        try:
            # Closing the socket makes any blocked recv / sendall raise;
            # the reader / writer threads fall through and exit.
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        # Wake the writer so it can observe the close and exit.
        with self._outbound_lock:
            self._outbound_cv.notify_all()
        if self.hub_id is not None:
            self._registry.unregister(self.hub_id)
            metrics.info("hub_disconnect", hub_id=self.hub_id,
                         peer=f"{self.peer[0]}:{self.peer[1]}",
                         reason=reason)
            # Close any channels this session was part of.
            for ch in self._iter_channels_for_session():
                ch.close(reason=reason)
        if self._on_disconnect is not None:
            try:
                self._on_disconnect(self)
            except Exception:
                pass

    def _wait_for_writer_drain(self, *, timeout: float = 1.0) -> None:
        """Block until the outbound queue is empty or ``timeout`` elapses.

        Used by ``close()`` to ensure any pending reply frames
        (e.g. ``register_error``) are flushed to the TCP socket
        *before* the socket is closed.  Without this, a fast close
        would race the writer thread and the client would see EOF
        before the reply.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._outbound_lock:
                if not self._outbound:
                    return
            time.sleep(0.01)
        # If we time out, give up — the writer will exit on its own
        # when the socket is closed.

    def wake_writer(self) -> None:
        with self._outbound_lock:
            self._outbound_cv.notify_all()

    # ---------------------------------------------------------------- parks

    def _timer_for_park(self, park: _ParkedRequest) -> threading.Timer:
        timer = threading.Timer(OPEN_PARK_SECONDS, self._expire_park, args=(park,))
        timer.daemon = True
        park.timer = timer
        return timer

    def _take_park_timers_locked(
        self,
        parks: list[_ParkedRequest],
    ) -> list[threading.Timer]:
        """Detach timers for ``parks`` while ``_parks_lock`` is held."""
        timers: list[threading.Timer] = []
        for park in parks:
            timer = park.timer
            park.timer = None
            if timer is not None:
                self._park_timers.discard(timer)
                timers.append(timer)
        return timers

    def _cancel_timers(
        self,
        timers: list[threading.Timer],
        *,
        join: bool = False,
    ) -> None:
        current = threading.current_thread()
        for timer in timers:
            timer.cancel()
        if not join:
            return
        for timer in timers:
            if timer is current or not timer.is_alive():
                continue
            timer.join(timeout=0.25)

    def _cancel_all_park_timers(self, *, join: bool = False) -> None:
        """Cancel and detach every parked-open timer owned by this session."""
        with self._parks_lock:
            parks = list(self._parks)
            self._parks.clear()
            timers = self._take_park_timers_locked(parks)
            # Defensive: include any timer registered before its park was
            # appended/removed by a racing callback.
            timers.extend(t for t in self._park_timers if t not in timers)
            self._park_timers.clear()
        self._cancel_timers(timers, join=join)

    def _cancel_parks_for(self, peer_hub_id: str) -> None:
        """Cancel any stale parks for ``peer_hub_id`` before opening a channel."""
        with self._parks_lock:
            parks = [p for p in self._parks if p.target_hub_id == peer_hub_id]
            if not parks:
                return
            self._parks = [p for p in self._parks if p.target_hub_id != peer_hub_id]
            timers = self._take_park_timers_locked(parks)
        self._cancel_timers(timers)

    # ---------------------------------------------------------------- outbound

    def send_frame(self, frame_bytes: bytes) -> None:
        """Queue a fully-framed message for delivery to the hub."""
        if len(frame_bytes) > MAX_FRAME_BYTES + 4:
            raise ValueError("frame too large for outbound queue")
        with self._outbound_lock:
            if self._closed:
                return
            self._outbound.append(frame_bytes)
            self._outbound_cv.notify()

    def enqueue_channel_bytes(self, channel, n: int) -> None:
        """Called by a channel forwarding bytes to us, to record an
        in-flight count.  The channel's ``ack_drained`` is called by
        our writer thread after the TCP socket accepts the bytes.
        """
        # We don't actually need to *store* this; the channel's
        # counter is decremented from the writer thread via
        # ``ack_drained``.  This method exists so the channel can
        # register the bytes-for-ack before we send.
        return None

    def _writer_loop(self) -> None:
        try:
            while True:
                with self._outbound_lock:
                    while not self._outbound and not self._closed:
                        self._outbound_cv.wait(timeout=1.0)
                    if self._closed and not self._outbound:
                        return
                    chunk = self._outbound.popleft()
                try:
                    self.sock.sendall(chunk)
                    # The chunk is now in the OS send buffer (or on
                    # the wire).  Decrement the corresponding
                    # channel's in-flight counter, if the chunk is a
                    # ``data`` frame for a known channel.  We don't
                    # actually know which channel it was for at this
                    # point (the chunk is fully framed), so we just
                    # best-effort peek: read 4 bytes of length, then
                    # peek the body.  This is only an optimization
                    # for the in-flight cap; if we can't parse it
                    # we just skip the ack.
                    self._ack_drained_for_data_chunk(chunk)
                except OSError:
                    self.close(reason="write_error")
                    return
        except Exception as exc:
            metrics.error("writer_loop_crash",
                          peer=f"{self.peer[0]}:{self.peer[1]}",
                          error=str(exc))
            self.close(reason="writer_crash")

    def _ack_drained_for_data_chunk(self, chunk: bytes) -> None:
        """Best-effort: if ``chunk`` is a ``data`` frame, find the
        source session (the one who wrote it) and ack its channel.
        """
        import struct
        import json as _json
        try:
            if len(chunk) < 4:
                return
            body_len = struct.unpack(">I", chunk[:4])[0]
            if body_len > MAX_FRAME_BYTES or body_len <= 0:
                return
            body = chunk[4:4 + body_len]
            obj = _json.loads(body.decode("utf-8"))
            if obj.get("type") != "data":
                return
            cid = obj.get("channel_id", "")
            ch = self._channels.get(cid)
            if ch is None:
                return
            # Determine which side sent this (the source).  If
            # ``self`` is session_b, the source is session_a (and
            # the data flowed a->b).  We need to ack against the
            # source session, not ourselves.
            if ch.session_a is self:
                source = ch.session_b
            elif ch.session_b is self:
                source = ch.session_a
            else:
                return
            ch.ack_drained(source, len(chunk))
        except Exception:
            # Best-effort; ignore parse errors.
            return

    # ---------------------------------------------------------------- inbound

    def _reader_loop(self) -> None:
        try:
            self.sock.settimeout(READ_TIMEOUT_SECONDS)
            while True:
                # Read the 4-byte length.
                header = self._recv_exact(4)
                if header is None:
                    self.close(reason="peer_closed")
                    return
                try:
                    body_len = decode_frame_header(header)
                except ValueError as exc:
                    metrics.warn("bad_frame_header",
                                 peer=f"{self.peer[0]}:{self.peer[1]}",
                                 error=str(exc))
                    self.close(reason="bad_frame_header")
                    return
                body = self._recv_exact(body_len)
                if body is None:
                    self.close(reason="peer_closed")
                    return
                try:
                    obj = decode_frame_body(body)
                except ValueError as exc:
                    metrics.warn("bad_frame_body",
                                 peer=f"{self.peer[0]}:{self.peer[1]}",
                                 error=str(exc))
                    self.close(reason="bad_frame_body")
                    return
                # The session must register before it can do anything else.
                if self.hub_id is None:
                    self._handle_unbound(obj)
                else:
                    self._handle_bound(obj)
        except TimeoutError:
            self.close(reason="idle_timeout")
        except (OSError, ConnectionError) as exc:
            self.close(reason=f"socket_error:{exc}")
        except Exception as exc:
            metrics.error("reader_loop_crash",
                          peer=f"{self.peer[0]}:{self.peer[1]}",
                          error=str(exc))
            self.close(reason="reader_crash")

    def _recv_exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    # ---------------------------------------------------------------- unbound

    def _handle_unbound(self, obj: dict) -> None:
        ftype = obj.get("type")
        if ftype != TYPE_REGISTER_HELLO:
            metrics.warn("frame_before_register",
                         peer=f"{self.peer[0]}:{self.peer[1]}",
                         frame_type=str(ftype))
            self.send_frame(encode_frame({
                "type": "register_error",
                "reason": "register_first",
            }))
            self.close(reason="register_first")
            return
        self._handle_register_hello(obj)

    def _handle_register_hello(self, obj: dict) -> None:
        claimed_hub_id = obj.get("hub_id", "")
        public_key_pem = obj.get("public_key_pem", "")
        if not (is_string_field(obj, "hub_id")
                and is_string_field(obj, "public_key_pem")):
            self.send_frame(encode_frame({
                "type": "register_error",
                "reason": "missing_fields",
            }))
            self.close(reason="register_missing_fields")
            return
        # Verify the hub_id derives from the pubkey (a hub can only
        # claim its own id).
        try:
            derived = hub_id_from_pem(public_key_pem)
        except Exception:
            self.send_frame(encode_frame({
                "type": "register_error",
                "reason": "bad_pubkey",
            }))
            self.close(reason="register_bad_pubkey")
            return
        if derived != claimed_hub_id:
            metrics.warn("register_hub_id_mismatch",
                         peer=f"{self.peer[0]}:{self.peer[1]}",
                         claimed=str(claimed_hub_id)[:16] + "...",
                         derived=str(derived)[:16] + "...")
            self.send_frame(encode_frame({
                "type": "register_error",
                "reason": "hub_id_mismatch",
            }))
            self.close(reason="register_hub_id_mismatch")
            return
        # Parse the pubkey + verify the signature.
        pub = parse_ed25519_public_pem(public_key_pem)
        if pub is None:
            self.send_frame(encode_frame({
                "type": "register_error",
                "reason": "bad_pubkey",
            }))
            self.close(reason="register_bad_pubkey")
            return
        if not verify_payload(obj, pub):
            metrics.warn("register_bad_signature",
                         peer=f"{self.peer[0]}:{self.peer[1]}",
                         hub_id=claimed_hub_id[:16] + "...")
            self.send_frame(encode_frame({
                "type": "register_error",
                "reason": "bad_signature",
            }))
            self.close(reason="register_bad_signature")
            return
        # Bind it.  Re-registration of an existing hub_id replaces the
        # old binding (the new TCP connection wins, kicking the old
        # one).
        old = self._registry.get(claimed_hub_id)
        if old is not None and old.conn is not self:
            try:
                old.conn.close(reason="re_registered")
            except Exception:
                pass
        binding = self._registry.register(
            hub_id=claimed_hub_id,
            public_key_pem=public_key_pem,
            public_key=pub,
            conn=self,
        )
        self.hub_id = claimed_hub_id
        self.public_key_pem = public_key_pem
        self.public_key = pub
        self.registered_at = binding.registered_at
        # Try to match any parked requests targeting this hub across
        # ALL sessions (a park from A is in A's session list, not
        # the freshly-arrived B's list).
        type(self)._match_parked_for_all(claimed_hub_id, self)
        # Send ``register_ok`` signed with the relay's key.
        receipt = {
            "type": TYPE_REGISTER_OK,
            "hub_id": claimed_hub_id,
            "expires_at": binding.expires_at,
        }
        receipt["signature"] = sign_payload(receipt, self._relay_key)
        self.send_frame(encode_frame(receipt))
        metrics.info("hub_register",
                     hub_id=claimed_hub_id,
                     fingerprint=metrics.pubkey_fingerprint(public_key_pem),
                     peer=f"{self.peer[0]}:{self.peer[1]}",
                     expires_at=binding.expires_at)

    # ---------------------------------------------------------------- bound

    def _handle_bound(self, obj: dict) -> None:
        ftype = obj.get("type")
        if ftype == TYPE_OPEN_CHANNEL:
            self._handle_open_channel(obj)
        elif ftype == TYPE_DATA:
            self._handle_data(obj)
        elif ftype == TYPE_CLOSE_CHANNEL:
            self._handle_close_channel(obj)
        elif ftype == TYPE_PING:
            self.send_frame(encode_frame({"type": TYPE_PONG}))
        else:
            metrics.warn("unknown_frame_type",
                         hub_id=self.hub_id,
                         frame_type=str(ftype))
            # Don't close — could be a forward-compat frame the relay
            # doesn't recognise yet.  Drop silently.

    def _handle_open_channel(self, obj: dict) -> None:
        # Verify the hub's signature on this frame.
        if not verify_payload(obj, self.public_key):
            metrics.warn("open_channel_bad_signature",
                         hub_id=self.hub_id)
            return
        peer_hub_id = obj.get("peer_hub_id", "")
        if not is_string_field(obj, "peer_hub_id"):
            self._send_open_error(obj.get("channel_id", ""), "missing_peer")
            return
        if peer_hub_id == self.hub_id:
            # Self-loop.  Reject.
            self._send_open_error(obj.get("channel_id", ""), "self_loop")
            return
        # The requester's channel id (if any) is echoed back so the
        # requester can match it against its local RelayChannel.
        requester_channel_id = str(obj.get("channel_id", "") or "")
        peer = self._registry.get(peer_hub_id)
        if peer is not None and peer.conn is not self:
            # If a channel already exists between us and this peer,
            # just confirm the existing channel back to the requester.
            # This prevents accidentally creating two channels when
            # both sides happen to open at the same time.
            existing = self._channels.find_between(self, peer.conn)
            if existing is not None:
                self.send_frame(encode_frame({
                    "type": TYPE_OPEN_CHANNEL_OK,
                    "channel_id": existing.channel_id,
                    "requester_channel_id": requester_channel_id,
                    "peer_hub_id": peer_hub_id,
                    "peer_hub_id_signature": sign_payload(
                        {"peer_hub_id": peer_hub_id}, self._relay_key),
                }))
                metrics.info("channel_reuse",
                             channel_id=existing.channel_id[:12],
                             requester=self.hub_id,
                             peer=peer_hub_id)
                return
            # Open the channel immediately.
            self._open_channel_with(peer.conn, peer_hub_id,
                                    mode="immediate",
                                    requester_channel_id=requester_channel_id)
            return
        # Park the request and wait for the peer to (re-)register.
        created_at = time.time()
        park = _ParkedRequest(
            requester=self,
            target_hub_id=peer_hub_id,
            created_at=created_at,
            deadline=created_at + OPEN_PARK_SECONDS,
            requester_channel_id=requester_channel_id,
        )
        timer = self._timer_for_park(park)
        with self._parks_lock:
            if len(self._parks) >= MAX_PARKS_PER_SESSION:
                self._cancel_timers([timer])
                self._send_open_error(requester_channel_id, "rate_limited")
                return
            if self._closed:
                self._cancel_timers([timer])
                return
            self._parks.append(park)
            self._park_timers.add(timer)
            timer.start()
        self.send_frame(encode_frame({
            "type": TYPE_OPEN_CHANNEL_PARKED,
            "channel_id": requester_channel_id,
            "peer_hub_id": peer_hub_id,
            "park_seconds": OPEN_PARK_SECONDS,
        }))
        metrics.info("open_channel_parked",
                     hub_id=self.hub_id,
                     peer_hub_id=peer_hub_id)

    def _expire_park(self, park: _ParkedRequest) -> None:
        with self._parks_lock:
            timer = park.timer
            park.timer = None
            if timer is not None:
                self._park_timers.discard(timer)
            if park not in self._parks:
                return
            self._parks.remove(park)
        self.send_frame(encode_frame({
            "type": TYPE_OPEN_CHANNEL_TIMEOUT,
            "channel_id": park.requester_channel_id,
            "peer_hub_id": park.target_hub_id,
        }))
        metrics.info("open_channel_timeout",
                     hub_id=self.hub_id,
                     peer_hub_id=park.target_hub_id)

    def _match_parked_for(self, registered_hub_id: str) -> None:
        """Match parked requests *on this session* targeting ``registered_hub_id``.

        Kept for the (rare) case where a hub parks an open_channel
        on its own session AND the peer registers through the same
        session (e.g. a re-registration of an already-paired hub).
        For the common case (peer is a different session), the
        classmethod ``_match_parked_for_all`` is what runs.
        """
        with self._parks_lock:
            waiting = [p for p in self._parks if p.target_hub_id == registered_hub_id]
            self._parks = [p for p in self._parks if p.target_hub_id != registered_hub_id]
            timers = self._take_park_timers_locked(waiting)
        self._cancel_timers(timers)
        now = time.time()
        for p in waiting:
            if p.deadline <= now:
                # Tell the requester it timed out.
                p.requester.send_frame(encode_frame({
                    "type": TYPE_OPEN_CHANNEL_TIMEOUT,
                    "channel_id": p.requester_channel_id,
                    "peer_hub_id": registered_hub_id,
                }))
                continue
            target = self._registry.get(registered_hub_id)
            if target is None or target.conn is p.requester:
                continue
            self._open_channel_with(
                target.conn, registered_hub_id,
                mode="parked-matched",
                requester=p.requester,
                requester_channel_id=p.requester_channel_id,
            )

    @classmethod
    def _match_parked_for_all(cls, registered_hub_id: str,
                              arriving_session: HubSession) -> None:
        """Match any parked open_channel request (across all sessions)
        that targets the freshly-registered hub.

        A park is held on the requester's session.  When a fresh hub
        registers, we must look at *every* live session's parks, not
        just the arriving session's, otherwise a park that was
        placed before the fresh hub arrived would never be matched.
        """
        for sess in list(_session_registry):
            with sess._parks_lock:
                waiting = [
                    p for p in sess._parks
                    if p.target_hub_id == registered_hub_id
                ]
                sess._parks = [
                    p for p in sess._parks
                    if p.target_hub_id != registered_hub_id
                ]
                timers = sess._take_park_timers_locked(waiting)
            sess._cancel_timers(timers)
            if not waiting:
                continue
            for p in waiting:
                if p.deadline <= time.time():
                    p.requester.send_frame(encode_frame({
                        "type": TYPE_OPEN_CHANNEL_TIMEOUT,
                        "channel_id": p.requester_channel_id,
                        "peer_hub_id": registered_hub_id,
                    }))
                    continue
                target = arriving_session._registry.get(registered_hub_id)
                if target is None or target.conn is p.requester:
                    continue
                arriving_session._open_channel_with(
                    target.conn, registered_hub_id,
                    mode="parked-matched",
                    requester=p.requester,
                    requester_channel_id=p.requester_channel_id,
                )

    def _open_channel_with(self, peer_session: HubSession,
                           peer_hub_id: str, *,
                           mode: str,
                           requester: HubSession | None = None,
                           requester_channel_id: str = "") -> None:
        # The caller of open_channel is `self` (or `requester` for the
        # parked case).  Open a channel between caller and peer_session.
        caller = requester if requester is not None else self
        if caller is peer_session:
            return
        caller._cancel_parks_for(peer_hub_id)
        # Use the requester-provided channel_id when available so the
        # requester can match it against its locally-created
        # RelayChannel.  Fall back to a fresh UUID only if the
        # requester didn't supply one (e.g. older clients).
        channel_id = requester_channel_id or uuid.uuid4().hex
        if self._channels.get(channel_id) is not None:
            # Channel id already in use — generate a fresh one to
            # avoid collision.  The requester won't be able to match
            # this against its local RelayChannel; the client side
            # handles this case (a fresh id is treated as a new
            # channel and the old one is abandoned).
            channel_id = uuid.uuid4().hex
        channel = RelayChannel(
            channel_id=channel_id,
            session_a=caller,
            session_b=peer_session,
        )
        self._channels.add(channel)
        # Tell A they got their channel.
        ok_a = {
            "type": TYPE_OPEN_CHANNEL_OK,
            "channel_id": channel_id,
            "requester_channel_id": requester_channel_id,
            "peer_hub_id": peer_hub_id,
            "peer_hub_id_signature": sign_payload(
                {"peer_hub_id": peer_hub_id}, self._relay_key),
        }
        caller.send_frame(encode_frame(ok_a))
        # Tell B they have an incoming channel.
        incoming_b = {
            "type": TYPE_INCOMING_CHANNEL,
            "channel_id": channel_id,
            "from_hub_id": caller.hub_id or "",
        }
        incoming_b["signature"] = sign_payload(incoming_b, self._relay_key)
        peer_session.send_frame(encode_frame(incoming_b))
        metrics.info("channel_open",
                     channel_id=channel_id[:12],
                     hub_a=caller.hub_id,
                     hub_b=peer_hub_id,
                     mode=mode)

    def _handle_data(self, obj: dict) -> None:
        # Verify the hub's signature on this frame.
        if not verify_payload(obj, self.public_key):
            metrics.warn("data_bad_signature", hub_id=self.hub_id)
            return
        channel_id = obj.get("channel_id", "")
        payload_b64 = obj.get("payload", "")
        if not is_string_field(obj, "channel_id") \
                or not is_string_field(obj, "payload"):
            return
        import base64
        try:
            payload = base64.b64decode(payload_b64, validate=True)
        except Exception:
            return
        if len(payload) > self._max_payload:
            metrics.warn("data_oversize",
                         hub_id=self.hub_id,
                         channel_id=channel_id[:12],
                         size=len(payload))
            return
        ch = self._channels.get(channel_id)
        if ch is None:
            # Unknown channel — could be a stale frame after a close.
            metrics.warn("data_unknown_channel",
                         hub_id=self.hub_id,
                         channel_id=channel_id[:12])
            return
        # Re-verify the channel actually contains this session.
        if ch.session_a is not self and ch.session_b is not self:
            metrics.warn("data_not_in_channel",
                         hub_id=self.hub_id,
                         channel_id=channel_id[:12])
            return
        # Frame the payload for the peer.
        framed = encode_data_frame(channel_id=channel_id, payload=payload)
        ok, reason = ch.forward_to(self, framed)
        if not ok:
            # Close the channel both ways.
            self._close_channel(ch, reason=reason)

    def _handle_close_channel(self, obj: dict) -> None:
        if not verify_payload(obj, self.public_key):
            metrics.warn("close_bad_signature", hub_id=self.hub_id)
            return
        channel_id = obj.get("channel_id", "")
        if not is_string_field(obj, "channel_id"):
            return
        ch = self._channels.get(channel_id)
        if ch is None:
            return
        self._close_channel(ch, reason="peer_closed")

    def _close_channel(self, ch: RelayChannel, *, reason: str) -> None:
        ch.close(reason=reason)
        # Notify both ends (idempotent).
        close_frame = encode_close_frame(channel_id=ch.channel_id, reason=reason)
        try:
            ch.session_a.send_frame(close_frame)
        except Exception:
            pass
        try:
            ch.session_b.send_frame(close_frame)
        except Exception:
            pass
        self._channels.remove(ch.channel_id)
        metrics.info("channel_close",
                     channel_id=ch.channel_id[:12],
                     reason=reason,
                     hub_a=ch.session_a.hub_id,
                     hub_b=ch.session_b.hub_id)

    def _iter_channels_for_session(self) -> list[RelayChannel]:
        """Snapshot of all open channels that include this session."""
        return [
            c for c in self._channels.snapshot()
            if c.session_a is self or c.session_b is self
        ]

    def _remove_channel(self, channel_id: str) -> None:
        """Remove a channel by id (alias for self._channels.remove)."""
        self._channels.remove(channel_id)

    def _send_open_error(self, channel_id: str, reason: str) -> None:
        """Send an ``open_channel_error`` frame back to the requester.

        ``channel_id`` is the requester-provided channel id (may be
        the empty string if the requester did not supply one).
        Echoing it back lets the requester match the error against
        the local RelayChannel it was trying to open.
        """
        self.send_frame(encode_frame({
            "type": TYPE_OPEN_CHANNEL_ERROR,
            "channel_id": channel_id,
            "reason": reason,
        }))
