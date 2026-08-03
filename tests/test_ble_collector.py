"""Unit tests for the BLE collector: parsers and publish-gate logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from server_pi.ble_collector.main import BleCollector, CollectorConfig
from server_pi.ble_collector.parsers import (
    GenericEnvironmentalParser,
    ParsedBlePayload,
    _parse_manufacturer_data,
)


def _make_device(address: str = "AA:BB:CC:DD:EE:FF", name: str = "LYWSD") -> MagicMock:
    device = MagicMock()
    device.address = address
    device.name = name
    return device


def _make_advertisement(
    rssi: int = -65,
    service_data: dict[str, Any] | None = None,
    manufacturer_data: dict[int, bytes] | None = None,
) -> MagicMock:
    ad = MagicMock()
    ad.rssi = rssi
    ad.service_data = service_data or {}
    ad.manufacturer_data = manufacturer_data or {}
    return ad


# ---------------------------------------------------------------------------
# _parse_manufacturer_data
# ---------------------------------------------------------------------------


def test_parse_manufacturer_data_empty() -> None:
    assert _parse_manufacturer_data({}) == {}


def test_parse_manufacturer_data_unknown_vendor() -> None:
    assert _parse_manufacturer_data({0x9999: b"\x01\x02\x03\x04\x05"}) == {}


def test_parse_manufacturer_data_xiaomi_variant() -> None:
    # Temp = 0x00F0 LE signed = 240 → 24.0 °C, humidity = 0x3C = 60 %, battery = 0x55 = 85 %
    raw = bytes([0xF0, 0x00, 0x3C, 0x00, 0x55])
    result = _parse_manufacturer_data({0x0157: raw})
    assert result["temperature_c"] == 24.0
    assert result["humidity_pct"] == 60
    assert result["battery_pct"] == 85


def test_parse_manufacturer_data_xiaomi_too_short() -> None:
    raw = bytes([0xF0, 0x00, 0x3C])
    assert _parse_manufacturer_data({0x0157: raw}) == {}


# ---------------------------------------------------------------------------
# GenericEnvironmentalParser
# ---------------------------------------------------------------------------


def test_parser_supports_device_with_name() -> None:
    parser = GenericEnvironmentalParser()
    device = _make_device(name="Sensor")
    ad = _make_advertisement()
    assert parser.supports(device, ad) is True


def test_parser_supports_device_with_service_data() -> None:
    parser = GenericEnvironmentalParser()
    device = _make_device(name="")
    device.name = ""
    ad = _make_advertisement(service_data={"0000181a-0000-1000-8000-00805f9b34fb": b"\x01"})
    assert parser.supports(device, ad) is True


def test_parser_returns_payload_with_correct_zone() -> None:
    parser = GenericEnvironmentalParser()
    device = _make_device(address="AA:BB:CC:DD:EE:FF", name="LYWSD")
    ad = _make_advertisement(rssi=-70)
    result = parser.parse(device, ad, zone="outdoor")
    assert result is not None
    assert result.payload["zone"] == "outdoor"
    assert result.payload["device_id"] == "aa-bb-cc-dd-ee-ff"
    assert result.payload["type"] == "ble"
    assert result.payload["rssi_dbm"] == -70


def test_parser_signature_changes_with_different_rssi() -> None:
    parser = GenericEnvironmentalParser()
    device = _make_device()
    ad1 = _make_advertisement(rssi=-60)
    ad2 = _make_advertisement(rssi=-80)
    r1 = parser.parse(device, ad1, zone="greenhouse")
    r2 = parser.parse(device, ad2, zone="greenhouse")
    assert r1 is not None and r2 is not None
    assert r1.signature != r2.signature


# ---------------------------------------------------------------------------
# BleCollector._should_publish
# ---------------------------------------------------------------------------


def _make_collector() -> BleCollector:
    config = CollectorConfig(
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        zone="greenhouse",
        scan_interval_sec=5,
        min_publish_interval_sec=30,
    )
    collector = BleCollector(config)
    collector._client = MagicMock()
    return collector


def _fake_payload(sig: str = "abc") -> ParsedBlePayload:
    return ParsedBlePayload(payload={"device_id": "dev-1"}, signature=sig)


def test_should_publish_first_time() -> None:
    collector = _make_collector()
    assert collector._should_publish("dev-1", _fake_payload("sig1")) is True


def test_should_not_publish_same_sig_within_interval() -> None:
    collector = _make_collector()
    collector._last_signatures["dev-1"] = "sig1"
    collector._last_publish_at["dev-1"] = datetime.now(UTC)
    assert collector._should_publish("dev-1", _fake_payload("sig1")) is False


def test_should_publish_same_sig_after_interval() -> None:
    collector = _make_collector()
    collector._last_signatures["dev-1"] = "sig1"
    collector._last_publish_at["dev-1"] = datetime.now(UTC) - timedelta(seconds=60)
    assert collector._should_publish("dev-1", _fake_payload("sig1")) is True


def test_should_publish_different_sig_regardless_of_interval() -> None:
    collector = _make_collector()
    collector._last_signatures["dev-1"] = "sig1"
    collector._last_publish_at["dev-1"] = datetime.now(UTC)
    assert collector._should_publish("dev-1", _fake_payload("sig2")) is True
