"""MQTT publisher wrapper."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from threading import Event

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MqttPublisher:
    """Wrapper around paho-mqtt for publish-only operations."""

    def __init__(self, host: str, port: int, username: str | None = None, password: str | None = None) -> None:
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username and password:
            self._client.username_pw_set(username, password)
        self._connected = Event()
        self._subscriptions: dict[str, Callable[[str, dict[str, object]], None]] = {}
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.connect_async(host, port, 30)
        self._client.loop_start()

    def _on_connect(self, client: mqtt.Client, userdata: object, flags: object, reason_code: object, properties: object) -> None:
        del client, userdata, flags, properties
        if int(reason_code) == 0:
            self._connected.set()
            logger.info("mqtt_connected")

    def _on_disconnect(self, client: mqtt.Client, userdata: object, disconnect_flags: object, reason_code: object, properties: object) -> None:
        del client, userdata, disconnect_flags, reason_code, properties
        self._connected.clear()
        logger.warning("mqtt_disconnected")

    def publish_json(self, topic: str, payload: dict[str, object], retain: bool = False) -> None:
        """Publish JSON payload.

        Args:
            topic: MQTT topic.
            payload: JSON payload.
            retain: Whether to retain the message.

        Raises:
            RuntimeError: If MQTT broker is disconnected.
        """
        if not self._connected.wait(timeout=3):
            raise RuntimeError("mqtt broker unavailable")
        result = self._client.publish(topic, json.dumps(payload, sort_keys=True), qos=1, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"mqtt publish failed with code={result.rc}")

    def subscribe_json(self, topic: str, handler: Callable[[str, dict[str, object]], None]) -> None:
        """Subscribe topic and execute JSON handler.

        Args:
            topic: MQTT topic filter.
            handler: Callback with topic and parsed payload.
        """
        self._subscriptions[topic] = handler
        self._client.subscribe(topic, qos=1)

    def _on_message(self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage) -> None:
        del client, userdata
        payload_text = msg.payload.decode("utf-8", errors="ignore")
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            logger.warning("mqtt_invalid_json topic=%s", msg.topic)
            return
        for topic, handler in self._subscriptions.items():
            if mqtt.topic_matches_sub(topic, msg.topic):
                handler(msg.topic, payload)

    def close(self) -> None:
        """Shutdown MQTT client loops."""
        self._client.loop_stop()
        self._client.disconnect()
