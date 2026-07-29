from datetime import UTC, datetime, timedelta

from server_pi.common.models import IrrigationRule, SensorReading, SensorType
from server_pi.rules_engine.engine import IrrigationRuleEngine


def test_rule_engine_triggers_when_soil_below_threshold() -> None:
    engine = IrrigationRuleEngine()
    rule = IrrigationRule(
        zone="greenhouse",
        enabled=True,
        soil_moisture_threshold_pct=40,
        allowed_start_hour_utc=0,
        allowed_end_hour_utc=23,
        cooldown_minutes=30,
        max_duration_sec=120,
        telemetry_timeout_minutes=20,
    )
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    reading = SensorReading(
        ts=now,
        device_id="sensor-1",
        zone="greenhouse",
        type=SensorType.ble,
        soil_moisture_pct=20,
    )
    decision = engine.evaluate(
        rule=rule, reading=reading, now=now, last_irrigation_at=now - timedelta(hours=1)
    )
    assert decision.should_start is True


def test_rule_engine_respects_cooldown() -> None:
    engine = IrrigationRuleEngine()
    rule = IrrigationRule(
        zone="greenhouse",
        enabled=True,
        soil_moisture_threshold_pct=40,
        allowed_start_hour_utc=0,
        allowed_end_hour_utc=23,
        cooldown_minutes=60,
        max_duration_sec=120,
        telemetry_timeout_minutes=20,
    )
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    reading = SensorReading(
        ts=now,
        device_id="sensor-1",
        zone="greenhouse",
        type=SensorType.ble,
        soil_moisture_pct=20,
    )
    decision = engine.evaluate(
        rule=rule, reading=reading, now=now, last_irrigation_at=now - timedelta(minutes=20)
    )
    assert decision.should_start is False
