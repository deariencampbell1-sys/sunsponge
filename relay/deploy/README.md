# SunSponge relay — deploy runbook

> One-command installer: `sudo bash engine/relay/deploy/install-relay.sh`
> from the repo root, on a fresh Ubuntu 22.04+ VPS with SSH already
> configured.

## Before you run it

- [ ] VPS is Ubuntu 22.04 or newer (Debian 12 also works).
- [ ] You can reach it as root over SSH.
- [ ] Inbound TCP 22 (SSH) and 3478 (relay) are open in the cloud
      provider's security group.
- [ ] You have the repo worktree in front of you (or a tarball that
      contains `engine/relay/`).

That's it.  The installer is idempotent and safe to re-run.

## What the installer does

1. **Installs `python3` (3.10+) and `python3-cryptography`** if
   missing.
2. **Creates a system user `relayd`** — no login, no home, no shell.
3. **Copies the relay package** to `/opt/sunsponge-relay/relay/`
   (this includes `relayd.py`, `protocol.py`, `registry.py`,
   `session.py`, `channel.py`, `metrics.py`, `protocol.md`, and
   `README.md`).
4. **Writes `/etc/sunsponge-relay/relay.env`** with sensible
   defaults.  Edit this file to change the port, bind address, TTL,
   or caps; then `systemctl restart sunsponge-relay`.
5. **Installs the systemd unit** `sunsponge-relay.service` with
   full hardening (`NoNewPrivileges`, `ProtectSystem=strict`,
   `RestrictAddressFamilies=AF_INET AF_INET6 AF_NETLINK`,
   `MemoryDenyWriteExecute`, …).
6. **Configures ufw** to default-deny inbound, allow SSH (preserved),
   allow 3478/tcp.
7. **Starts and enables** the service.
8. **Prints the relay's signing-key fingerprint** so you can pin it
   in your trust audit log (the fingerprint is advisory — it is NOT
   in the mTLS trust chain).

## After it runs

```bash
# Status
systemctl status sunsponge-relay

# Live logs
journalctl -u sunsponge-relay -f

# Filter for control events
journalctl -u sunsponge-relay -f | grep -E '"event":"(hub_register|channel_open|channel_close)"'

# Confirm it's listening
ss -ltnp | grep 3478

# Confirm firewall
ufw status verbose
```

You should see one `relay_start` event in the log on first boot and
a `relay_pubkey_fingerprint` field that you can save to your trust
audit log.

## Pointing a hub at this relay

The hub picks up the relay endpoint from `settings.json`:

```json
{
  "relay_endpoint": "<vps-public-ip-or-hostname>:3478"
}
```

Default in this release: `relay.rhobear.ai:3478`.  Set to `null`
to disable the relay entirely and fall back to direct-dial.

To force a single hub *off* the relay (e.g. while debugging), set the
environment variable `RHOBEAR_RELAY_DISABLE=1` before starting the
hub.  The hub will use direct-dial for every outbox message until
the variable is unset.

## Rollback

```bash
sudo systemctl stop sunsponge-relay
sudo systemctl disable sunsponge-relay
sudo rm /etc/systemd/system/sunsponge-relay.service
sudo rm -rf /opt/sunsponge-relay /etc/sunsponge-relay
sudo ufw delete allow 3478/tcp
sudo systemctl daemon-reload
```

## Optional: pinning a long-lived signing key

The installer auto-generates a relay signing key on first run and
stores it at `/etc/sunsponge-relay/relay-ed25519.key` (mode 0600,
owned by `relayd:relayd`).  This key is used to sign the relay's
*control plane* frames (register_ok, incoming_channel,
peer_hub_id_signature).  It is **not** part of the mTLS trust chain.

If you need non-repudiation across restarts (e.g. for a legal
audit), keep the file.  If you want a clean slate on every restart
(so a compromised key is automatically rotated), delete the file
and restart the service — the relay will generate a new one.

## Optional: rate-limiting per source IP

ufw supports per-IP rate limits:

```bash
sudo ufw limit 3478/tcp
```

This caps inbound to 6 connections per 30 s per source IP — a
sensible default.  For higher throughput, drop the `limit` rule and
let the relay's `MAX_CONNECTIONS` cap do the work.

## Optional: a second relay (anycast, regional)

Each hub can only be configured to talk to one relay in v1.  To
deploy a second relay, repeat the installer on a second VPS and
have hubs opt-in by setting their `relay_endpoint` accordingly.
For most deployments, one relay is plenty — the bandwidth is
modest (one mTLS envelope = ≤16 KiB amortised; the relay sees
~1 Gbps on a 1-vCPU VPS).

## Threat model reminder

If the relay host is compromised, the attacker gains a network
MITM position between hubs and the ability to drop traffic.  They
**cannot** decrypt or forge hub-to-hub traffic (mTLS end-to-end),
**cannot** impersonate a hub (pin check), and **cannot** persist
any hub identity across a restart (no persistent registry).

Treat the relay as a soft target.  Monitor its `journald` log for
unusual `hub_register` patterns (a hub re-registering hundreds of
times per second = something is wrong).  Rotate the relay's
signing key on a regular cadence by deleting
`/etc/sunsponge-relay/relay-ed25519.key` and restarting.
