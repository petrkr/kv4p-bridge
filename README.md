# kv4p-bridge

Userspace bridge for KV4P-HT radios.

MVP scope:

- KV4P KISS protocol over USB serial
- PTT
- SQL/COS
- native KV4P RX/TX audio payload forwarding
- dummy connector first

No SvxLink connector yet. The current target profile is the v2.0.0.1
Android/FW17 line, where live voice audio is Opus on KV4P command `0x07`.

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
