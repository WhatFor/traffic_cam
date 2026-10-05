"""Records as JSON lines in a file, for replays and tests."""

from pathlib import Path

from trafficcam.contracts import Passage


class JsonlSink:
    """Writes one contract JSON object per line. An existing file is replaced."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("w", encoding="utf-8")

    def passage(self, passage: Passage) -> None:
        self._file.write(passage.model_dump_json(by_alias=True) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
