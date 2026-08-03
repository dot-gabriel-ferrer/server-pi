"""Multi-condition and failsafe tests for the irrigation rule engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server_pi.common.models import IrrigationRule, SensorReading, SensorType, WeatherCondition
from server_pi.rules_engine.engine import IrrigationRuleEngine


@pytest.fixture
def rule() -> IrrigationRule:
    return IrrigationRule(
        zone="greenhouse",
        enabled=True,
        soil_moisture_threshold_pct=40,
        allowed_start_hour_utc=5,
        allowed_end_hour_utc=11,
        cooldown_minutes=180,
        max_duration_sec=60,
        telemetry_timeout_minutes=15,
        weather=WeatherCondition(max_ambient_temp_c=35.0, min_ambient_humidity_pct=80.0),
        temp_sensor_device_id="sensor-ambient",
    )


@pytest.fixture
def soil_reading() -> SensorReading:
    return SensorReading(
        ts=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        device_id="sensor-soil",
        zone="greenhouse",
        type=SensorType.ble,
        soil_moisture_pct=20.0,
    )


@pytest.fixture
def ambient_ok() -> SensorReading:
    return SensorReading(
        ts=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        device_id="sensor-ambient",
        zone="greenhouse",
        type=SensorType.zigbee,
        temperature_c=25.0,
        humidity_pct=60.0,
    )


def test_all_conditions_met_triggers_irrigation(rule, soil_reading, ambient_ok) -> None:
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    decision = engine.evaluate(rule, soil_reading, now, now - timedelta(hours=4), ambient_ok)
    assert decision.should_start is True
    assert decision.reason == "all conditions met"


def test_blocks_when_ambient_too_hot(rule, soil_reading) -> None:
    hot_ambient = SensorReading(
        ts=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        device_id="sensor-ambient",
        zone="greenhouse",
        type=SensorType.zigbee,
        temperature_c=38.0,
        humidity_pct=30.0,
    )
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    decision = engine.evaluate(rule, soil_reading, now, now - timedelta(hours=4), hot_ambient)
    assert decision.should_start is False
    assert "ambient temp" in decision.reason


def test_blocks_when_ambient_humidity_too_high(rule, soil_reading) -> None:
    humid_ambient = SensorReading(
        ts=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        device_id="sensor-ambient",
        zone="greenhouse",
        type=SensorType.zigbee,
        temperature_c=22.0,
        humidity_pct=85.0,
    )
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    decision = engine.evaluate(rule, soil_reading, now, now - timedelta(hours=4), humid_ambient)
    assert decision.should_start is False
    assert "ambient humidity" in decision.reason


def test_no_ambient_reading_does_not_block(rule, soil_reading) -> None:
    """When no ambient sensor is available, weather gates are skipped."""
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    decision = engine.evaluate(rule, soil_reading, now, now - timedelta(hours=4), None)
    assert decision.should_start is True


def test_weather_condition_defaults_do_not_block(soil_reading) -> None:
    """A rule with default WeatherCondition (all None) never blocks via weather."""
    rule_no_weather = IrrigationRule(
        zone="greenhouse",
        enabled=True,
        soil_moisture_threshold_pct=40,
        allowed_start_hour_utc=5,
        allowed_end_hour_utc=11,
        cooldown_minutes=30,
        max_duration_sec=60,
        telemetry_timeout_minutes=15,
    )
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    ambient = SensorReading(
        ts=now,
        device_id="sensor-ambient",
        zone="greenhouse",
        type=SensorType.zigbee,
        temperature_c=50.0,
        humidity_pct=99.0,
    )
    decision = engine.evaluate(
        rule_no_weather, soil_reading, now, now - timedelta(hours=1), ambient
    )
    assert decision.should_start is True


def test_telemetry_expired_when_no_telemetry(rule) -> None:
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    assert engine.telemetry_expired(rule, now, None) is True


def test_telemetry_expired_when_stale(rule) -> None:
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    stale = now - timedelta(minutes=20)
    assert engine.telemetry_expired(rule, now, stale) is True


def test_telemetry_not_expired_when_fresh(rule) -> None:
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    fresh = now - timedelta(minutes=5)
    assert engine.telemetry_expired(rule, now, fresh) is False


def test_rule_disabled_skips_all_checks(rule, soil_reading, ambient_ok) -> None:
    rule_off = rule.model_copy(update={"enabled": False})
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    decision = engine.evaluate(rule_off, soil_reading, now, None, ambient_ok)
    assert decision.should_start is False
    assert decision.reason == "rule disabled"


def test_backward_compat_without_ambient_kwarg(rule, soil_reading) -> None:
    """evaluate() works with the original positional signature (no ambient_reading)."""
    engine = IrrigationRuleEngine()
    now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    decision = engine.evaluate(rule, soil_reading, now, now - timedelta(hours=4))
    assert decision.should_start is True
