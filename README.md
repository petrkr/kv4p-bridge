# kv4p-bridge

Userspace bridge for KV4P-HT radios.

MVP scope:

- KV4P KISS protocol over USB serial
- PTT
- SQL/COS
- native KV4P RX/TX audio payload forwarding
- dummy connector first

The current target profile is the v2.0.0.1 Android/FW17 line, where live
voice audio is Opus on KV4P command `0x07`.

## Run

```sh
python -m kv4p_bridge --config config.example.toml --log-level DEBUG
```

With `kv4p.device = ""`, the radio transport is disabled and only the dummy
connector lifecycle is exercised.

## Layout

```text
kv4p_bridge/
  __main__.py
  config.py
  log.py
  kv4p/
    __init__.py
    protocol.py
    transport.py
    types.py
  connectors/
    base.py
    dummy.py
```

`Kv4pRadio` owns only USB/protocol/radio state. The application entry point owns
configuration, connector lifecycle, and callback dispatch. Audio is passed as
KV4P-native payload bytes; conversion is a connector concern.

## SvxLink connector

Bridges to SvxLink's local UDP audio device (`AUDIO_DEV=udp:host:port`, see
`svxlink.conf(5)`) plus its PTY-based squelch (`SQL_DET=PTY`) and PTT
(`PTT_TYPE=PTY`) detectors. No extra dependency (stdlib only).

RX and TX need **separate** `Local` sections with separate ports. SvxLink's
`AudioDeviceUDP` always sends its TX audio to the exact `host:port` given in
`AUDIO_DEV` -- it does not track a peer, so a single section shared for both
RX and TX (`RX=KV4P`, `TX=KV4P` on the same `AUDIO_DEV`) makes SvxLink's TX
audio loop back to itself instead of reaching the bridge.

This is inherently a same-host/LAN transport: SvxLink always sends TX audio
to a fixed, pre-configured address, with no peer discovery or NAT traversal.
It is not suitable if SvxLink and this bridge are not on the same reachable
network (e.g. the radio side is behind a NAT with no public/forwarded port)
-- that would need SvxLink's `TYPE=Net` protocol instead, not implemented here.

Matching `svxlink.conf` setup:

```ini
[GLOBAL]
CARD_SAMPLE_RATE=48000   # must match kv4p Opus rate, avoids resampling

[SimplexLogic]
RX=KV4P
TX=KV4PTx

[KV4P]
TYPE=Local
AUDIO_DEV=udp:127.0.0.1:10100   # matches connector.svxlink.remote_port
AUDIO_CHANNEL=0
SQL_DET=PTY
PTY_PATH=/tmp/kv4p_sql           # matches connector.svxlink.sql_pty_path

[KV4PTx]
TYPE=Local
AUDIO_DEV=udp:127.0.0.1:10101   # matches connector.svxlink.bind_port
AUDIO_CHANNEL=0
PTT_TYPE=PTY
PTT_PTY=/tmp/kv4p_ptt            # matches connector.svxlink.ptt_pty_path
```

`remote_host`/`remote_port` is where SvxLink's Rx section listens -- the
bridge connects to and sends RX audio there. `bind_host`/`bind_port` is the
address the bridge itself binds -- SvxLink's Tx section sends its TX audio
there. The PTY paths are created by SvxLink itself (`posix_openpt` +
symlink); the bridge only opens the path SvxLink creates, retrying until it
appears.
