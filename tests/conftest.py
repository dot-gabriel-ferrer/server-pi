"""Shared pytest fixtures for server-pi tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from server_pi.api.app_state import AppState
from server_pi.api.camera import CameraService
from server_pi.common.storage import InMemoryTimeSeriesRepository, StateRepository
from server_pi.rules_engine.engine import IrrigationRuleEngine


class FakePublisher:
    """In-memory MQTT publisher for testing."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object], bool]] = []

    def publish_json(self, topic: str, payload: dict[str, object], retain: bool = False) -> None:
        self.messages.append((topic, payload, retain))


@pytest.fixture
def fake_publisher() -> FakePublisher:
    """Return a fresh FakePublisher for each test."""
    return FakePublisher()


@pytest.fixture
def runtime(tmp_path: Path, fake_publisher: FakePublisher) -> AppState:
    """Return a fully wired AppState backed by in-memory stores."""
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    return AppState(
        publisher=fake_publisher,
        timeseries=InMemoryTimeSeriesRepository(),
        state_repo=StateRepository(tmp_path / "state.json"),
        camera=CameraService(
            camera_id="cam-1",
            zone="greenhouse",
            rtsp_url=None,
            snapshot_source_url=None,
            snapshot_dir=snapshots,
            snapshot_retention_days=7,
        ),
        rule_engine=IrrigationRuleEngine(),
        default_timeout_sec=90,
    )
