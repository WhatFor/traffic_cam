# 0002. One repository

Date: 2026-10-04. Status: Accepted.

## Context

The system has several parts in different languages: vision (Python), ingest (.NET), the MQTT contracts, the site config and the deployment files. They change together. Adding a detector typically touches a contract, a database migration and a dashboard.

## Decision

Everything lives in one repository, with one top-level directory per part: `vision/`, `ingest/`, `contracts/`, `config/`, `deploy/`, `docs/`. One `justfile` and one Nix devShell cover all of them.

## Consequences

- A change that spans parts is one commit, and there is one version of the whole system and one site config.
- `just deploy` pushes the working tree, so what runs on the Pi is whatever is checked out, not a tagged release.
- Generated code for both languages sits next to the schema it comes from (see 0007).
- The repository must never contain footage, frame grabs or secrets; `.gitignore` excludes them by extension and by directory.
