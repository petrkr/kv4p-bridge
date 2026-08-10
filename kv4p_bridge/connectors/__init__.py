"""Connector factory."""

from __future__ import annotations

from kv4p_bridge.config import ConnectorConfig

from .base import Connector
from .dummy import create_dummy_connector


def create_connector(config: ConnectorConfig) -> Connector:
    """Create configured connector."""
    if config.type == "dummy":
        return create_dummy_connector(config.options)
    raise ValueError(f"unknown connector type: {config.type}")
