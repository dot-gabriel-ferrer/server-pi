"""Domain models for API and services."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SensorType(StrEnum):
    """Supported sensor types."""

    ble = "ble"
    zigbee = "zigbee"


class EventType(StrEnum):
    """Event types in the platform."""

    irrigation = "irrigation"
    alert = "alert"
    error = "error"
    camera = "camera"
    actuator = "actuator"


class SensorReading(BaseModel):
    """Normalized sensor reading payload."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    device_id: str = Field(min_length=1, max_length=120)
    zone: str = Field(min_length=1, max_length=80)
    type: SensorType
    temperature_c: float | None = None
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    soil_moisture_pct: float | None = Field(default=None, ge=0, le=100)
    rssi_dbm: int | None = None
    battery_pct: int | None = Field(default=None, ge=0, le=100)


class ActuatorMode(StrEnum):
    """Actuator operation mode."""

    manual = "manual"
    automatic = "automatic"


class CommandAction(StrEnum):
    """Allowed actuator command actions."""

    on = "on"
    off = "off"


class ActuatorCommandRequest(BaseModel):
    """Actuator command input payload."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    zone: str = Field(min_length=1, max_length=80)
    actor: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)
    action: CommandAction
    duration_sec: int = Field(default=0, ge=0, le=3600)
    mode: ActuatorMode = ActuatorMode.manual


class ActuatorState(BaseModel):
    """Current actuator state."""

    model_config = ConfigDict(extra="forbid")

    actuator_id: str
    zone: str
    state: CommandAction
    mode: ActuatorMode
    updated_at: datetime
    last_actor: str
    safety_timeout_sec: int = Field(ge=1, le=3600)


class WeatherCondition(BaseModel):
    """Optional ambient/weather gate for multi-condition irrigation rules."""

    model_config = ConfigDict(extra="forbid")

    max_ambient_temp_c: float | None = Field(default=None, ge=-20, le=60)
    max_ambient_humidity_pct: float | None = Field(default=None, ge=0, le=100)


class IrrigationRule(BaseModel):
    """Multi-condition irrigation rule."""

    model_config = ConfigDict(extra="forbid")

    zone: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    soil_moisture_threshold_pct: float = Field(ge=0, le=100)
    allowed_start_hour_utc: int = Field(ge=0, le=23)
    allowed_end_hour_utc: int = Field(ge=0, le=23)
    cooldown_minutes: int = Field(ge=1, le=24 * 60)
    max_duration_sec: int = Field(ge=1, le=3600)
    telemetry_timeout_minutes: int = Field(ge=1, le=24 * 60)
    weather: WeatherCondition = Field(default_factory=WeatherCondition)
    temp_sensor_device_id: str | None = None

    @field_validator("allowed_end_hour_utc")
    @classmethod
    def validate_window_not_equal(cls, value: int, info: object) -> int:
        """Prevent empty windows.

        Args:
            value: End hour.
            info: Validation context.

        Returns:
            int: Validated end hour.
        """
        start = getattr(info, "data", {}).get("allowed_start_hour_utc")
        if start is not None and value == start:
            raise ValueError("allowed_end_hour_utc must differ from allowed_start_hour_utc")
        return value


class EventRecord(BaseModel):
    """Event/audit record."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    zone: str = Field(min_length=1, max_length=80)
    device_id: str = Field(min_length=1, max_length=120)
    type: EventType
    level: str = Field(min_length=1, max_length=20)
    message: str = Field(min_length=1, max_length=2000)
    actor: str = Field(default="system", min_length=1, max_length=80)
    rule_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class CameraInfo(BaseModel):
    """Camera stream and snapshot metadata."""

    model_config = ConfigDict(extra="forbid")

    camera_id: str
    zone: str
    stream_url: str | None = None
    latest_snapshot_url: str | None = None
    latest_snapshot_ts: datetime | None = None
