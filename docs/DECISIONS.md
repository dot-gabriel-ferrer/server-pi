# Decisiones técnicas

## 1) Arquitectura local-first modular
- **Decisión:** stack Docker Compose autocontenida en el repo.
- **Motivo:** despliegue directo en Raspberry Pi, operación offline/local.
- **Trade-off:** menos elasticidad que cloud-managed services.

## 2) FastAPI para backend
- **Decisión:** API HTTP con validación estricta Pydantic v2.
- **Motivo:** simplicidad, tipado, rapidez de desarrollo y tests.
- **Trade-off:** no se incluyó autenticación avanzada en este MVP.

## 3) InfluxDB como almacenamiento temporal
- **Decisión:** persistir telemetría y eventos en InfluxDB 2.
- **Motivo:** consultas de series temporales e integración nativa con Grafana.
- **Trade-off:** query layer con Flux aumenta complejidad operativa.

## 4) MQTT como bus local único
- **Decisión:** sensores y actuadores se normalizan a tópicos `cultivo/...`.
- **Motivo:** desacoplar productores/consumidores y permitir extensiones.
- **Trade-off:** requiere disciplina de esquema y validación de payload.

## 5) Motor de reglas MVP en proceso API
- **Decisión:** reglas de riego ejecutadas periódicamente dentro del servicio API.
- **Motivo:** reducir componentes y facilitar trazabilidad/auditoría.
- **Trade-off:** escalar reglas complejas puede requerir separar worker dedicado.

## 6) Estado seguro de actuadores persistido en JSON
- **Decisión:** estado/rules/cooldowns en archivo de estado.
- **Motivo:** robusto, simple y suficiente para MVP local.
- **Trade-off:** sin locking distribuido; para alta concurrencia conviene DB transaccional.

## 7) Cámara Xiaomi por RTSP/snapshot local
- **Decisión:** URL RTSP configurable y fallback snapshot HTTP/ffmpeg.
- **Motivo:** evitar dependencia cloud del fabricante.
- **Trade-off:** compatibilidad real depende del modelo y firmware.
