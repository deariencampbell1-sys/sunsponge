"""relay/channel.py — per-channel byte pump + backpressure.

A *channel* is a virtual full-duplex byte stream between two hub
sessions.  The relay treats both directions independently:

  * A 1 MiB in-flight cap per direction (the "send queue").  When
    a slow consumer falls behind, frames beyond the cap are dropped
    and the channel is closed with reason ``peer_slow``.

  * A 64 KiB cap on any single data frame (matches the relay's
    frame cap, ``MAX_FRAME_BYTES``).

  * Frames are forwarded **byte-for-byte** — the relay NEVER inspects
    application payload.  What it sees is, in the worst case, TLS
    1.3 records (``0x16 0x03 0x03 ...``), which are AEAD-encrypted
    under the *end hubs'* session keys.

The pump has no idea what mTLS is.  It only knows that bytes go
``src.send_queue`` -> ``dst`` and vice versa.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Final

# Cap on bytes in-flight per direction.  When a peer's send queue
# exceeds this, we close the channel.  1 MiB is small enough that a
# stuck consumer can't OOM the relay and large enough that a normal
# mTLS envelope (≤16 KiB) plus burst tolerates network jitter.
MAX_INFLIGHT_BYTES: Final[int] = 1 * 1024 * 1024

# Cap on a single ``data`` frame's payload.  Mirrors
# ``MAX_FRAME_BYTES`` in protocol.py; 64 KiB.
MAX_DATA_PAYLOAD: Final[int] = 64 * 1024


@dataclass
class SendQueue:
    """Thread-safe FIFO of bytes with a hard cap.

    Items in the queue are *already framed* bytes that the peer can
    write straight to its TCP socket — the queue is drained by
    :class:`RelayChannel.pump_to_peer`.

    The queue is a :class:`collections.deque` of ``bytes``.  We do
    not use :class:`queue.Queue` because we want the *bytes* API and
    the *full* atomicity of each enqueue (a TLS record or a single
    envelope is never split across writes).
    """

    peer_session: Any
    cap_bytes: int = MAX_INFLIGHT_BYTES
    _items: collections.deque[bytes] = field(default_factory=collections.deque)
    _current_bytes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False
    _close_reason: str | None = None

    def push(self, framed: bytes) -> bool:
        """Enqueue a framed chunk.  Returns False if it would exceed the cap."""
        with self._lock:
            if self._closed:
                return False
            if self._current_bytes + len(framed) > self.cap_bytes:
                self._close_reason = "peer_slow"
                self._closed = True
                return False
            self._items.append(framed)
            self._current_bytes += len(framed)
            return True

    def pop(self) -> bytes | None:
        with self._lock:
            if not self._items:
                return None
            chunk = self._items.popleft()
            self._current_bytes -= len(chunk)
            return chunk

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def close_reason(self) -> str | None:
        with self._lock:
            return self._close_reason

    def close(self, reason: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._close_reason = reason

    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes


class RelayChannel:
    """A paired channel between two sessions.

    Created by :meth:`relay.session.HubSession.open_channel` after the
    server-side handshake completes (peer was already registered, or
    a parked request matched a fresh registration).

    The channel's lifecycle is:

      1. ``__init__`` — both sessions are bound, both queues exist.
      2. data flows: each session's reader thread receives a
         ``data`` frame, looks up this channel, pushes the framed
         bytes into the *opposite* session's send queue, and wakes
         the writer thread.
      3. Either side sends ``close_channel`` (or the relay forces a
         close on backpressure / disconnect).
    """

    def __init__(
        self,
        *,
        channel_id: str,
        session_a: Any,
        session_b: Any,
    ) -> None:
        self.channel_id = channel_id
        self.session_a = session_a
        self.session_b = session_b
        self.created_at = time.time()
        self._closed = False
        self._lock = threading.Lock()
        # Per-direction in-flight byte counter.  When the destination
        # session's outbound queue exceeds the cap, we drop the frame
        # and close the channel with reason ``peer_slow``.  This is
        # the back-pressure mechanism.
        self._inflight_a_to_b = 0
        self._inflight_b_to_a = 0

    # ----------------------------------------------------------------- routing

    def forward_to(self, src_session: Any, framed: bytes) -> tuple[bool, str]:
        """Forward framed bytes from ``src_session`` to its peer.

        Returns ``(ok, reason)``.  If ``ok`` is False, the channel is
        closed; the caller should send a ``close_channel`` frame to
        both ends.
        """
        with self._lock:
            if self._closed:
                return False, "channel_closed"
        if len(framed) > MAX_DATA_PAYLOAD:
            self._close(reason="oversize")
            return False, "oversize"
        if src_session is self.session_a:
            dst = self.session_b
            new_inflight = self._inflight_a_to_b + len(framed)
            if new_inflight > MAX_INFLIGHT_BYTES:
                self._close(reason="peer_slow")
                return False, "peer_slow"
            self._inflight_a_to_b = new_inflight
        elif src_session is self.session_b:
            dst = self.session_a
            new_inflight = self._inflight_b_to_a + len(framed)
            if new_inflight > MAX_INFLIGHT_BYTES:
                self._close(reason="peer_slow")
                return False, "peer_slow"
            self._inflight_b_to_a = new_inflight
        else:
            return False, "unknown_source"
        # Hand the framed bytes to the destination session's outbound
        # queue.  The session's writer thread will pick them up and
        # write them to the TCP socket.  When the write succeeds
        # (TCP has accepted the bytes), the session's writer calls
        # ``ack_drained(channel, n)`` to decrement our counter.
        try:
            dst.enqueue_channel_bytes(self, len(framed))
        except Exception:
            self._close(reason="peer_slow")
            return False, "peer_slow"
        try:
            dst.send_frame(framed)
        except Exception:
            self._close(reason="peer_slow")
            return False, "peer_slow"
        return True, "ok"

    def ack_drained(self, src_session: Any, n: int) -> None:
        """Called by the destination session when it has written
        ``n`` bytes to the TCP socket.  Decrements the in-flight
        counter.
        """
        with self._lock:
            if src_session is self.session_a:
                self._inflight_b_to_a = max(0, self._inflight_b_to_a - n)
            elif src_session is self.session_b:
                self._inflight_a_to_b = max(0, self._inflight_a_to_b - n)

    # ----------------------------------------------------------------- teardown

    def _close(self, *, reason: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self, *, reason: str = "local") -> None:
        """Close the channel.  Idempotent."""
        self._close(reason=reason)


# ---------------------------------------------------------------------------
# Data frame helpers (server-side encoding only — the hub does the
# client-side encoding in engine/rhobear/hub_relay.py)
# ---------------------------------------------------------------------------

def encode_data_frame(*, channel_id: str, payload: bytes) -> bytes:
    """Encode a ``data`` frame body for forwarding to a peer.

    Returns the *complete* framed bytes (header + body) ready to
    write to a socket.
    """
    from .protocol import encode_frame
    import base64
    obj = {
        "type": "data",
        "channel_id": channel_id,
        "payload": base64.b64encode(payload).decode("ascii"),
    }
    return encode_frame(obj)


def encode_close_frame(*, channel_id: str, reason: str = "ok") -> bytes:
    """Encode a ``close_channel`` frame for forwarding to a peer."""
    from .protocol import encode_frame
    return encode_frame(
        {
            "type": "close_channel",
            "channel_id": channel_id,
            "reason": reason,
        }
    )
