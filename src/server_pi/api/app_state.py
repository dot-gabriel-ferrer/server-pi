"""Application state container."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from server_pi.api.camera import CameraService
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
from server_pi.common.storage import StateRepository, TimeSeriesRepository
from server_pi.rules_engine.engine import IrrigationRuleEngine

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppState:
    """Runtime dependencies for request handlers."""

    publisher: MqttPublisher
    timeseries: TimeSeriesRepository
    state_repo: StateRepository
    camera: CameraService
    rule_engine: IrrigationRuleEngine
    default_timeout_sec: int

    def ensure_default_off(self, actuator_id: str, zone: str) -> ActuatorState:
        """Ensure OFF by default after restart."""
        existing = self.state_repo.get_actuator_state(actuator_id)
        if existing:
            return existing
        state = ActuatorState(
            actuator_id=actuator_id,
            zone=zone,
            state=CommandAction.off,
            mode=ActuatorMode.manual,
            updated_at=datetime.now(timezone.utc),
            last_actor="system",
            safety_timeout_sec=self.default_timeout_sec,
        )
        self.state_repo.set_actuator_state(state)
        return state

    def command_actuator(self, actuator_id: str, payload: ActuatorCommandRequest) -> ActuatorState:
        """Publish actuator command and persist state/event."""
        timeout = payload.duration_sec or self.default_timeout_sec
        state = ActuatorState(
            actuator_id=actuator_id,
            zone=payload.zone,
            state=payload.action,
            mode=payload.mode,
            updated_at=payload.ts.astimezone(timezone.utc),
            last_actor=payload.actor,
            safety_timeout_sec=timeout,
        )
        topic_set = f"cultivo/{payload.zone}/actuator/{actuator_id}/set"
        topic_state = f"cultivo/{payload.zone}/actuator/{actuator_id}/state"
        body = {
            "ts": payload.ts.astimezone(timezone.utc).isoformat(),
            "device_id": actuator_id,
            "zone": payload.zone,
            "type": "actuator",
            "action": payload.action.value,
            "duration_sec": timeout,
            "mode": payload.mode.value,
            "actor": payload.actor,
            "reason": payload.reason,
        }
        self.publisher.publish_json(topic_set, body)
        self.publisher.publish_json(topic_state, body, retain=True)
        self.state_repo.set_actuator_state(state)
        if payload.action == CommandAction.on:
            self.state_repo.set_last_irrigation(actuator_id, payload.ts)
        self.timeseries.write_event(
            EventRecord(
                ts=payload.ts,
                zone=payload.zone,
                device_id=actuator_id,
                type=EventType.actuator,
                level="info",
                message=f"actuator {payload.action.value}",
                actor=payload.actor,
                metadata={"reason": payload.reason, "mode": payload.mode.value, "duration_sec": timeout},
            )
        )
        return state

    def upsert_rule(self, actuator_id: str, rule: IrrigationRule) -> IrrigationRule:
        """Store irrigation rule and emit event."""
        self.state_repo.set_rule(actuator_id, rule)
        self.timeseries.write_event(
            EventRecord(
                ts=datetime.now(timezone.utc),
                zone=rule.zone,
                device_id=actuator_id,
                type=EventType.irrigation,
                level="info",
                message="irrigation rule updated",
                actor="api",
                metadata=rule.model_dump(mode="json"),
            )
        )
        return rule

    def ingest_sensor(self, reading: SensorReading) -> None:
        """Persist incoming sensor reading and evaluate automation."""
        self.timeseries.write_sensor(reading)
        self.state_repo.set_last_telemetry(reading.zone, reading.ts)

    def ingest_sensor_payload(self, payload: dict[str, object]) -> None:
        """Validate and persist raw sensor payload."""
        self.ingest_sensor(SensorReading.model_validate(payload))


async def run_periodic_tasks(state: AppState, interval_sec: int = 10) -> None:
    """Execute periodic tasks: rules, failsafe and camera snapshots."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            _evaluate_rules(state, now)
            event = state.camera.capture_snapshot()
            if event:
                state.timeseries.write_event(event)
        except Exception:  # noqa: BLE001
            logger.exception("periodic_task_failure")
        await asyncio.sleep(interval_sec)


def _evaluate_rules(state: AppState, now: datetime) -> None:
    for actuator_id in ["irrigation-main"]:
        rule = state.state_repo.get_rule(actuator_id)
        if not rule:
            continue
        last_telemetry = state.state_repo.get_last_telemetry(rule.zone)
        if state.rule_engine.telemetry_expired(rule, now, last_telemetry):
            current = state.ensure_default_off(actuator_id, rule.zone)
            if current.state != CommandAction.off:
                request = ActuatorCommandRequest(
                    ts=now,
                    zone=rule.zone,
                    actor="failsafe",
                    reason="telemetry timeout",
                    action=CommandAction.off,
                    duration_sec=state.default_timeout_sec,
                    mode=ActuatorMode.automatic,
                )
                state.command_actuator(actuator_id, request)
            continue

        latest = state.timeseries.get_latest_by_zone(rule.zone)
        soil = next((entry for entry in latest if entry.soil_moisture_pct is not None), None)
        if not soil:
            continue
        decision = state.rule_engine.evaluate(rule, soil, now, state.state_repo.get_last_irrigation(actuator_id))
        if not decision.should_start:
            continue
        state.command_actuator(
            actuator_id,
            ActuatorCommandRequest(
                ts=now,
                zone=rule.zone,
                actor="rules-engine",
                reason=decision.reason,
                action=CommandAction.on,
                duration_sec=rule.max_duration_sec,
                mode=ActuatorMode.automatic,
            ),
        )
