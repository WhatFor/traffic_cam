# Run inside the devShell (`nix develop`, or direnv).

default:
    @just --list

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
