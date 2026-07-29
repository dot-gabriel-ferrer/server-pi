"""BLE collector service entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from paho.mqtt.client import CallbackAPIVersion, Client

from server_pi.ble_collector.parsers import PARSERS, ParsedBlePayload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("server_pi.ble_collector")


@dataclass(slots=True)
class CollectorConfig:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    zone: str
    scan_interval_sec: int
    min_publish_interval_sec: int


class BleCollector:
    """Scans BLE packets and publishes normalized MQTT messages."""

    def __init__(self, config: CollectorConfig) -> None:
        self._config = config
        self._client = Client(callback_api_version=CallbackAPIVersion.VERSION2)
        if config.mqtt_username and config.mqtt_password:
            self._client.username_pw_set(config.mqtt_username, config.mqtt_password)
        self._last_signatures: dict[str, str] = {}
        self._last_publish_at: dict[str, datetime] = {}

    def start(self) -> None:
        """Start MQTT loop and collector loop."""
        self._client.connect(self._config.mqtt_host, self._config.mqtt_port, 30)
        self._client.loop_start()
        asyncio.run(self._run())

    async def _run(self) -> None:
        while True:
            devices = await BleakScanner.discover(timeout=self._config.scan_interval_sec, return_adv=True)
            self._process_scan(devices)
            await asyncio.sleep(self._config.scan_interval_sec)

    def _process_scan(self, devices: dict[str, tuple[BLEDevice, AdvertisementData]]) -> None:
        for _, (device, advertisement) in devices.items():
            parsed = self._parse_device(device, advertisement)
            if parsed is None:
                continue
            device_id = str(parsed.payload["device_id"])
            if not self._should_publish(device_id, parsed):
                continue
            topic = f"cultivo/{self._config.zone}/sensor/{device_id}/state"
            self._client.publish(topic, json.dumps(parsed.payload, sort_keys=True), qos=1, retain=False)
            self._last_signatures[device_id] = parsed.signature
            self._last_publish_at[device_id] = datetime.now(tz=timezone.utc)

    def _parse_device(self, device: BLEDevice, advertisement: AdvertisementData) -> ParsedBlePayload | None:
        for parser in PARSERS:
            if parser.supports(device, advertisement):
                return parser.parse(device, advertisement, self._config.zone)
        return None

    def _should_publish(self, device_id: str, parsed: ParsedBlePayload) -> bool:
        now = datetime.now(tz=timezone.utc)
        last_sig = self._last_signatures.get(device_id)
        last_at = self._last_publish_at.get(device_id)
        if last_sig == parsed.signature and last_at is not None:
            return now - last_at >= timedelta(seconds=self._config.min_publish_interval_sec)
        return True


def _config_from_env() -> CollectorConfig:
    return CollectorConfig(
        mqtt_host=os.getenv("MQTT_HOST", "mosquitto"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        mqtt_username=os.getenv("MQTT_USERNAME"),
        mqtt_password=os.getenv("MQTT_PASSWORD"),
        zone=os.getenv("DEFAULT_ZONE", "greenhouse"),
        scan_interval_sec=int(os.getenv("BLE_SCAN_INTERVAL_SEC", "10")),
        min_publish_interval_sec=int(os.getenv("BLE_MIN_PUBLISH_INTERVAL_SEC", "30")),
    )


if __name__ == "__main__":
    BleCollector(_config_from_env()).start()
