"""Records published to the MQTT broker, with the service's online/offline status."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from pydantic import ValidationError

from trafficcam.contracts import (
    Clip,
    ClipCommand,
    ClipDeleted,
    ClipKeep,
    Event,
    GroupState,
    Passage,
    SignalChange,
    Status,
    VisionState,
)

PASSAGES_TOPIC = "trafficcam/v1/passages"
EVENTS_TOPIC = "trafficcam/v1/events"  # followed by the event's type
SIGNALS_TOPIC = "trafficcam/v1/signals"  # followed by the head's name
GROUPS_TOPIC = "trafficcam/v1/groups"  # followed by the group's name, and "/settled"
CLIPS_TOPIC = "trafficcam/v1/clips"  # followed by the clip's id, and "/deleted" once it is gone
CLIP_COMMAND_TOPIC = "trafficcam/v1/cmd/clip"
CLIP_KEEP_TOPIC = "trafficcam/v1/cmd/keep"  # followed by the clip's id; retained
STATUS_TOPIC = "trafficcam/v1/status/vision"
USERNAME = "trafficcam"
QOS = 1
KEEPALIVE_S = 30
# Messages held while the broker is unreachable; beyond this they are dropped.
MAX_QUEUED = 10_000
CLOSE_TIMEOUT_S = 2.0


Record = Passage | Event | SignalChange | GroupState | Clip | ClipDeleted


class MqttSink:
    """Publishes without ever blocking the caller: paho's own thread does the network I/O.

    It also listens for clip commands and for which clips to keep, and hands each one for
    this camera to `on_clip_command` or `on_clip_keep`, on paho's thread.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        camera: str,
        max_queued: int = MAX_QUEUED,
        on_clip_command: Callable[[ClipCommand], None] | None = None,
        on_clip_keep: Callable[[ClipKeep], None] | None = None,
    ) -> None:
        self._camera = camera
        self._on_clip_command = on_clip_command
        self._on_clip_keep = on_clip_keep
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
        self._client.on_message = self._on_message
        self._client.connect_async(host, port, keepalive=KEEPALIVE_S)
        self._client.loop_start()

    @property
    def connected(self) -> bool:
        return self._client.is_connected()

    def passage(self, passage: Passage) -> None:
        self._publish(PASSAGES_TOPIC, passage)

    def event(self, event: Event) -> None:
        self._publish(f"{EVENTS_TOPIC}/{event.type}", event)

    def signal(self, change: SignalChange) -> None:
        # Retained, so a new subscriber learns each head's state at once.
        self._publish(f"{SIGNALS_TOPIC}/{change.head_id}", change, retain=True)

    def group_state(self, state: GroupState) -> None:
        if state.settled:
            self._publish(f"{GROUPS_TOPIC}/{state.group}/settled", state)
        else:
            # Retained, so a new subscriber learns each group's state at once.
            self._publish(f"{GROUPS_TOPIC}/{state.group}", state, retain=True)

    def clip(self, clip: Clip) -> None:
        self._publish(f"{CLIPS_TOPIC}/{clip.id}", clip)

    def clip_deleted(self, deleted: ClipDeleted) -> None:
        self._publish(f"{CLIPS_TOPIC}/{deleted.id}/deleted", deleted)
        # An empty retained payload removes whatever was retained: nothing is left to keep.
        self._client.publish(f"{CLIP_KEEP_TOPIC}/{deleted.id}", None, qos=QOS, retain=True)

    def _publish(self, topic: str, record: Record, retain: bool = False) -> None:
        info = self._client.publish(
            topic, record.model_dump_json(by_alias=True), qos=QOS, retain=retain
        )
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
        # The session is not kept, so a command sent while this service was down is not
        # acted on when it comes back.
        if self._on_clip_command is not None:
            client.subscribe(CLIP_COMMAND_TOPIC, qos=QOS)
        # These are retained, so every clip to be kept is told again on each connection.
        if self._on_clip_keep is not None:
            client.subscribe(f"{CLIP_KEEP_TOPIC}/+", qos=QOS)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        if not message.payload:
            return  # a retained message being removed
        try:
            if message.topic == CLIP_COMMAND_TOPIC:
                command = ClipCommand.model_validate_json(message.payload)
                if command.camera == self._camera and self._on_clip_command is not None:
                    print(f"clip asked for: {command.reason}", flush=True)
                    self._on_clip_command(command)
            else:
                keep = ClipKeep.model_validate_json(message.payload)
                if keep.camera == self._camera and self._on_clip_keep is not None:
                    self._on_clip_keep(keep)
        except ValidationError as error:
            print(f"invalid message on {message.topic}: {error}", flush=True)

    def _status(self, state: VisionState) -> str:
        status = Status.model_validate(
            {"id": uuid.uuid4(), "ts": datetime.now(UTC), "camera": self._camera, "state": state}
        )
        return status.model_dump_json(by_alias=True)
