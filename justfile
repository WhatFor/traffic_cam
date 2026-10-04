# Run inside the devShell (`nix develop`, or direnv).

default:
    @just --list

# Push deploy/ (including .env) to the Pi and bring the Compose stack up to date
[arg("target", long="target", help="SSH destination of the Pi, as user@host")]
deploy target:
    #!/usr/bin/env bash
    set -euo pipefail

    # Top-level directories with changed files; each is named after its service.
    changed=$(rsync -az --delete --mkpath --itemize-changes deploy/ {{target}}:trafficcam/deploy/ \
        | awk '$1 ~ /^<f/ && $2 ~ /\// { sub(/\/.*/, "", $2); print $2 }' | sort -u)

    ssh {{target}} bash -s -- $changed <<'EOF'
    set -euo pipefail
    cd trafficcam/deploy
    # Config files are bind-mounted and `up` does not notice edits to them,
    # so restart the running services whose files changed.
    for svc in "$@"; do
        [[ -z $(docker compose ps -q "$svc" 2>/dev/null) ]] || docker compose restart "$svc"
    done
    docker compose pull --quiet
    docker compose up -d --remove-orphans --wait --wait-timeout 180
    docker compose ps
    EOF

# Create or update vision/.venv from the lockfile
[working-directory('vision')]
sync:
    uv sync

# Format and apply lint fixes
fmt:
    ruff format vision
    ruff check --fix vision

# Format check, lint and type check
[working-directory('vision')]
lint:
    ruff format --check .
    ruff check .
    uv run pyright

[working-directory('vision')]
test:
    uv run pytest
