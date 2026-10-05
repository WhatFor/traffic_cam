# 0007. Contracts: one JSON Schema, generated types in both languages

Date: 2026-10-05. Status: Accepted.

## Context

MQTT payloads are written by Python and read by C# (0001). The build plan makes them contracts-first: defined once as JSON Schema, with types generated for both sides.

## Decision

All payloads are defined in one file, `contracts/trafficcam.v1.schema.json`. `just gen-contracts` generates pydantic models into `vision/src/trafficcam/contracts.py` (with `datamodel-code-generator`) and C# types into `ingest/src/TrafficCam.Contracts/Contracts.g.cs` (with NJsonSchema). Generated files are committed.

- Every payload carries the same envelope: `schema`, `id`, `ts`, `camera`. Data payloads produced by vision also carry `config_hash`.
- Unknown properties are allowed, so a consumer ignores fields added later.
- `contracts/examples/` holds one example per payload. Both test suites round-trip every example through its generated type.

## Consequences

- One file, not one per payload, so each shared enum is generated once in each language.
- The schema uses the older `definitions` keyword, not `$defs`, and nullable references are written as `oneOf` with null. Both are there because the C# generator needs them.
- In Python, `schema` and `class` clash with existing names and become `schema_` and `class_` with aliases; serialising must pass `by_alias=True`.
- In C#, each type has an `AdditionalProperties` dictionary that collects unknown fields; the generator offers no way to drop it while unknown properties are allowed.
- Nothing checks that generated code is current (0005).
