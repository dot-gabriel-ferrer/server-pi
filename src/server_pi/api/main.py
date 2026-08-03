"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

try:
    import psutil

    HAS_PSUTIL = True
except Exception:  # noqa: BLE001
    psutil = None
    HAS_PSUTIL = False

from server_pi.api.app_state import AppState, run_periodic_tasks
from server_pi.api.camera import CameraService
from server_pi.common.config import settings
from server_pi.common.models import (
    ActuatorCommandRequest,
    ActuatorMode,
    ActuatorState,
    CommandAction,
    EventRecord,
    EventType,
    IrrigationRule,
    SensorReading,
)
from server_pi.common.mqtt_client import MqttPublisher
from server_pi.common.storage import (
    InfluxTimeSeriesRepository,
    InMemoryTimeSeriesRepository,
    StateRepository,
    TimeSeriesRepository,
)
from server_pi.rules_engine.engine import IrrigationRuleEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("server_pi.api")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class SystemProcess(BaseModel):
    """Top process usage details."""

    pid: int
    name: str
    cpu_percent: float
    mem_mb: float
    status: str


class SystemStats(BaseModel):
    """System resource snapshot."""

    cpu_percent: float = 0.0
    cpu_count: int = 0
    memory_total_gb: float = 0.0
    memory_used_gb: float = 0.0
    memory_percent: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_percent: float = 0.0
    net_bytes_sent_mb: float = 0.0
    net_bytes_recv_mb: float = 0.0
    uptime_seconds: int = 0
    load_avg: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    processes: list[SystemProcess] = Field(default_factory=list)


def _round_metric(value: object, digits: int = 1) -> float:
    try:
        return round(float(value), digits)
    except Exception:  # noqa: BLE001
        return 0.0


