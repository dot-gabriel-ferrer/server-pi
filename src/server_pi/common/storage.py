"""Persistence adapters for time-series and state."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from server_pi.common.models import ActuatorState, EventRecord, IrrigationRule, SensorReading


class TimeSeriesRepository(ABC):
    """Abstract time-series repository."""

    @abstractmethod
    def write_sensor(self, reading: SensorReading) -> None:
        """Persist a sensor reading."""

    @abstractmethod
    def get_latest_by_zone(self, zone: str) -> list[SensorReading]:
        """Fetch latest sensor readings by zone."""

    @abstractmethod
    def get_history(
        self,
        device_id: str,
        from_ts: datetime,
        to_ts: datetime,
        interval: str | None,
    ) -> list[SensorReading]:
        """Fetch historical sensor readings."""

    @abstractmethod
    def write_event(self, event: EventRecord) -> None:
        """Persist an event."""

    @abstractmethod
    def get_events(self, limit: int = 200) -> list[EventRecord]:
        """Fetch latest events."""


class InfluxTimeSeriesRepository(TimeSeriesRepository):
    """InfluxDB-backed time-series repository."""

    def __init__(self, url: str, token: str, org: str, bucket: str) -> None:
        self._org = org
        self._bucket = bucket
        self._client = InfluxDBClient(url=url, token=token, org=org)
        self._write = self._client.write_api(write_options=SYNCHRONOUS)
        self._query = self._client.query_api()

    def write_sensor(self, reading: SensorReading) -> None:
        """Persist a sensor reading as measurement."""
        point = Point("sensor")
        point = (
            point.tag("device_id", reading.device_id)
            .tag("zone", reading.zone)
            .tag("type", reading.type.value)
        )
        fields = reading.model_dump()
        for key in (
            "temperature_c",
            "humidity_pct",
            "soil_moisture_pct",
            "rssi_dbm",
            "battery_pct",
        ):
            value = fields.get(key)
            if value is not None:
                point = point.field(key, value)
        point = point.time(reading.ts.astimezone(UTC))
        self._write.write(bucket=self._bucket, org=self._org, record=point)

    def get_latest_by_zone(self, zone: str) -> list[SensorReading]:
        """Fetch latest state for each sensor in zone."""
        query = f"""
