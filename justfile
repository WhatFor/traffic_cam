# Run inside the devShell (`nix develop`, or direnv).

default:
    @just --list

# Publish ingest and web; push deploy/ (including .env), vision/ and config/ to the Pi; update the Compose stack and the host services
[arg("target", long="target", help="SSH destination of the Pi, as user@host")]
deploy target:
    #!/usr/bin/env bash
    set -euo pipefail

    # Each runs on the Pi from its directory, in the stock ASP.NET runtime image.
    dotnet publish ingest/src/TrafficCam.Ingest --configuration Release --runtime linux-arm64 \
        --self-contained false --output deploy/ingest --nologo --verbosity quiet
    dotnet publish ingest/src/TrafficCam.Web --configuration Release --runtime linux-arm64 \
        --self-contained false --output deploy/web --nologo --verbosity quiet

    # Top-level directories with changed files; each is named after its service.
    changed=$(rsync -az --delete --mkpath --itemize-changes deploy/ {{target}}:trafficcam/deploy/ \
        | awk '$1 ~ /^<f/ && $2 ~ /\// { sub(/\/.*/, "", $2); print $2 }' | sort -u)
    rsync -az --delete --mkpath --exclude .venv --exclude __pycache__ --exclude '.*_cache' \
        vision/ {{target}}:trafficcam/vision/
    rsync -az --delete --mkpath config/ {{target}}:trafficcam/config/

    ssh {{target}} bash -s -- $changed <<'EOF'
    set -euo pipefail
    cd trafficcam/deploy
    mountpoint -q /mnt/data || { echo "/mnt/data is not mounted" >&2; exit 1; }
    mkdir -p /mnt/data/tracklogs /mnt/data/clips
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
    install -D -m 644 -t ~/.config/systemd/user ../deploy/systemd/*.service
    systemctl --user daemon-reload
    systemctl --user enable trafficcam-vision trafficcam-throttled
    systemctl --user restart trafficcam-vision trafficcam-throttled
    EOF

# Create or update vision/.venv from the lockfile
[working-directory('vision')]
sync:
    uv sync

# The generated contract types are left as the generator wrote them.
dotnet_format := "dotnet format ingest/TrafficCam.slnx --exclude src/TrafficCam.Contracts/Contracts.g.cs"

# Format and apply lint fixes
fmt:
    ruff format vision
    ruff check --fix vision
    {{dotnet_format}}

# Format check, lint and type check
lint:
    ruff format --check vision
    ruff check vision
    cd vision && uv run pyright
    {{dotnet_format}} --verify-no-changes

test:
    cd vision && uv run pytest
    cd ingest && dotnet test

# Regenerate the pydantic models and C# types from contracts/
gen-contracts:
    cd vision && uv run datamodel-codegen \
        --input ../contracts/trafficcam.v1.schema.json --input-file-type jsonschema \
        --output src/trafficcam/contracts.py --output-model-type pydantic_v2.BaseModel \
        --target-python-version 3.13 --skip-root-model --disable-timestamp \
        --use-schema-description --use-field-description --use-one-literal-as-default \
        --field-constraints --allow-population-by-field-name \
        --formatters ruff-format ruff-check \
        --custom-file-header '# Generated from contracts/trafficcam.v1.schema.json by `just gen-contracts`. Do not edit.'
    dotnet run contracts/gen-csharp.cs -- contracts/trafficcam.v1.schema.json ingest/src/TrafficCam.Contracts/Contracts.g.cs

# Save a full-resolution still from the live stream to calibration/frame.png
[arg("host", long="host", help="Host name or address of the Pi")]
grab-frame host:
    mkdir -p calibration
    ffmpeg -hide_banner -loglevel error -y -rtsp_transport tcp -i rtsp://{{host}}:8554/cam \
        -ss 1 -frames:v 1 -update 1 calibration/frame.png

# Record the live stream to calibration/clip.mp4, without re-encoding
[arg("host", long="host", help="Host name or address of the Pi")]
[arg("seconds", long="seconds", help="Length of the recording")]
grab-clip host seconds:
    mkdir -p calibration
    ffmpeg -hide_banner -loglevel error -y -rtsp_transport tcp -i rtsp://{{host}}:8554/cam \
        -t {{seconds}} -c copy calibration/clip.mp4

# Ask the vision service for a clip and wait for it to be written; the reason is recorded with it
[arg("host", long="host", help="Host name or address of the Pi")]
[working-directory('vision')]
clip host reason:
    #!/usr/bin/env bash
    set -euo pipefail
    . ../deploy/.env
    export MQTT_PASSWORD
    uv run python -m trafficcam.clipcmd --config ../config/site.yaml --host {{host}} {{quote(reason)}}

# Draw the site config's geometry over calibration/frame.png, into calibration/preview.png
[working-directory('vision')]
preview:
    uv run python -m trafficcam.preview --config ../config/site.yaml \
        --frame ../calibration/frame.png --out ../calibration/preview.png
