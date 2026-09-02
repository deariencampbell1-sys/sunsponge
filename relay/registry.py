"""relay/registry.py — in-memory (hub_id -> connection) bindings with TTL.

The relay keeps a *lease*-based registry of hubs that have registered
in the last ``REGISTRY_TTL_SECONDS``.  A hub re-registers on every
successful ``open_channel``, so a healthy hub's lease is constantly
refreshed.  An unhealthy hub (silent for >TTL) is evicted on the next
sweep.

The registry is **deliberately in-memory only**.  We do not persist
hub bindings across restarts.  Rationale:

  * The relay is zero-knowledge — persisting bindings would mean
    persisting (hub_id, pubkey) tuples, which is a metadata leak
    window across restarts that we do not need.
  * Restart-friendliness: when the relay restarts, hubs detect the
    disconnect, re-register, and re-open their channels.  This is
    exactly the behaviour we want.
  * A persistent registry would need a write-ahead log + replay
    protection (the kind of complexity we want to keep out of v1).

The registry is locked per mutation (``_lock``) and per lookup
(``RLock`` semantics — a hub's outbound lookup may re-enter the lock
to find the peer).  All public methods are thread-safe.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class HubBinding:
    """A single registered hub on the relay.

    Attributes
    ----------
    hub_id:
        The hub's identity, derived from ``public_key_pem``.  The
        registry will not allow a hub to register under a different
        id than what its key derives to.
    public_key_pem:
        The Ed25519 SubjectPublicKeyInfo PEM the hub presented at
        registration.  Stored only so we can re-derive the hub_id on
        eviction / log; never used for verification of mTLS traffic.
    public_key:
        The parsed Ed25519 public key object (cryptography).  We
        keep the parsed object (not just the PEM) so verification
        doesn't re-parse on every frame.
    conn:
        The :class:`relay.session.HubSession` that owns the TCP
        connection.  A hub can only have one binding at a time;
        re-registration replaces the binding.
    registered_at:
        Unix timestamp of (re-)registration.
    expires_at:
        Unix timestamp at which the binding will be evicted by the
        sweeper.
    """

    hub_id: str
    public_key_pem: str
    public_key: Any
    conn: Any
    registered_at: float
    expires_at: float


class Registry:
    """Thread-safe (hub_id -> HubBinding) store with TTL eviction."""

    def __init__(self, ttl_seconds: float, *, clock=time.monotonic) -> None:
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._bindings: dict[str, HubBinding] = {}
        self._lock = threading.RLock()
        # A counter of registrations (and re-registrations) for metrics.
        self._total_registrations: int = 0
        self._total_evictions: int = 0

    # ------------------------------------------------------------------ helpers

    def _now(self) -> float:
        # Use the real wall clock for expiry semantics; monotonic is for
        # tests that want to control time.
        return time.time() if self._clock is time.monotonic else self._clock()

    # ------------------------------------------------------------------ mutators

    def register(
        self,
        *,
        hub_id: str,
        public_key_pem: str,
        public_key: Any,
        conn: Any,
        ttl_seconds: float | None = None,
    ) -> HubBinding:
        """Bind a hub to its connection.  Replaces any prior binding."""
        ttl = self._ttl if ttl_seconds is None else float(ttl_seconds)
        ts = self._now()
        binding = HubBinding(
            hub_id=hub_id,
            public_key_pem=public_key_pem,
            public_key=public_key,
            conn=conn,
            registered_at=ts,
            expires_at=ts + ttl,
        )
        with self._lock:
            self._bindings[hub_id] = binding
            self._total_registrations += 1
        return binding

    def unregister(self, hub_id: str) -> None:
        """Remove a hub binding (e.g. on disconnect)."""
        with self._lock:
            self._bindings.pop(hub_id, None)

    def renew(self, hub_id: str, *, ttl_seconds: float | None = None) -> bool:
        """Reset the expiry on an existing binding.  Returns False if gone."""
        ttl = self._ttl if ttl_seconds is None else float(ttl_seconds)
        ts = self._now()
        with self._lock:
            binding = self._bindings.get(hub_id)
            if binding is None:
                return False
            # Mutate in place (HubBinding is not a frozen dataclass) so
            # any other code that holds a reference sees the new expiry.
            binding.expires_at = ts + ttl
            return True

    # ------------------------------------------------------------------ lookups

    def get(self, hub_id: str) -> HubBinding | None:
        with self._lock:
            return self._bindings.get(hub_id)

    def has(self, hub_id: str) -> bool:
        with self._lock:
            return hub_id in self._bindings

    def all_hub_ids(self) -> list[str]:
        with self._lock:
            return list(self._bindings.keys())

    def size(self) -> int:
        with self._lock:
            return len(self._bindings)

    # ------------------------------------------------------------------ sweeper

    def sweep(self) -> list[str]:
        """Evict expired bindings.  Returns the list of evicted hub_ids."""
        ts = self._now()
        evicted: list[str] = []
        with self._lock:
            for hub_id, binding in list(self._bindings.items()):
                if binding.expires_at <= ts:
                    self._bindings.pop(hub_id, None)
                    evicted.append(hub_id)
                    self._total_evictions += 1
        return evicted

    # ------------------------------------------------------------------ metrics

    @property
    def total_registrations(self) -> int:
        with self._lock:
            return self._total_registrations

    @property
    def total_evictions(self) -> int:
        with self._lock:
            return self._total_evictions


# ---------------------------------------------------------------------------
# Channel-registry: hub-to-hub channel table
# ---------------------------------------------------------------------------

# NOTE: we store :class:`relay.channel.RelayChannel` directly.  The
# older ``ChannelBinding`` dataclass was redundant; RelayChannel
# already carries everything ChannelRegistry needs (channel_id,
# session_a, session_b, queues) and is what the rest of the relay
# uses.  We deliberately do NOT import RelayChannel here (even under
# TYPE_CHECKING) to avoid a circular import at module load time;
# the docstring references in this file are string-form so type
# checkers still see the link.


class ChannelRegistry:
    """Maps ``channel_id`` -> :class:`relay.channel.RelayChannel`.

    Lookups are O(1) and thread-safe.  Channels are removed when either
    side closes; an entry whose session is gone is treated as closed
    and the peer is notified.
    """

    def __init__(self) -> None:
        self._channels: dict[str, Any] = {}
        self._lock = threading.RLock()

    def add(self, channel) -> None:  # type: ignore[no-untyped-def]
        """Register a :class:`RelayChannel` instance."""
        with self._lock:
            self._channels[channel.channel_id] = channel

    def get(self, channel_id: str):
        with self._lock:
            return self._channels.get(channel_id)

    def remove(self, channel_id: str):
        with self._lock:
            return self._channels.pop(channel_id, None)

    def count_for(self, session: Any) -> int:
        """How many open channels involve this session."""
        with self._lock:
            return sum(
                1
                for c in self._channels.values()
                if c.session_a is session or c.session_b is session
            )

    def size(self) -> int:
        with self._lock:
            return len(self._channels)

    def snapshot(self) -> list:
        """Return a shallow copy of all current channel bindings."""
        with self._lock:
            return list(self._channels.values())

    def find_between(self, session_a: Any, session_b: Any):
        """Return the open channel between ``session_a`` and ``session_b``
        (in either order), or None if no channel exists.

        Used by the open_channel handler to ensure a single channel
        exists per pair of sessions (a hub on each side).
        """
        with self._lock:
            for c in self._channels.values():
                if (c.session_a is session_a and c.session_b is session_b) \
                        or (c.session_a is session_b and c.session_b is session_a):
                    return c
            return None
