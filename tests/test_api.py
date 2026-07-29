from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from server_pi.api.app_state import AppState
from server_pi.api.camera import CameraService
from server_pi.api.main import app, get_runtime
from server_pi.common.models import ActuatorMode, CommandAction
from server_pi.common.storage import InMemoryTimeSeriesRepository, StateRepository
from server_pi.rules_engine.engine import IrrigationRuleEngine


class DummyPublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object], bool]] = []

    def publish_json(self, topic: str, payload: dict[str, object], retain: bool = False) -> None:
        self.messages.append((topic, payload, retain))


def make_runtime(tmp_path: Path) -> AppState:
    return AppState(
        publisher=DummyPublisher(),
        timeseries=InMemoryTimeSeriesRepository(),
        state_repo=StateRepository(tmp_path / "state.json"),
        camera=CameraService(
            camera_id="cam-1",
            zone="greenhouse",
            rtsp_url="rtsp://example.local/live",
            snapshot_source_url=None,
            snapshot_dir=tmp_path / "snapshots",
            snapshot_retention_days=30,
        ),
        rule_engine=IrrigationRuleEngine(),
        default_timeout_sec=90,
    )


def test_actuator_command_endpoint(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    app.dependency_overrides[get_runtime] = lambda: runtime
    client = TestClient(app)

    response = client.post(
        "/api/v1/actuators/irrigation-main/command",
        json={
            "ts": datetime.now(timezone.utc).isoformat(),
            "zone": "greenhouse",
            "actor": "tester",
            "reason": "manual run",
            "action": "on",
            "duration_sec": 15,
            "mode": "manual",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == CommandAction.on.value
    assert payload["mode"] == ActuatorMode.manual.value


def test_state_repo_persistence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    app.dependency_overrides[get_runtime] = lambda: runtime
    client = TestClient(app)

    first = client.get("/api/v1/actuators/irrigation-main/state", params={"zone": "greenhouse"})
    assert first.status_code == 200
    assert first.json()["state"] == "off"

    second = client.get("/api/v1/actuators/irrigation-main/state", params={"zone": "greenhouse"})
    assert second.status_code == 200
    assert second.json()["state"] == "off"
