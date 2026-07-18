# Run `just` to list commands. Requires: uv, just.

# Show available commands
default:
    @just --list

# Full local setup: uv + deps + git hooks + .env + smoke test
setup-local:
    bash scripts/setup-local.sh

# Start the dev server with auto-reload (http://127.0.0.1:8000)
run PORT="8000":
    uv run uvicorn bits_marks_tracker.app:app --reload --port {{PORT}}

# Create venv, install all deps, install git hooks
bootstrap:
    uv sync
    uv run pre-commit install

# Install / update dependencies from pyproject.toml
sync:
    uv sync

# Auto-format the codebase
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Lint (ruff). Use `just fmt` to auto-fix.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Static type check
type:
    uv run mypy

# Run the test suite
test *ARGS:
    uv run pytest {{ARGS}}

# Lint + type-check + test (the full local gate)
check: lint type test

# Run all pre-commit hooks against all files
hooks:
    uv run pre-commit run --all-files

# Build sdist + wheel into dist/
build:
    uv build

# Set the next release version on your feature branch (e.g. `just set-version 1.4.0`).
# If you skip this, the release workflow auto-bumps the minor version on merge to main.
set-version VERSION:
    uv version {{VERSION}}
    @echo "Set version to {{VERSION}}. Commit, push, and open a PR to main to release it."


# Remove build/test/cache artifacts
clean:
    rm -rf dist build .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
    find . -type d -name __pycache__ -exec rm -rf {} +