def _format_uptime(uptime_seconds: int) -> str:
    days, remainder = divmod(max(uptime_seconds, 0), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _empty_system_stats() -> SystemStats:
    return SystemStats()


def _get_system_stats() -> SystemStats:
    stats = _empty_system_stats()
    if not HAS_PSUTIL or psutil is None:
        return stats

    try:
        stats.cpu_percent = _round_metric(psutil.cpu_percent(interval=None))
        stats.cpu_count = psutil.cpu_count() or 0
    except Exception:  # noqa: BLE001
        logger.exception("system_stats_cpu_failed")

    try:
        memory = psutil.virtual_memory()
        stats.memory_total_gb = _round_metric(memory.total / 1024**3)
        stats.memory_used_gb = _round_metric(memory.used / 1024**3)
        stats.memory_percent = _round_metric(memory.percent)
    except Exception:  # noqa: BLE001
        logger.exception("system_stats_memory_failed")

    try:
        disk = psutil.disk_usage("/")
        stats.disk_total_gb = _round_metric(disk.total / 1024**3)
        stats.disk_used_gb = _round_metric(disk.used / 1024**3)
        stats.disk_percent = _round_metric(disk.percent)
    except Exception:  # noqa: BLE001
        logger.exception("system_stats_disk_failed")

    try:
        network = psutil.net_io_counters()
        stats.net_bytes_sent_mb = _round_metric(network.bytes_sent / 1024**2)
        stats.net_bytes_recv_mb = _round_metric(network.bytes_recv / 1024**2)
    except Exception:  # noqa: BLE001
        logger.exception("system_stats_network_failed")

    try:
        stats.uptime_seconds = int(max(datetime.now(UTC).timestamp() - psutil.boot_time(), 0))
    except Exception:  # noqa: BLE001
        logger.exception("system_stats_uptime_failed")

    try:
        stats.load_avg = [_round_metric(value) for value in psutil.getloadavg()]
    except Exception:  # noqa: BLE001
        stats.load_avg = [0.0, 0.0, 0.0]

    try:
        processes: list[SystemProcess] = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
            try:
                info = proc.info
                memory_info = info.get("memory_info")
                mem_mb = 0.0
                if memory_info is not None:
                    mem_mb = _round_metric(getattr(memory_info, "rss", 0) / 1024**2)
                processes.append(
                    SystemProcess(
                        pid=int(info.get("pid") or 0),
                        name=str(info.get("name") or "unknown"),
                        cpu_percent=_round_metric(info.get("cpu_percent") or 0.0),
                        mem_mb=mem_mb,
                        status=str(info.get("status") or "unknown"),
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        stats.processes = sorted(
            processes, key=lambda process: process.cpu_percent, reverse=True
        )[:15]
    except Exception:  # noqa: BLE001
        logger.exception("system_stats_processes_failed")

    return stats


def _serialize_container(container: object) -> dict[str, str]:
    image = getattr(container, "image", None)
    tags = getattr(image, "tags", []) if image is not None else []
    return {
        "name": str(getattr(container, "name", "unknown")),
        "status": str(getattr(container, "status", "unknown")),
        "image": tags[0] if tags else "unknown",
        "id": str(getattr(container, "short_id", "unknown")),
    }


def _get_services() -> list[dict[str, str]]:
    try:
        import docker

        client = docker.from_env()
        try:
            containers = client.containers.list(all=True)
            return [_serialize_container(container) for container in containers]
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception:  # noqa: BLE001
        return []


def _control_service(container_name: str, action: str) -> dict[str, str]:
    try:
        import docker

        client = docker.from_env()
        try:
            container = client.containers.get(container_name)
            getattr(container, action)()
            container.reload()
            return _serialize_container(container)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Docker service unavailable") from exc


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
                    ts=datetime.now(UTC),
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
    worker = asyncio.create_task(
        run_periodic_tasks(app.state.runtime, interval_sec=settings.camera_snapshot_interval_sec)
    )
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
    return {"status": "ok", "ts": datetime.now(UTC).isoformat()}


@app.get("/", response_class=HTMLResponse)
def portal(request: Request) -> HTMLResponse:
    """Render minimal local portal UI."""
    return templates.TemplateResponse(
        request=request, name="home.html", context={"request": request}
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Render dashboard UI."""
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={"request": request}
    )


@app.get("/resources", response_class=HTMLResponse)
def resources(request: Request) -> HTMLResponse:
    """Render resources page."""
    return templates.TemplateResponse(
        request=request, name="resources.html", context={"request": request}
    )


@app.get("/api/v1/system/stats", response_model=SystemStats)
def system_stats() -> SystemStats:
    """Return host system statistics."""
    return _get_system_stats()


@app.get("/api/v1/services")
def services_status() -> list[dict[str, str]]:
    """Return Docker service status list."""
    return _get_services()


@app.post("/api/v1/services/{container_name}/start")
def service_start(container_name: str) -> dict[str, str]:
    """Start a Docker service."""
    return _control_service(container_name, "start")


@app.post("/api/v1/services/{container_name}/stop")
def service_stop(container_name: str) -> dict[str, str]:
    """Stop a Docker service."""
    return _control_service(container_name, "stop")


@app.post("/api/v1/services/{container_name}/restart")
def service_restart(container_name: str) -> dict[str, str]:
    """Restart a Docker service."""
    return _control_service(container_name, "restart")


@app.get("/api/v1/sensors/latest")
def sensors_latest(
    zone: str = Query(..., min_length=1), runtime: AppState = Depends(get_runtime)
) -> list[SensorReading]:
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
    return runtime.timeseries.get_history(
        device_id=device_id, from_ts=from_ts, to_ts=to_ts, interval=interval
    )


@app.post("/api/v1/sensors/ingest", status_code=202)
def sensors_ingest(
    reading: SensorReading, runtime: AppState = Depends(get_runtime)
) -> dict[str, str]:
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
def actuator_state(
    actuator_id: str, zone: str = Query(..., min_length=1), runtime: AppState = Depends(get_runtime)
) -> ActuatorState:
    """Get current actuator state."""
    state = runtime.state_repo.get_actuator_state(actuator_id)
    if state is None:
        state = runtime.ensure_default_off(actuator_id, zone)
    return state


@app.get("/api/v1/rules/irrigation")
def rules_irrigation_get(
    actuator_id: str = Query("irrigation-main", min_length=1),
    runtime: AppState = Depends(get_runtime),
) -> IrrigationRule | None:
    """Fetch current irrigation rule for an actuator."""
    return runtime.state_repo.get_rule(actuator_id)


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
def events(
    runtime: AppState = Depends(get_runtime), limit: int = Query(200, ge=1, le=1000)
) -> list[EventRecord]:
    """Return event timeline."""
    return runtime.timeseries.get_events(limit=limit)


@app.get("/api/v1/camera")
def camera_info(runtime: AppState = Depends(get_runtime)) -> JSONResponse:
    """Return configured camera stream/snapshot metadata."""
    return JSONResponse(content=runtime.camera.info().model_dump(mode="json"))


# ---------------------------------------------------------------------------
# UI partial routes (HTMX fragments)
# ---------------------------------------------------------------------------


@app.get("/ui/partials/sensors", response_class=HTMLResponse)
def ui_partial_sensors(
    request: Request,
    zone: str = Query(..., min_length=1),
    compact: bool = Query(False),
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Render sensor readings card fragment for HTMX polling."""
    readings = runtime.timeseries.get_latest_by_zone(zone)
    return templates.TemplateResponse(
        request=request,
        name="partials/sensors.html",
        context={
            "request": request,
            "readings": readings,
            "zone": zone,
            "now": datetime.now(UTC),
            "compact": compact,
        },
    )


@app.get("/ui/partials/actuator", response_class=HTMLResponse)
def ui_partial_actuator(
    request: Request,
    actuator_id: str = Query(..., min_length=1),
    zone: str = Query(..., min_length=1),
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Render actuator status card fragment for HTMX polling."""
    state = runtime.state_repo.get_actuator_state(actuator_id) or runtime.ensure_default_off(
        actuator_id, zone
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/actuator.html",
        context={"request": request, "state": state, "actuator_id": actuator_id, "zone": zone},
    )


@app.get("/ui/partials/events", response_class=HTMLResponse)
def ui_partial_events(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Render recent events list fragment for HTMX polling."""
    events = runtime.timeseries.get_events(limit=limit)
    return templates.TemplateResponse(
        request=request,
        name="partials/events.html",
        context={"request": request, "events": events, "limit": limit},
    )


@app.get("/ui/partials/system-stats", response_class=HTMLResponse)
def ui_partial_system_stats(request: Request) -> HTMLResponse:
    """Render compact system stats fragment for HTMX polling."""
    stats = _get_system_stats()
    return templates.TemplateResponse(
        request=request,
        name="partials/system_stats.html",
        context={
            "request": request,
            "stats": stats,
            "uptime_label": _format_uptime(stats.uptime_seconds),
        },
    )


@app.get("/ui/partials/services", response_class=HTMLResponse)
def ui_partial_services(request: Request) -> HTMLResponse:
    """Render Docker services fragment for HTMX polling."""
    return templates.TemplateResponse(
        request=request,
        name="partials/services.html",
        context={"request": request, "services": _get_services()},
    )


@app.get("/ui/partials/camera", response_class=HTMLResponse)
def ui_partial_camera(
    request: Request,
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Render camera card fragment for HTMX polling."""
    camera_info_obj = runtime.camera.info()
    return templates.TemplateResponse(
        request=request,
        name="partials/camera.html",
        context={
            "request": request,
            "camera": camera_info_obj,
            "now_ts": int(datetime.now(UTC).timestamp()),
        },
    )


@app.get("/ui/partials/rule", response_class=HTMLResponse)
def ui_partial_rule(
    request: Request,
    actuator_id: str = Query("irrigation-main", min_length=1),
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Render irrigation rule card fragment for HTMX polling."""
    rule = runtime.state_repo.get_rule(actuator_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/rule.html",
        context={"request": request, "rule": rule, "actuator_id": actuator_id},
    )


# ---------------------------------------------------------------------------
# UI actuator control routes (HTMX form POST → return updated card fragment)
# ---------------------------------------------------------------------------


@app.post("/ui/actuators/{actuator_id}/on", response_class=HTMLResponse)
def ui_actuator_on(
    request: Request,
    actuator_id: str,
    zone: str = Query(..., min_length=1),
    duration_sec: int = Form(30, ge=1, le=3600),
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Activate actuator via UI and return updated card fragment."""
    now = datetime.now(UTC)
    try:
        state = runtime.command_actuator(
            actuator_id,
            ActuatorCommandRequest(
                ts=now,
                zone=zone,
                actor="portal-ui",
                reason="manual button",
                action=CommandAction.on,
                duration_sec=duration_sec,
                mode=ActuatorMode.manual,
            ),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="partials/actuator.html",
        context={"request": request, "state": state, "actuator_id": actuator_id, "zone": zone},
    )


@app.post("/ui/actuators/{actuator_id}/off", response_class=HTMLResponse)
def ui_actuator_off(
    request: Request,
    actuator_id: str,
    zone: str = Query(..., min_length=1),
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Deactivate actuator via UI and return updated card fragment."""
    now = datetime.now(UTC)
    state = runtime.command_actuator(
        actuator_id,
        ActuatorCommandRequest(
            ts=now,
            zone=zone,
            actor="portal-ui",
            reason="manual stop",
            action=CommandAction.off,
            duration_sec=0,
            mode=ActuatorMode.manual,
        ),
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/actuator.html",
        context={"request": request, "state": state, "actuator_id": actuator_id, "zone": zone},
    )


@app.post("/ui/rules/{actuator_id}", response_class=HTMLResponse)
def ui_rule_update(
    request: Request,
    actuator_id: str,
    zone: str = Query(..., min_length=1),
    enabled: bool = Query(True),
    soil_moisture_threshold_pct: float = Query(..., ge=0, le=100),
    allowed_start_hour_utc: int = Query(..., ge=0, le=23),
    allowed_end_hour_utc: int = Query(..., ge=0, le=23),
    cooldown_minutes: int = Query(..., ge=1, le=1440),
    max_duration_sec: int = Query(..., ge=1, le=3600),
    telemetry_timeout_minutes: int = Query(..., ge=1, le=1440),
    runtime: AppState = Depends(get_runtime),
) -> HTMLResponse:
    """Update irrigation rule via UI form and return updated card fragment."""
    existing = runtime.state_repo.get_rule(actuator_id)
    if existing is not None:
        rule = existing.model_copy(
            update={
                "zone": zone,
                "enabled": enabled,
                "soil_moisture_threshold_pct": soil_moisture_threshold_pct,
                "allowed_start_hour_utc": allowed_start_hour_utc,
                "allowed_end_hour_utc": allowed_end_hour_utc,
                "cooldown_minutes": cooldown_minutes,
                "max_duration_sec": max_duration_sec,
                "telemetry_timeout_minutes": telemetry_timeout_minutes,
            }
        )
    else:
        rule = IrrigationRule(
            zone=zone,
            enabled=enabled,
            soil_moisture_threshold_pct=soil_moisture_threshold_pct,
            allowed_start_hour_utc=allowed_start_hour_utc,
            allowed_end_hour_utc=allowed_end_hour_utc,
            cooldown_minutes=cooldown_minutes,
            max_duration_sec=max_duration_sec,
            telemetry_timeout_minutes=telemetry_timeout_minutes,
        )
    runtime.upsert_rule(actuator_id, rule)
    return templates.TemplateResponse(
        request=request,
        name="partials/rule.html",
        context={"request": request, "rule": rule, "actuator_id": actuator_id, "saved": True},
    )
