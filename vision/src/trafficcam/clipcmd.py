"""Ask the vision service for a clip, and wait for it to be written.

    python -m trafficcam.clipcmd --config ../config/site.yaml --host HOST "reason"

MQTT_PASSWORD must be set, as for the service.
"""

import argparse
import os
import queue
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import ValidationError

from trafficcam.clips.recorder import MANUAL
from trafficcam.config import ConfigError, load_site_config
from trafficcam.contracts import Clip, ClipCommand
from trafficcam.sinks.mqtt import CLIP_COMMAND_TOPIC, CLIPS_TOPIC, QOS, USERNAME

# On top of the clip's own length: for the file to be closed and its still frame saved.
EXTRA_WAIT_S = 30


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the vision service for a clip.")
    parser.add_argument("--config", type=Path, required=True, help="path to site.yaml")
    parser.add_argument("--host", required=True, help="host name or address of the broker")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("reason", help="why the clip is wanted; it is recorded with the clip")
    args = parser.parse_args()
    password = os.environ.get("MQTT_PASSWORD")
    if not password:
        sys.exit("MQTT_PASSWORD is not set")
    try:
        config, _ = load_site_config(args.config)
    except ConfigError as error:
        sys.exit(f"invalid config: {error}")

    sent = datetime.now(UTC)
    command = ClipCommand.model_validate(
        {"id": uuid.uuid4(), "ts": sent, "camera": config.camera.id, "reason": args.reason}
    )
    found: queue.Queue[Clip | str] = queue.Queue()

    def on_connect(
        client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any
    ) -> None:
        if reason_code.is_failure:
            found.put(f"connection refused: {reason_code}")
        else:
            client.subscribe(f"{CLIPS_TOPIC}/+", qos=QOS)

    def on_subscribe(client: mqtt.Client, *_: Any) -> None:
        # Only once listening, so the clip's announcement cannot be missed.
        client.publish(CLIP_COMMAND_TOPIC, command.model_dump_json(by_alias=True), qos=QOS)
        print(f"asked {config.camera.id} for a clip: {args.reason}", flush=True)

    def on_message(client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            clip = Clip.model_validate_json(message.payload)
        except ValidationError:
            return
        ours = (t for t in clip.triggers if t.type == MANUAL and t.reason == args.reason)
        if clip.camera == config.camera.id and clip.ended_at >= sent and any(ours):
            found.put(clip)

    client = mqtt.Client(CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    client.username_pw_set(USERNAME, password)
    client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
    client.connect(args.host, args.port)
    client.loop_start()
    pre_s, post_s = config.clips.lengths()
    try:
        result = found.get(timeout=pre_s + post_s + EXTRA_WAIT_S)
    except queue.Empty:
        sys.exit("no clip was announced in time")
    finally:
        client.disconnect()
        client.loop_stop()
    if isinstance(result, str):
        sys.exit(result)

    print(result.path)
    span = f"{result.started_at:%H:%M:%S} to {result.ended_at:%H:%M:%S} UTC"
    print(f"  {span}, {result.bytes / 1e6:.1f} MB")
    for trigger in result.triggers:
        print(
            f"  {trigger.at:%H:%M:%S} {trigger.type}"
            + (f": {trigger.reason}" if trigger.reason else "")
        )
    if result.keyframe_path:
        print(f"  still: {result.keyframe_path}")


if __name__ == "__main__":
    main()
