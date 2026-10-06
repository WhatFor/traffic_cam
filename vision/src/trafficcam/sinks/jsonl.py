"""Records as JSON lines in a file, for replays and tests."""

from pathlib import Path

from trafficcam.contracts import Event, Passage, SignalChange


class JsonlSink:
    """Writes one contract JSON object per line. An existing file is replaced."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("w", encoding="utf-8")

    def passage(self, passage: Passage) -> None:
        self._write(passage)

    def event(self, event: Event) -> None:
        self._write(event)

    def signal(self, change: SignalChange) -> None:
        self._write(change)

    def _write(self, record: Passage | Event | SignalChange) -> None:
        self._file.write(record.model_dump_json(by_alias=True) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
