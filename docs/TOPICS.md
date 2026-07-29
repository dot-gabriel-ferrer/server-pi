# Especificación MQTT

## Convención de tópicos

- `cultivo/<zona>/sensor/<dispositivo>/state`
- `cultivo/<zona>/actuator/<dispositivo>/set`
- `cultivo/<zona>/actuator/<dispositivo>/state`
- `cultivo/<zona>/camera/<camara>/snapshot`
- `cultivo/<zona>/events`

## Payload base JSON

Campos comunes:

- `ts`: timestamp ISO8601 en UTC
- `device_id`: identificador dispositivo
- `zone`: zona lógica
- `type`: `ble`, `zigbee`, `actuator`, `camera`, etc.

Campos opcionales según tipo:

- `temperature_c`
- `humidity_pct`
- `soil_moisture_pct`
- `rssi_dbm`
- `battery_pct`
- `action` (actuadores)
- `duration_sec`
- `mode`
- `actor`
- `reason`

## Ejemplos

### Sensor BLE/Zigbee

Topic:
`cultivo/greenhouse/sensor/ble-aa-bb/state`

Payload:
```json
{
  "ts": "2026-07-29T15:00:00Z",
  "device_id": "ble-aa-bb",
  "zone": "greenhouse",
  "type": "ble",
  "temperature_c": 24.3,
  "humidity_pct": 58,
  "soil_moisture_pct": 31,
  "rssi_dbm": -65,
  "battery_pct": 89
}
```

### Comando actuador

Topic:
`cultivo/greenhouse/actuator/irrigation-main/set`

Payload:
```json
{
  "ts": "2026-07-29T15:01:00Z",
  "device_id": "irrigation-main",
  "zone": "greenhouse",
  "type": "actuator",
  "action": "on",
  "duration_sec": 30,
  "mode": "manual",
  "actor": "operator",
  "reason": "manual check"
}
```

### Estado actuador (retained)

Topic:
`cultivo/greenhouse/actuator/irrigation-main/state`

### Eventos

Topic:
`cultivo/greenhouse/events`

Payload ejemplo:
```json
{
  "ts": "2026-07-29T15:02:00Z",
  "device_id": "irrigation-main",
  "zone": "greenhouse",
  "type": "irrigation",
  "level": "info",
  "message": "irrigation rule triggered"
}
```
