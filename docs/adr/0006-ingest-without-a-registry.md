# 0006. Ingest runs from a stock runtime image, with no registry

Date: 2026-10-05. Status: Accepted, not yet implemented.

## Context

Ingest belongs in the Compose stack with the broker and the database. The original plan built an arm64 image in CI and pulled it from GHCR. With no CI (0005) that leaves nothing to build the image, and a registry was judged heavy-handed for one service on one machine.

## Decision

Ingest is published on the PC, framework-dependent for `linux-arm64`, into `deploy/ingest/`, which is git-ignored. It runs in Compose from Microsoft's stock ASP.NET runtime image, with that directory bind-mounted. There is no Dockerfile, no custom image and no registry.

## Alternatives considered

- A registry (GHCR): the original plan; rejected as above.
- Building the image on the Pi: the Pi would pull the SDK image and compile while vision is encoding video.
- Building an image tarball on the PC and loading it on the Pi: a normal image, but a large copy on every change.
- A systemd service like vision: simplest, but takes ingest out of the Compose stack.

## Consequences

- The build travels with the existing rsync of `deploy/`, and the deploy recipe's restart-on-change logic restarts the container when it changes.
- The Pi needs no .NET SDK and no registry credentials.
- The runtime image tag must be pinned and must match the .NET version ingest targets.
- What runs is whatever was last published on the PC; there is no image tag recording the version.
