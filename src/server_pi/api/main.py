"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server_pi.api.app_state import AppState, run_periodic_tasks
from server_pi.api.camera import CameraService
from server_pi.common.config import settings
from server_pi.common.models import ActuatorCommandRequest, ActuatorState, EventRecord, EventType, IrrigationRule, SensorReading
from server_pi.common.mqtt_client import MqttPublisher
from server_pi.common.storage import InfluxTimeSeriesRepository, InMemoryTimeSeriesRepository, StateRepository, TimeSeriesRepository
from server_pi.rules_engine.engine import IrrigationRuleEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("server_pi.api")


def _build_repo() -> TimeSeriesRepository:
    try:
        return InfluxTimeSeriesRepository(
            url=settings.influx_url,
            token=settings.influx_token,
            org=settings.influx_org,
            bucket=settings.influx_bucket,
        )
    except Exception:  # noqa: BLE001
        logger.exception("influx_init_failed_fallback_memory")
        return InMemoryTimeSeriesRepository()


def _sensor_message_handler(runtime: AppState):
    def handler(topic: str, payload: dict[str, object]) -> None:
        del topic
        try:
            runtime.ingest_sensor_payload(payload)
        except Exception as exc:  # noqa: BLE001
            runtime.timeseries.write_event(
                EventRecord(
                    ts=datetime.now(timezone.utc),
                    zone=str(payload.get("zone", settings.default_zone)),
                    device_id=str(payload.get("device_id", "unknown")),
                    type=EventType.error,
                    level="error",
                    message="invalid sensor payload",
                    actor="mqtt-consumer",
                    metadata={"error": str(exc)},
                )
            )

    return handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifecycle hook."""
    state_repo = StateRepository(settings.state_file)
    publisher = MqttPublisher(
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_username,
        settings.mqtt_password,
    )
    camera = CameraService(
        camera_id=settings.camera_id,
        zone=settings.camera_zone,
        rtsp_url=settings.camera_rtsp_url,
        snapshot_source_url=settings.camera_snapshot_source_url,
        snapshot_dir=settings.camera_snapshot_dir,
        snapshot_retention_days=settings.camera_snapshot_retention_days,
    )
    runtime = AppState(
        publisher=publisher,
        timeseries=_build_repo(),
        state_repo=state_repo,
        camera=camera,
        rule_engine=IrrigationRuleEngine(),
        default_timeout_sec=settings.actuator_default_timeout_sec,
    )
    publisher.subscribe_json("cultivo/+/sensor/+/state", _sensor_message_handler(runtime))
    app.state.runtime = runtime
    app.mount("/snapshots", StaticFiles(directory=settings.camera_snapshot_dir), name="snapshots")
    worker = asyncio.create_task(run_periodic_tasks(app.state.runtime, interval_sec=settings.camera_snapshot_interval_sec))
    try:
        yield
    finally:
        worker.cancel()
        publisher.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_runtime() -> AppState:
    """FastAPI dependency for runtime state."""
    return app.state.runtime


@app.get("/health")
def health() -> dict[str, str]:
    """Health endpoint."""
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/sensors/latest")
def sensors_latest(zone: str = Query(..., min_length=1), runtime: AppState = Depends(get_runtime)) -> list[SensorReading]:
    """Return latest sensor values for zone."""
    return runtime.timeseries.get_latest_by_zone(zone)


@app.get("/api/v1/sensors/history")
def sensors_history(
    device_id: str = Query(..., min_length=1),
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
    interval: str | None = Query(None),
    runtime: AppState = Depends(get_runtime),
) -> list[SensorReading]:
    """Return historical sensor values in UTC range."""
    if to_ts <= from_ts:
        raise HTTPException(status_code=422, detail="'to' must be greater than 'from'")
    return runtime.timeseries.get_history(device_id=device_id, from_ts=from_ts, to_ts=to_ts, interval=interval)


@app.post("/api/v1/sensors/ingest", status_code=202)
def sensors_ingest(reading: SensorReading, runtime: AppState = Depends(get_runtime)) -> dict[str, str]:
    """Ingest normalized sensor message."""
    runtime.ingest_sensor(reading)
    return {"status": "accepted"}


@app.post("/api/v1/actuators/{actuator_id}/command")
def actuator_command(
    actuator_id: str,
    command: ActuatorCommandRequest,
    runtime: AppState = Depends(get_runtime),
) -> ActuatorState:
    """Send manual/automatic command to actuator."""
    if command.action.value == "on" and command.duration_sec == 0:
        raise HTTPException(status_code=422, detail="duration_sec must be > 0 when action is on")
    try:
        return runtime.command_actuator(actuator_id, command)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/actuators/{actuator_id}/state")
def actuator_state(actuator_id: str, zone: str = Query(..., min_length=1), runtime: AppState = Depends(get_runtime)) -> ActuatorState:
    """Get current actuator state."""
    state = runtime.state_repo.get_actuator_state(actuator_id)
    if state is None:
        state = runtime.ensure_default_off(actuator_id, zone)
    return state


@app.post("/api/v1/rules/irrigation")
def rules_irrigation(
    actuator_id: str = Query("irrigation-main", min_length=1),
    rule: IrrigationRule | None = None,
    runtime: AppState = Depends(get_runtime),
) -> IrrigationRule:
    """Create or update irrigation rule."""
    if rule is None:
        raise HTTPException(status_code=422, detail="rule payload is required")
    return runtime.upsert_rule(actuator_id, rule)


@app.get("/api/v1/events")
def events(runtime: AppState = Depends(get_runtime), limit: int = Query(200, ge=1, le=1000)) -> list[EventRecord]:
    """Return event timeline."""
    return runtime.timeseries.get_events(limit=limit)


@app.get("/api/v1/camera")
def camera_info(runtime: AppState = Depends(get_runtime)) -> JSONResponse:
    """Return configured camera stream/snapshot metadata."""
    return JSONResponse(content=runtime.camera.info().model_dump(mode="json"))
