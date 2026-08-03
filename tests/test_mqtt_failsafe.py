"""Failsafe and MQTT integration tests using mocked dependencies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server_pi.api.app_state import AppState, _evaluate_rules
from server_pi.common.models import (
    ActuatorMode,
    ActuatorState,
    CommandAction,
    IrrigationRule,
    SensorReading,
    SensorType,
)


def _base_rule(zone: str = "greenhouse") -> IrrigationRule:
    return IrrigationRule(
        zone=zone,
        enabled=True,
        soil_moisture_threshold_pct=40,
        allowed_start_hour_utc=0,
        allowed_end_hour_utc=23,
        cooldown_minutes=60,
        max_duration_sec=30,
        telemetry_timeout_minutes=15,
    )


def _soil_reading(zone: str = "greenhouse", moisture: float = 20.0) -> SensorReading:
    return SensorReading(
        ts=datetime.now(UTC),
        device_id="sensor-soil",
        zone=zone,
        type=SensorType.ble,
        soil_moisture_pct=moisture,
    )


# ---------------------------------------------------------------------------
# Failsafe: telemetry timeout forces actuator OFF
# ---------------------------------------------------------------------------


def test_failsafe_turns_off_active_actuator(runtime: AppState) -> None:
    """When telemetry expires and actuator is ON, failsafe must send OFF command."""
    rule = _base_rule()
    runtime.state_repo.set_rule("irrigation-main", rule)

    # Put actuator in ON state
    on_state = ActuatorState(
        actuator_id="irrigation-main",
        zone="greenhouse",
        state=CommandAction.on,
        mode=ActuatorMode.automatic,
        updated_at=datetime.now(UTC),
        last_actor="rules-engine",
        safety_timeout_sec=30,
    )
    runtime.state_repo.set_actuator_state(on_state)

    # Telemetry is stale (beyond timeout_minutes)
    stale_ts = datetime.now(UTC) - timedelta(minutes=30)
    runtime.state_repo.set_last_telemetry("greenhouse", stale_ts)

    now = datetime.now(UTC)
    _evaluate_rules(runtime, now)

    state_after = runtime.state_repo.get_actuator_state("irrigation-main")
    assert state_after is not None
    assert state_after.state == CommandAction.off
    assert state_after.last_actor == "failsafe"


def test_failsafe_no_duplicate_off_when_already_off(runtime: AppState) -> None:
    """When telemetry expires but actuator is already OFF, no command is published."""
    rule = _base_rule()
    runtime.state_repo.set_rule("irrigation-main", rule)

    off_state = ActuatorState(
        actuator_id="irrigation-main",
        zone="greenhouse",
        state=CommandAction.off,
        mode=ActuatorMode.automatic,
        updated_at=datetime.now(UTC),
        last_actor="failsafe",
        safety_timeout_sec=30,
    )
    runtime.state_repo.set_actuator_state(off_state)
    runtime.state_repo.set_last_telemetry("greenhouse", datetime.now(UTC) - timedelta(minutes=30))

    # Capture publisher messages count before
    initial_count = len(runtime.publisher.messages)  # type: ignore[attr-defined]
    _evaluate_rules(runtime, datetime.now(UTC))

    assert len(runtime.publisher.messages) == initial_count  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Rule evaluation: irrigation triggered when soil below threshold
# ---------------------------------------------------------------------------


def test_rule_triggers_irrigation_when_soil_low(runtime: AppState) -> None:
    """Rules engine publishes ON command when soil moisture is below threshold."""
    rule = _base_rule()
    runtime.state_repo.set_rule("irrigation-main", rule)

    # Fresh telemetry (not expired)
    runtime.state_repo.set_last_telemetry("greenhouse", datetime.now(UTC))

    # Inject a low soil moisture reading
    reading = _soil_reading(moisture=10.0)
    runtime.timeseries.write_sensor(reading)

    _evaluate_rules(runtime, datetime.now(UTC))

    state = runtime.state_repo.get_actuator_state("irrigation-main")
    assert state is not None
    assert state.state == CommandAction.on
    assert state.last_actor == "rules-engine"


def test_rule_does_not_trigger_when_soil_above_threshold(runtime: AppState) -> None:
    """No irrigation command when soil moisture is above threshold."""
    rule = _base_rule()
    runtime.state_repo.set_rule("irrigation-main", rule)

    runtime.state_repo.set_last_telemetry("greenhouse", datetime.now(UTC))

    reading = _soil_reading(moisture=80.0)
    runtime.timeseries.write_sensor(reading)

    _evaluate_rules(runtime, datetime.now(UTC))

    # State repo should have no actuator state (no command was sent)
    state = runtime.state_repo.get_actuator_state("irrigation-main")
    assert state is None


def test_rule_respects_cooldown_after_irrigation(runtime: AppState) -> None:
    """No second irrigation command when cooldown is active."""
    rule = _base_rule()
    runtime.state_repo.set_rule("irrigation-main", rule)

    runtime.state_repo.set_last_telemetry("greenhouse", datetime.now(UTC))

    # Simulate a very recent irrigation
    recent_irrigation = datetime.now(UTC) - timedelta(minutes=5)
    runtime.state_repo.set_last_irrigation("irrigation-main", recent_irrigation)

    reading = _soil_reading(moisture=10.0)
    runtime.timeseries.write_sensor(reading)

    _evaluate_rules(runtime, datetime.now(UTC))

    state = runtime.state_repo.get_actuator_state("irrigation-main")
    assert state is None


def test_no_rule_stored_skips_evaluation(runtime: AppState) -> None:
    """When no rule is stored, _evaluate_rules publishes nothing."""
    runtime.timeseries.write_sensor(_soil_reading(moisture=10.0))
    initial_count = len(runtime.publisher.messages)  # type: ignore[attr-defined]
    _evaluate_rules(runtime, datetime.now(UTC))
    assert len(runtime.publisher.messages) == initial_count  # type: ignore[attr-defined]
