"""Each payload in contracts/examples validates against the schema and round-trips its model."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from trafficcam import contracts

CONTRACTS = Path(__file__).parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "trafficcam.v1.schema.json").read_text())

MODELS: dict[str, type[BaseModel]] = {
    "passage/1": contracts.Passage,
    "event/1": contracts.Event,
    "signal_change/1": contracts.SignalChange,
    "clip/1": contracts.Clip,
    "clip_deleted/1": contracts.ClipDeleted,
    "clip_command/1": contracts.ClipCommand,
    "status/1": contracts.Status,
}


def validator_for(model: type[BaseModel]) -> Draft202012Validator:
    schema = {"$ref": f"#/definitions/{model.__name__}", "definitions": SCHEMA["definitions"]}
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


@pytest.mark.parametrize(
    "path", sorted((CONTRACTS / "examples").glob("*.json")), ids=lambda p: p.stem
)
def test_example_round_trips(path: Path) -> None:
    payload = json.loads(path.read_text())
    model_type = MODELS[payload["schema"]]
    validator = validator_for(model_type)

    validator.validate(payload)
    model = model_type.model_validate(payload)

    written = json.loads(model.model_dump_json(by_alias=True))
    validator.validate(written)
    assert model_type.model_validate(written) == model
