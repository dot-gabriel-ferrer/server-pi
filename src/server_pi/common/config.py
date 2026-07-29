"""Application configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "server-pi-api"
    app_env: str = "dev"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None

    influx_url: str = "http://influxdb:8086"
    influx_token: str = "server-pi-token"
    influx_org: str = "server-pi"
    influx_bucket: str = "cultivo"

    default_zone: str = "greenhouse"
    actuator_default_timeout_sec: int = 120

    camera_id: str = "xiaomi-main"
    camera_zone: str = "greenhouse"
    camera_rtsp_url: str | None = None
    camera_snapshot_source_url: str | None = None
    camera_snapshot_enabled: bool = False
    camera_snapshot_interval_sec: int = 300
    camera_snapshot_retention_days: int = 30
    camera_snapshot_dir: Path = Field(default=Path("/data/snapshots"))

    state_file: Path = Field(default=Path("/data/state/state.json"))


settings = Settings()
