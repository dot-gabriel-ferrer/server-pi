from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from server_pi.common.models import ActuatorCommandRequest, CommandAction, SensorReading, SensorType


def test_sensor_payload_validation_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SensorReading.model_validate(
            {
                "ts": datetime.now(timezone.utc),
                "device_id": "ble-01",
                "zone": "greenhouse",
                "type": SensorType.ble,
                "humidity_pct": 40,
                "unknown_field": 1,
            }
        )


def test_actuator_command_requires_duration_for_on() -> None:
    payload = ActuatorCommandRequest.model_validate(
        {
            "ts": datetime.now(timezone.utc),
            "zone": "greenhouse",
            "actor": "tester",
            "reason": "manual",
            "action": CommandAction.on,
            "duration_sec": 30,
            "mode": "manual",
        }
    )
    assert payload.duration_sec == 30
