"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from .config import load_config
from .connectors import create_connector
from .kv4p import Kv4pRadio, Kv4pSettings
from .log import setup_logging

logger = logging.getLogger(__name__)


def _wait_for_signal() -> None:
    done = threading.Event()

    def stop(signum: int, _frame: object) -> None:
        logger.info("received signal %s", signum)
        done.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        done.wait()
    except KeyboardInterrupt:
        logger.info("received keyboard interrupt")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", help="Path to TOML config")
    parser.add_argument("--log-level", help="Override log level")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(args.log_level or cfg.log.level)

    connector = create_connector(cfg.connector)

    def on_rx_audio(payload: bytes) -> None:
        try:
            connector.on_rx_audio(payload)
        except Exception:
            logger.exception("connector rx audio handler failed")

    def on_sql(open: bool) -> None:
        try:
            connector.on_sql(open)
        except Exception:
            logger.exception("connector sql handler failed")

    radio = Kv4pRadio(
        cfg.kv4p.device,
        baudrate=cfg.kv4p.baudrate,
        settings=Kv4pSettings(
            rx_freq=cfg.kv4p.radio.rx_freq,
            tx_freq=cfg.kv4p.radio.tx_freq,
            bandwidth=cfg.kv4p.radio.bandwidth,
            squelch=cfg.kv4p.radio.squelch,
            ctcss_rx=cfg.kv4p.radio.ctcss_rx,
            ctcss_tx=cfg.kv4p.radio.ctcss_tx,
            high_power=cfg.kv4p.radio.high_power,
            tx_allowed=cfg.kv4p.radio.tx_allowed,
            rssi=cfg.kv4p.radio.rssi,
            filter_pre=cfg.kv4p.radio.filter_pre,
            filter_high=cfg.kv4p.radio.filter_high,
            filter_low=cfg.kv4p.radio.filter_low,
        ),
        tx_audio_command=cfg.kv4p.tx_audio_command,
        rx_audio_open=cfg.kv4p.rx_audio_open,
        status_reports=cfg.kv4p.status_reports,
        on_rx_audio=on_rx_audio,
        on_sql=on_sql,
    )

    connector.open(radio)
    try:
        with radio:
            _wait_for_signal()
    finally:
        connector.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
