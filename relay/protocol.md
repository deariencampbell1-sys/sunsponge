# Hub-link relay — wire protocol spec

> Source of truth for the on-the-wire format between a Rhobear hub and
> the SunSponge relay (`relayd`).  The implementation lives in
> `engine/relay/protocol.py` and `engine/relay/session.py` on the
> server side and in `engine/rhobear/hub_relay.py` on the hub side.

## 0. Transport

* One TCP connection per hub.
* Plain TCP, no TLS — the bytes the relay sees are *already* the
  end-to-end mTLS records of the two paired hubs, or relay-protocol
  control frames.
* Max frame size: 64 KiB (`MAX_FRAME_BYTES`).

## 1. Frame format

Every frame is:

```
+----------------+----------------------------------+
| length: u32 BE | body: UTF-8 JSON (length bytes)  |
+----------------+----------------------------------+
```

`length` is the byte length of the JSON body.  Both sides reject
frames with `length == 0` or `length > 65536`.

## 2. Frame types

| Direction | Type                  | Purpose                                 |
|-----------|-----------------------|-----------------------------------------|
| H → R     | `register_hello`      | Initial hub registration                |
| R → H     | `register_ok`         | Registration accepted                    |
| R → H     | `register_error`      | Registration rejected                   |
| H → R     | `open_channel`        | Request a channel to peer B             |
| R → H     | `open_channel_ok`     | Channel open                            |
| R → H     | `open_channel_parked` | Peer not registered; will wait          |
| R → H     | `open_channel_timeout`| Park expired without peer appearing     |
| R → H     | `open_channel_error`  | Hard reject (rate-limited, self-loop)   |
| R → H     | `incoming_channel`    | Server-side push to peer B              |
| H → R     | `data`                | Forward opaque bytes (mTLS record)      |
| R → H     | `data`                | Forward opaque bytes (mTLS record)      |
| H → R     | `close_channel`       | End a channel                           |
| R → H     | `close_channel`       | Server closing a channel (e.g. peer slow)|
| H → R     | `ping`                | Liveness                                |
| R → H     | `pong`                | Liveness reply                          |

## 3. Registration

### 3.1 `register_hello` (hub → relay)

```json
{
  "type": "register_hello",
  "hub_id": "<32-char base64url no padding>",
  "public_key_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
  "signature": "<base64 Ed25519 over canonical body without 'signature'>"
}
```

Validation order (fail-closed at every step):

1. `hub_id` and `public_key_pem` are non-empty strings.
2. `derived_hub_id = hub_id_from_pem(public_key_pem)` matches the
   `hub_id` field.  A hub can therefore only claim its own id.
3. `verify_payload(payload, parsed_pubkey)` is `True`.  A hub must
   hold the private key for the presented public key.
4. On success, the relay binds `hub_id -> session` and replies.

### 3.2 `register_ok` (relay → hub)

```json
{
  "type": "register_ok",
  "hub_id": "<32-char base64url no padding>",
  "expires_at": 1718300000.123,
  "signature": "<base64 Ed25519 signed with the relay's long-term key>"
}
```

The `signature` is over the canonical body with `signature` removed.
The hub may (or may not) verify this against a pinned relay pubkey;
it is for non-repudiation of the registration receipt only.

### 3.3 `register_error` (relay → hub)

```json
{ "type": "register_error", "reason": "<string>" }
```

Reasons:

* `register_first` — frame sent before registration.
* `missing_fields` — `hub_id` or `public_key_pem` empty.
* `bad_pubkey` — PEM parse error or not Ed25519.
* `hub_id_mismatch` — derived id ≠ claimed id.
* `bad_signature` — Ed25519 verify failed.

## 4. Open a channel

### 4.1 `open_channel` (hub A → relay)

```json
{
  "type": "open_channel",
  "peer_hub_id": "<B>",
  "signature": "<base64 Ed25519>"
}
```

If B is registered and the signature verifies, the relay opens a
channel immediately and replies to A with `open_channel_ok` and
pushes `incoming_channel` to B.

If B is not registered, A receives `open_channel_parked`:

```json
{ "type": "open_channel_parked", "peer_hub_id": "<B>",
  "park_seconds": 30 }
```

When B subsequently registers, the relay pairs the parked request
automatically.  If B does not register within `park_seconds` (default
30), A receives `open_channel_timeout`:

```json
{ "type": "open_channel_timeout", "peer_hub_id": "<B>" }
```

### 4.2 `open_channel_ok` (relay → A)

```json
{
  "type": "open_channel_ok",
  "channel_id": "<uuid hex>",
  "peer_hub_id": "<B>",
  "peer_hub_id_signature": "<base64 Ed25519, relay signs the string peer_hub_id>"
}
```

### 4.3 `incoming_channel` (relay → B)

```json
{
  "type": "incoming_channel",
  "channel_id": "<uuid hex>",
  "from_hub_id": "<A>",
  "signature": "<base64 Ed25519, relay signs the body>"
}
```

A hub that receives an `incoming_channel` MUST verify the relay
signature against its configured relay pubkey (if one is pinned) to
detect relay-side hijacking.  v1 logs a warning if verification
fails but does not close the channel — the *real* authentication is
the mTLS handshake that follows.

## 5. Data forwarding

```json
{
  "type": "data",
  "channel_id": "<uuid hex>",
  "payload": "<base64 of opaque bytes>",
  "signature": "<base64 Ed25519>"
}
```

`payload` is base64-encoded; the relay forwards the raw bytes
decoded, *byte-for-byte*, to the peer's session.  The relay does
**not** inspect the payload (it cannot — the payload is, in practice,
an mTLS record).

## 6. Channel close

```json
{ "type": "close_channel", "channel_id": "<uuid hex>",
  "reason": "ok | peer_closed | peer_slow | oversize | re_registered",
  "signature": "<base64 Ed25519>" }
```

The relay forwards the close to the peer (without the signature,
since the peer doesn't have the requester's pubkey in this protocol)
and removes the channel from its table.

## 7. Backpressure

* Per-channel in-flight cap: 1 MiB.  When a peer's outbound queue
  exceeds the cap, the channel is closed with reason `peer_slow`.
* Single-frame payload cap: 64 KiB (enforced on receive).
* Idle TCP read timeout: 120 s.  An idle hub is kicked.

## 8. Versioning

The current protocol has no version field.  Future revisions
introduce a top-level `"v": 1` integer on every frame; hubs that see
an unknown `v` value close the connection.  v1 (this spec) is
identified by the *absence* of `v` and is fully wire-compatible with
the current implementation.
