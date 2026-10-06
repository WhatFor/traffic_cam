"""Records published to the MQTT broker, with the service's online/offline status."""

import uuid
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from trafficcam.contracts import Event, Passage, Status, VisionState

PASSAGES_TOPIC = "trafficcam/v1/passages"
EVENTS_TOPIC = "trafficcam/v1/events"  # followed by the event's type
STATUS_TOPIC = "trafficcam/v1/status/vision"
USERNAME = "trafficcam"
QOS = 1
KEEPALIVE_S = 30
# Messages held while the broker is unreachable; beyond this they are dropped.
MAX_QUEUED = 10_000
CLOSE_TIMEOUT_S = 2.0


class MqttSink:
    """Publishes without ever blocking the caller: paho's own thread does the network I/O."""

    def __init__(
        self, *, host: str, port: int, password: str, camera: str, max_queued: int = MAX_QUEUED
    ) -> None:
        self._camera = camera
        self.dropped = 0
        self._client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=f"trafficcam-vision-{camera}",
            protocol=mqtt.MQTTv5,
        )
        self._client.username_pw_set(USERNAME, password)
        self._client.max_queued_messages_set(max_queued)
        # The broker publishes this if the connection drops without a clean disconnect.
        self._client.will_set(STATUS_TOPIC, self._status(VisionState.offline), qos=QOS, retain=True)
        self._client.on_connect = self._on_connect
        self._client.connect_async(host, port, keepalive=KEEPALIVE_S)
        self._client.loop_start()

    @property
    def connected(self) -> bool:
        return self._client.is_connected()

    def passage(self, passage: Passage) -> None:
        self._publish(PASSAGES_TOPIC, passage)

    def event(self, event: Event) -> None:
        self._publish(f"{EVENTS_TOPIC}/{event.type}", event)

    def _publish(self, topic: str, record: Passage | Event) -> None:
        info = self._client.publish(topic, record.model_dump_json(by_alias=True), qos=QOS)
        if info.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
            self.dropped += 1
            if self.dropped % 100 == 1:
                print(f"MQTT queue full: {self.dropped} records dropped so far", flush=True)

    def close(self) -> None:
        """Say goodbye properly; a clean disconnect does not trigger the last will."""
        info = self._client.publish(
            STATUS_TOPIC, self._status(VisionState.offline), qos=QOS, retain=True
        )
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            info.wait_for_publish(CLOSE_TIMEOUT_S)
        self._client.disconnect()
        self._client.loop_stop()

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            print(f"MQTT connection refused: {reason_code}", flush=True)
            return
        client.publish(STATUS_TOPIC, self._status(VisionState.online), qos=QOS, retain=True)

    def _status(self, state: VisionState) -> str:
        status = Status.model_validate(
            {"id": uuid.uuid4(), "ts": datetime.now(UTC), "camera": self._camera, "state": state}
        )
        return status.model_dump_json(by_alias=True)
