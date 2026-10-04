# Run inside the devShell (`nix develop`, or direnv).

default:
    @just --list

# Push deploy/ (including .env) and vision/ to the Pi; update the Compose stack and the vision service
[arg("target", long="target", help="SSH destination of the Pi, as user@host")]
deploy target:
    #!/usr/bin/env bash
    set -euo pipefail

    # Top-level directories with changed files; each is named after its service.
    changed=$(rsync -az --delete --mkpath --itemize-changes deploy/ {{target}}:trafficcam/deploy/ \
        | awk '$1 ~ /^<f/ && $2 ~ /\// { sub(/\/.*/, "", $2); print $2 }' | sort -u)
    rsync -az --delete --mkpath --exclude .venv --exclude __pycache__ --exclude '.*_cache' \
        vision/ {{target}}:trafficcam/vision/

    ssh {{target}} bash -s -- $changed <<'EOF'
    set -euo pipefail
    cd trafficcam/deploy
    mountpoint -q /mnt/data || { echo "/mnt/data is not mounted" >&2; exit 1; }
    # Config files are bind-mounted and `up` does not notice edits to them,
    # so restart the running services whose files changed.
    for svc in "$@"; do
        [[ -z $(docker compose ps -q "$svc" 2>/dev/null) ]] || docker compose restart "$svc"
    done
    docker compose pull --quiet
    docker compose run --rm -T data-dirs </dev/null   # stdin is this script
    docker compose up -d --remove-orphans --wait --wait-timeout 1200
    docker compose ps

    # uv's installer puts it in ~/.local/bin, which non-interactive SSH leaves off PATH.
    export PATH="$HOME/.local/bin:$PATH"
    cd ../vision
    # picamera2 and the Hailo bindings come from apt, hence the system site packages.
    [[ -d .venv ]] || uv venv --python /usr/bin/python3 --system-site-packages
    uv sync --frozen --no-dev
    install -D -m 644 ../deploy/systemd/trafficcam-vision.service ~/.config/systemd/user/trafficcam-vision.service
    systemctl --user daemon-reload
    systemctl --user enable trafficcam-vision
    systemctl --user restart trafficcam-vision
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
