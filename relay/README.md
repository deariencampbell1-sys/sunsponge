# SunSponge hub-link relay (`relayd`)

A zero-knowledge relay for the Rhobear hub-to-hub mesh.  See
[`docs/plans/HUB-RELAY-DESIGN.md`](../../docs/plans/HUB-RELAY-DESIGN.md)
for the security model and [`engine/relay/protocol.md`](protocol.md)
for the wire spec.

## TL;DR

```bash
# On a fresh Ubuntu 22.04+ VPS (or any POSIX box with Python 3.10+):
sudo bash engine/relay/deploy/install-relay.sh
systemctl status sunsponge-relay
journalctl -u sunsponge-relay -f
```

That's it.  The relay is running on TCP/3478, hardened systemd
service, ufw locked down to SSH + 3478, structured JSON logs in
journald.  Hubs point at `<vps-public-ip>:3478` (or
`relay.rhobear.ai:3478` if you used the default hostname).

## What this is *not*

* It is **not** a TURN/ICE server.  It only relays bytes between two
  *paired* hubs, identified by their Ed25519 pubkeys.
* It is **not** an authentication server.  Hubs already have
  identities (their Ed25519 hub keys); the relay just confirms the
  hub is who it says it is, then steps out of the way.
* It is **not** a hub itself.  It does not speak mTLS, it does not
  decode envelopes, it does not see message bodies.  Compromising
  the relay host is equivalent to compromising the network
  between the two hubs — *not* to compromising either hub.

## Files

| Path                                       | What                                                 |
|--------------------------------------------|------------------------------------------------------|
| `relayd.py`                                | Daemon entry point                                   |
| `protocol.py`                              | Frame codec, Ed25519 helpers, hub_id derivation      |
| `registry.py`                              | In-memory (hub_id, pubkey, conn) bindings + TTL      |
| `channel.py`                               | Per-channel byte pump + 1 MiB backpressure cap       |
| `session.py`                               | Per-connection state machine + reader/writer threads |
| `metrics.py`                               | Structured JSON logging                              |
| `__init__.py`                              | Package marker                                       |
| `protocol.md`                              | Wire-spec doc                                        |
| `deploy/install-relay.sh`                  | One-command installer                                |
| `deploy/relayd.service`                    | systemd unit                                         |
| `deploy/relayd.env`                        | environment file                                     |
| `deploy/ufw-rules.sh`                      | firewall rules (called by install)                   |
| `deploy/README.md`                         | deploy runbook                                       |

## Configuration

`/etc/sunsponge-relay/relay.env`:

| Var                | Default                     | Notes                                       |
|--------------------|-----------------------------|---------------------------------------------|
| `PORT`             | 3478                        | TCP listen port                             |
| `BIND`             | 0.0.0.0                     | bind address                                |
| `REGISTRY_TTL`    | 3600                        | seconds a hub lease is valid                |
| `MAX_CONNECTIONS`  | 4096                        | hard cap on simultaneous TCP connections    |
| `MAX_CHANNELS`     | 16384                       | hard cap on simultaneous open channels      |
| `MAX_DATA_BYTES`   | 65536                       | max single-frame payload (default 64 KiB)   |
| `RELAY_KEY_PATH`   | `/etc/sunsponge-relay/relay-ed25519.key` | long-term signing key            |

`MAX_DATA_BYTES` deliberately matches the mTLS record max (2^14 +
256 bytes) — anything bigger is rejected; the hub's mTLS would have
rejected it anyway.

## What the relay sees

When two paired hubs are talking via the relay, the relay's
`journalctl` shows something like:

```json
{"ts": "...", "level": "info", "event": "hub_register",
 "hub_id": "abc...", "fingerprint": "d0b4..."}
{"ts": "...", "level": "info", "event": "channel_open",
 "channel_id": "...", "hub_a": "abc...", "hub_b": "def...", "mode": "immediate"}
{"ts": "...", "level": "info", "event": "channel_close",
 "channel_id": "...", "reason": "peer_closed", "hub_a": "...", "hub_b": "..."}
```

It does **not** log message bodies, message sizes, or any byte
that came in via a `data` frame.  All it logs is *control plane*
events (who's connected, when a channel opened/closed, and the
reason for any close).

## Verifying the deploy

```bash
# 1.  Relay is up
systemctl status sunsponge-relay

# 2.  Listening
ss -ltnp | grep 3478

# 3.  Firewall
ufw status

# 4.  Smoke test (no Ed25519 — just confirms TCP works)
nc -vz <relay-host> 3478
```

End-to-end smoke (requires two hubs):

```bash
# On hub A:
$ python3 -c "from rhobear.hub_relay import RelayClient; \
              print(RelayClient.register_with('relay.example.com:3478'))"

# On hub B:
$ python3 -c "from rhobear.hub_relay import RelayClient; \
              print(RelayClient.register_with('relay.example.com:3478'))"

# On hub A:
$ python3 -c "from rhobear.hub_relay import RelayClient; \
              print(RelayClient.open_channel_to('relay.example.com:3478', '<B hub_id>'))"
```

The `hub_register` and `channel_open` events should appear in
`journalctl -u sunsponge-relay -f` within a second.

## Hardening (already applied by `install-relay.sh`)

* Systemd `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=`,
  `PrivateTmp`, `RestrictSUIDSGID`, `RestrictNamespaces`,
  `LockPersonality`, `MemoryDenyWriteExecute`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_NETLINK`.
* Runs as user `relayd` (system account, no login, no home).
* ufw default deny inbound; only SSH (preserved) + 3478/tcp open.
* Optional: rate-limit per source IP with `ufw limit` (see
  `deploy/README.md`).
* Relay Ed25519 key on disk is mode 0600, owned by `relayd`.
* No persistent registry — a restart forgets everything, which is
  the desired behaviour (hubs re-register transparently).

## Threat model reminder

The relay is the most exposed piece in the system.  If it is
compromised, an attacker gains:

* **Network MITM position** between hubs — *but* they cannot read
  traffic (mTLS end-to-end).
* **Ability to drop traffic** — same as a network outage, no
  security impact.
* **Ability to forge relay control frames** (`register_ok`,
  `incoming_channel`) — *but* these are not in the mTLS trust
  chain.  The hub pins its *peer's* self-signed cert, not the
  relay's.  A forged `incoming_channel` cannot lead to a successful
  mTLS handshake with a peer that wasn't actually opened.
* **Timing / size side channels** — the same as a network ISP
  would have.  We accept this.

What a relay compromise **cannot** do:

* Decrypt or read any hub-to-hub message.
* Forge a message that either hub will accept (pin check fails).
* Cause one hub to address a third hub that it isn't paired with
  (non-transitive trust is enforced at the pairing layer, not the
  relay).
* Persist a hub's pubkey or hub_id across a restart (no persistent
  registry).

This is the same threat model as Tailscale's DERP relays, MoQ
gateways, and SSH jump hosts.  The relay is a soft target, not a
high-value one.
