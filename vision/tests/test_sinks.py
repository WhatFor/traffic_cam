"""Sinks deliver records in the contract's JSON form."""

import json
import queue
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion
from test_contracts import validator_for
from test_passages import THROUGH, gone, run
from test_pipeline import an_event

from trafficcam.contracts import Event, Passage, Status
from trafficcam.sinks.jsonl import JsonlSink
from trafficcam.sinks.mqtt import EVENTS_TOPIC, PASSAGES_TOPIC, STATUS_TOPIC, MqttSink
from trafficcam.timesync import wait_for_clock_sync

Received = tuple[str, dict]


def a_passage() -> Passage:
    (passage,), _ = run(gone(THROUGH))
    return passage


def test_jsonl_lines_parse_back_into_the_passages_written(tmp_path: Path) -> None:
    path = tmp_path / "passages.jsonl"
    passage = a_passage()

    sink = JsonlSink(path)
    sink.passage(passage)
    sink.passage(passage)
    sink.close()

    lines = path.read_text().splitlines()
    assert [Passage.model_validate_json(line) for line in lines] == [passage, passage]
    assert '"class":' in lines[0]


def test_jsonl_holds_events_alongside_passages(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"

    sink = JsonlSink(path)
    sink.passage(a_passage())
    sink.event(an_event())
    sink.close()

    passage_line, event_line = path.read_text().splitlines()
    assert json.loads(passage_line)["schema"] == "passage/1"
    assert Event.model_validate_json(event_line) == an_event()


def test_jsonl_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "passages.jsonl"
    for _ in range(2):
        sink = JsonlSink(path)
        sink.passage(a_passage())
        sink.close()

    assert len(path.read_text().splitlines()) == 1


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def broker(tmp_path: Path) -> Iterator[int]:
    """A real Mosquitto broker on a free local port."""
    binary = shutil.which("mosquitto")
    if binary is None:
        pytest.skip("mosquitto is not installed")
    port = free_port()
    config = tmp_path / "mosquitto.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n")
    process = subprocess.Popen([binary, "-c", str(config)], stderr=subprocess.DEVNULL)
    for _ in range(100):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)
    yield port
    process.terminate()
    process.wait()


@pytest.fixture
def received(broker: int) -> Iterator[Callable[[str, str | None], dict]]:
    """Subscribe to everything; the returned function waits for a matching message."""
    messages: queue.Queue[Received] = queue.Queue()
    client = mqtt.Client(CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)

    def subscribe(connected: mqtt.Client, *_: object) -> None:
        connected.subscribe("trafficcam/v1/#", qos=1)

    def collect(_client: mqtt.Client, _userdata: object, message: mqtt.MQTTMessage) -> None:
        messages.put((message.topic, json.loads(message.payload)))

    client.on_connect = subscribe
    client.on_message = collect
    client.connect("127.0.0.1", broker)
    client.loop_start()

    def wait_for(topic: str, state: str | None = None) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                got_topic, payload = messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if got_topic == topic and (state is None or payload["state"] == state):
                return payload
        raise AssertionError(f"no message on {topic} with state {state}")

    yield wait_for
    client.disconnect()
    client.loop_stop()


def sink_for(port: int, **kwargs: int) -> MqttSink:
    return MqttSink(host="127.0.0.1", port=port, password="unused", camera="junction-1", **kwargs)


def test_mqtt_publishes_passages_in_the_contract_form(
    broker: int, received: Callable[..., dict]
) -> None:
    sink = sink_for(broker)
    sink.passage(a_passage())

    payload = received(PASSAGES_TOPIC)
    sink.close()

    validator_for(Passage).validate(payload)
    assert payload["movement"] == "south->north"


def test_mqtt_publishes_an_event_under_its_type(broker: int, received: Callable[..., dict]) -> None:
    sink = sink_for(broker)
    sink.event(an_event())

    payload = received(f"{EVENTS_TOPIC}/box_junction_stop")
    sink.close()

    validator_for(Event).validate(payload)
    assert payload["attrs"] == an_event().attrs


def test_mqtt_status_is_online_then_offline_on_close(
    broker: int, received: Callable[..., dict]
) -> None:
    sink = sink_for(broker)

    online = received(STATUS_TOPIC, "online")
    sink.close()
    offline = received(STATUS_TOPIC, "offline")

    for payload in (online, offline):
        validator_for(Status).validate(payload)
        assert payload["camera"] == "junction-1"


def test_mqtt_reports_whether_it_is_connected(broker: int, received: Callable[..., dict]) -> None:
    sink = sink_for(broker)
    assert not sink_for(free_port()).connected

    received(STATUS_TOPIC, "online")
    assert sink.connected

    sink.close()
    assert not sink.connected


def test_mqtt_status_goes_offline_when_the_process_is_killed(
    broker: int, received: Callable[..., dict]
) -> None:
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from trafficcam.sinks.mqtt import MqttSink;"
            "MqttSink(host='127.0.0.1', port=int(sys.argv[1]), password='x', camera='junction-1');"
            "time.sleep(60)",
            str(broker),
        ]
    )
    received(STATUS_TOPIC, "online")

    holder.kill()
    holder.wait()

    assert received(STATUS_TOPIC, "offline")["state"] == "offline"


def test_mqtt_drops_and_counts_when_its_queue_is_full() -> None:
    sink = sink_for(free_port(), max_queued=3)
    passage = a_passage()

    started = time.monotonic()
    for _ in range(5):
        sink.passage(passage)

    assert sink.dropped == 2
    assert time.monotonic() - started < 1


def test_wait_for_clock_sync_polls_until_synchronised() -> None:
    answers = iter([False, False, True])
    slept: list[float] = []

    polls: list[None] = []

    wait_for_clock_sync(
        synchronised=lambda: next(answers),
        sleep=slept.append,
        poll_s=2.0,
        waiting=lambda: polls.append(None),
    )

    assert slept == [2.0, 2.0]
    assert len(polls) == 2
