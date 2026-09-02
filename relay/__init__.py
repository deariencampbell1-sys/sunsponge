"""SunSponge hub-link relay (relayd).

A zero-knowledge relay for the Rhobear hub-to-hub mesh.  See
``docs/plans/HUB-RELAY-DESIGN.md`` for the security model and
``engine/relay/protocol.md`` for the wire spec.

This package is the *server side* of the relay.  It is intentionally
small (~250 LoC) and uses only the Python standard library so the
deploy image can be a one-file copy onto a tiny Ubuntu VPS.

Layout:

  relayd.py        — the daemon entry point (python3 -m relay.relayd or
                     python3 relay/relayd.py)
  protocol.py      — frame encoding, signature verification, hub_id
                     derivation from an Ed25519 PEM
  registry.py      — in-memory (hub_id, pubkey, conn) bindings + TTL
  channel.py       — per-channel byte pump with backpressure
  metrics.py       — structured JSON event logging to stdout
  deploy/          — systemd unit + ufw + install script
  README.md        — operator quick-start
  protocol.md      — wire spec
"""

__all__: list[str] = []