from(bucket: "{self._bucket}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "sensor" and r.zone == "{zone}")
  |> group(columns: ["device_id", "_field"])
  |> last()
"""
        tables = self._query.query(query=query, org=self._org)
        by_device: dict[str, dict[str, object]] = defaultdict(dict)
        for table in tables:
            for record in table.records:
                device = str(record.values.get("device_id"))
                by_device[device]["device_id"] = device
                by_device[device]["zone"] = zone
                by_device[device]["type"] = record.values.get("type", "ble")
                by_device[device]["ts"] = record.get_time()
                by_device[device][record.get_field()] = record.get_value()
        output: list[SensorReading] = []
        for data in by_device.values():
            output.append(SensorReading.model_validate(data))
        return sorted(output, key=lambda item: item.ts, reverse=True)

    def get_history(
        self,
        device_id: str,
        from_ts: datetime,
        to_ts: datetime,
        interval: str | None,
    ) -> list[SensorReading]:
        """Fetch readings for a device in time range."""
        aggregate = ""
        if interval:
            aggregate = f"|> aggregateWindow(every: {interval}, fn: mean, createEmpty: false)"
        start_iso = from_ts.astimezone(UTC).isoformat()
        stop_iso = to_ts.astimezone(UTC).isoformat()
        query = f"""
from(bucket: "{self._bucket}")
  |> range(start: time(v: "{start_iso}"), stop: time(v: "{stop_iso}"))
  |> filter(fn: (r) => r._measurement == "sensor" and r.device_id == "{device_id}")
  {aggregate}
"""
        tables = self._query.query(query=query, org=self._org)
        by_ts: dict[str, dict[str, object]] = defaultdict(dict)
        for table in tables:
            for record in table.records:
                key = record.get_time().isoformat()
                bucket = by_ts[key]
                bucket["device_id"] = device_id
                bucket["zone"] = str(record.values.get("zone"))
                bucket["type"] = record.values.get("type", "ble")
                bucket["ts"] = record.get_time()
                bucket[record.get_field()] = record.get_value()
        return [SensorReading.model_validate(item) for _, item in sorted(by_ts.items())]

    def write_event(self, event: EventRecord) -> None:
        """Persist event entries."""
        point = Point("event")
        point = (
            point.tag("zone", event.zone)
            .tag("device_id", event.device_id)
            .tag("type", event.type.value)
        )
        point = point.tag("level", event.level).tag("actor", event.actor)
        point = point.field("message", event.message)
        if event.rule_id:
            point = point.field("rule_id", event.rule_id)
        point = point.field("metadata", json.dumps(event.metadata, sort_keys=True))
        point = point.time(event.ts.astimezone(UTC))
        self._write.write(bucket=self._bucket, org=self._org, record=point)

    def get_events(self, limit: int = 200) -> list[EventRecord]:
        """Return most recent events."""
        query = f"""
from(bucket: "{self._bucket}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "event")
  |> group(columns: ["_time"])
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {limit})
"""
        tables = self._query.query(query=query, org=self._org)
        events: list[EventRecord] = []
        for table in tables:
            for record in table.records:
                metadata_raw = record.values.get("metadata", "{}")
                try:
                    metadata = json.loads(metadata_raw)
                except json.JSONDecodeError:
                    metadata = {}
                events.append(
                    EventRecord.model_validate(
                        {
                            "ts": record.get_time(),
                            "zone": record.values.get("zone"),
                            "device_id": record.values.get("device_id"),
                            "type": record.values.get("type"),
                            "level": record.values.get("level", "info"),
                            "message": record.values.get("message", ""),
                            "actor": record.values.get("actor", "system"),
                            "rule_id": record.values.get("rule_id"),
                            "metadata": metadata,
                        }
                    )
                )
        return events


class InMemoryTimeSeriesRepository(TimeSeriesRepository):
    """In-memory repository for tests/local fallback."""

    def __init__(self) -> None:
        self.sensors: list[SensorReading] = []
        self.events: list[EventRecord] = []

    def write_sensor(self, reading: SensorReading) -> None:
        self.sensors.append(reading)

    def get_latest_by_zone(self, zone: str) -> list[SensorReading]:
        latest: dict[str, SensorReading] = {}
        for reading in self.sensors:
            if reading.zone != zone:
                continue
            previous = latest.get(reading.device_id)
            if previous is None or reading.ts > previous.ts:
                latest[reading.device_id] = reading
        return sorted(latest.values(), key=lambda item: item.ts, reverse=True)

    def get_history(
        self,
        device_id: str,
        from_ts: datetime,
        to_ts: datetime,
        interval: str | None,
    ) -> list[SensorReading]:
        del interval
        return [
            reading
            for reading in self.sensors
            if reading.device_id == device_id and from_ts <= reading.ts <= to_ts
        ]

    def write_event(self, event: EventRecord) -> None:
        self.events.append(event)

    def get_events(self, limit: int = 200) -> list[EventRecord]:
        return list(reversed(self.events[-limit:]))


class StateRepository:
    """JSON-backed persistence for rules and actuator states."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_raw(
                {"actuators": {}, "rules": {}, "last_irrigation": {}, "last_telemetry": {}}
            )

    def _read_raw(self) -> dict[str, object]:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write_raw(self, payload: dict[str, object]) -> None:
        self._path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    def set_actuator_state(self, state: ActuatorState) -> None:
        data = self._read_raw()
        actuators = data["actuators"]
        assert isinstance(actuators, dict)
        actuators[state.actuator_id] = state.model_dump(mode="json")
        self._write_raw(data)

    def get_actuator_state(self, actuator_id: str) -> ActuatorState | None:
        data = self._read_raw()
        actuators = data["actuators"]
        assert isinstance(actuators, dict)
        payload = actuators.get(actuator_id)
        if not payload:
            return None
        return ActuatorState.model_validate(payload)

    def set_rule(self, actuator_id: str, rule: IrrigationRule) -> None:
        data = self._read_raw()
        rules = data["rules"]
        assert isinstance(rules, dict)
        rules[actuator_id] = rule.model_dump(mode="json")
        self._write_raw(data)

    def get_rule(self, actuator_id: str) -> IrrigationRule | None:
        data = self._read_raw()
        rules = data["rules"]
        assert isinstance(rules, dict)
        payload = rules.get(actuator_id)
        if not payload:
            return None
        return IrrigationRule.model_validate(payload)

    def set_last_irrigation(self, actuator_id: str, timestamp: datetime) -> None:
        data = self._read_raw()
        bucket = data["last_irrigation"]
        assert isinstance(bucket, dict)
        bucket[actuator_id] = timestamp.astimezone(UTC).isoformat()
        self._write_raw(data)

    def get_last_irrigation(self, actuator_id: str) -> datetime | None:
        data = self._read_raw()
        bucket = data["last_irrigation"]
        assert isinstance(bucket, dict)
        value = bucket.get(actuator_id)
        return datetime.fromisoformat(value) if value else None

    def set_last_telemetry(self, zone: str, timestamp: datetime) -> None:
        data = self._read_raw()
        bucket = data["last_telemetry"]
        assert isinstance(bucket, dict)
        bucket[zone] = timestamp.astimezone(UTC).isoformat()
        self._write_raw(data)

    def get_last_telemetry(self, zone: str) -> datetime | None:
        data = self._read_raw()
        bucket = data["last_telemetry"]
        assert isinstance(bucket, dict)
        value = bucket.get(zone)
        return datetime.fromisoformat(value) if value else None
