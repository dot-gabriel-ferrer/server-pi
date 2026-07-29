"""BLE parser registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


@dataclass(slots=True)
class ParsedBlePayload:
    """Normalized BLE payload."""

    payload: dict[str, object]
    signature: str


class BleParser(ABC):
    """Abstract BLE parser contract."""

    @abstractmethod
    def supports(self, device: BLEDevice, ad: AdvertisementData) -> bool:
        """Whether parser can parse the advertisement."""

    @abstractmethod
    def parse(self, device: BLEDevice, ad: AdvertisementData, zone: str) -> ParsedBlePayload | None:
        """Parse BLE packet into normalized sensor payload."""


class GenericEnvironmentalParser(BleParser):
    """Generic BLE parser with extensible manufacturer handling."""

    def supports(self, device: BLEDevice, ad: AdvertisementData) -> bool:
        return bool(ad.service_data or ad.manufacturer_data or device.name)

    def parse(self, device: BLEDevice, ad: AdvertisementData, zone: str) -> ParsedBlePayload | None:
        manufacturer = _parse_manufacturer_data(ad.manufacturer_data)
        payload = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "device_id": device.address.replace(":", "-").lower(),
            "zone": zone,
            "type": "ble",
            "rssi_dbm": ad.rssi,
            "battery_pct": manufacturer.get("battery_pct"),
            "temperature_c": manufacturer.get("temperature_c"),
            "humidity_pct": manufacturer.get("humidity_pct"),
            "soil_moisture_pct": manufacturer.get("soil_moisture_pct"),
        }
        signature = sha1(str(sorted(payload.items())).encode("utf-8")).hexdigest()
        return ParsedBlePayload(payload=payload, signature=signature)


def _parse_manufacturer_data(data: dict[int, bytes]) -> dict[str, float | int]:
    """Parse known manufacturer payload fragments.

    The parser is intentionally conservative; unsupported binary formats
    return an empty dictionary and can be extended per vendor later.
    """
    if not data:
        return {}

    parsed: dict[str, float | int] = {}
    for company_id, raw in data.items():
        if company_id == 0x0157 and len(raw) >= 5:
            # Common Xiaomi-like layout used by some LYWSD variants.
            temperature_raw = int.from_bytes(raw[0:2], byteorder="little", signed=True)
            humidity_raw = raw[2]
            battery_raw = raw[4]
            parsed["temperature_c"] = round(temperature_raw / 10.0, 2)
            parsed["humidity_pct"] = int(humidity_raw)
            parsed["battery_pct"] = int(min(max(battery_raw, 0), 100))
    return parsed


PARSERS: list[BleParser] = [GenericEnvironmentalParser()]
