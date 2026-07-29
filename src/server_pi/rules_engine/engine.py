"""Irrigation rule engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from server_pi.common.models import IrrigationRule, SensorReading


@dataclass(slots=True)
class RuleDecision:
    """Decision returned by rule evaluation."""

    should_start: bool
    reason: str


class IrrigationRuleEngine:
    """Evaluates irrigation rules against sensor readings."""

    def evaluate(
        self,
        rule: IrrigationRule,
        reading: SensorReading,
        now: datetime,
        last_irrigation_at: datetime | None,
    ) -> RuleDecision:
        """Evaluate whether irrigation should start.

        Args:
            rule: Irrigation rule definition.
            reading: Latest sensor reading.
            now: Current UTC timestamp.
            last_irrigation_at: Last irrigation timestamp.

        Returns:
            RuleDecision: Evaluation output.
        """
        if not rule.enabled:
            return RuleDecision(False, "rule disabled")

        if reading.soil_moisture_pct is None:
            return RuleDecision(False, "missing soil_moisture_pct")

        if reading.soil_moisture_pct >= rule.soil_moisture_threshold_pct:
            return RuleDecision(False, "soil moisture above threshold")

        if not _hour_allowed(now.astimezone(timezone.utc).hour, rule.allowed_start_hour_utc, rule.allowed_end_hour_utc):
            return RuleDecision(False, "outside allowed window")

        if last_irrigation_at is not None:
            min_next = last_irrigation_at + timedelta(minutes=rule.cooldown_minutes)
            if now < min_next:
                return RuleDecision(False, "cooldown active")

        return RuleDecision(True, "soil moisture below threshold")

    def telemetry_expired(self, rule: IrrigationRule, now: datetime, last_telemetry_at: datetime | None) -> bool:
        """Check failsafe timeout for telemetry.

        Args:
            rule: Active irrigation rule.
            now: Current timestamp.
            last_telemetry_at: Last critical telemetry timestamp.

        Returns:
            bool: True when telemetry timeout has expired.
        """
        if last_telemetry_at is None:
            return True
        timeout = timedelta(minutes=rule.telemetry_timeout_minutes)
        return now - last_telemetry_at > timeout


def _hour_allowed(current_hour: int, start_hour: int, end_hour: int) -> bool:
    if start_hour < end_hour:
        return start_hour <= current_hour < end_hour
    return current_hour >= start_hour or current_hour < end_hour
