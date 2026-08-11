"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from .config import load_config
from .connectors import create_connector
from kv4p import Kv4pRadio
from kv4p.transports.dummy import DummyTransport
from kv4p.transports.serial import Kv4pSerialTransport
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

    def on_ax25_frame(payload: bytes) -> None:
        try:
            connector.on_ax25_frame(payload)
        except Exception:
            logger.exception("connector ax25 frame handler failed")

    if cfg.kv4p.device:
        transport = Kv4pSerialTransport(cfg.kv4p.device, cfg.kv4p.baudrate)
    else:
        logger.warning("kv4p device not configured; using DummyTransport")
        transport = DummyTransport()

    radio = Kv4pRadio(
        transport,
        rx_audio_open=cfg.kv4p.rx_audio_open,
        status_reports=cfg.kv4p.status_reports,
        on_rx_audio=on_rx_audio,
        on_sql=on_sql,
        on_ax25_frame=on_ax25_frame,
    )

    connector.open(radio)
    try:
        with radio:
            # radio.freq_rx/bandwidth/squelch/... are already seeded from the
            # firmware's actual tuned state (HELLO's DeviceState) — only push
            # a set_*() when the configured value actually differs from it.
            radio_cfg = cfg.kv4p.radio
            if radio_cfg.rx_freq != radio.freq_rx or radio_cfg.tx_freq != radio.freq_tx:
                radio.set_frequency(radio_cfg.rx_freq, radio_cfg.tx_freq)
            if radio_cfg.bandwidth != radio.bandwidth:
                radio.set_bandwidth(radio_cfg.bandwidth)
            if radio_cfg.squelch != radio.squelch:
                radio.set_squelch(radio_cfg.squelch)
            ctcss_rx = radio_cfg.ctcss_rx if radio_cfg.ctcss_rx != radio.ctcss_rx else None
            ctcss_tx = radio_cfg.ctcss_tx if radio_cfg.ctcss_tx != radio.ctcss_tx else None
            if ctcss_rx is not None or ctcss_tx is not None:
                radio.set_ctcss(rx=ctcss_rx, tx=ctcss_tx)
            if radio_cfg.high_power != radio.high_power:
                radio.set_high_power(radio_cfg.high_power)
            if radio_cfg.tx_allowed != radio.tx_allowed:
                radio.set_tx_allowed(radio_cfg.tx_allowed)
            if radio_cfg.rssi != radio.rssi:
                radio.set_rssi(radio_cfg.rssi)
            pre = radio_cfg.filter_pre if radio_cfg.filter_pre != radio.filter_pre else None
            high = radio_cfg.filter_high if radio_cfg.filter_high != radio.filter_high else None
            low = radio_cfg.filter_low if radio_cfg.filter_low != radio.filter_low else None
            if pre is not None or high is not None or low is not None:
                radio.set_filters(pre=pre, high=high, low=low)
            _wait_for_signal()
    finally:
        connector.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
