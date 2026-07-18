#!/usr/bin/env bash
# Full local setup — step 2, after ./scripts/install-just.sh.
# Run via:
#
#   just setup-local        (or directly: ./scripts/setup-local.sh)
#
# Installs uv if missing, creates the virtualenv with all dependencies
# (downloading Python if needed), installs git hooks, creates .env, and
# runs the test suite as a smoke test.
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. uv — package & environment manager
if ! command -v uv >/dev/null 2>&1; then
  echo "→ Installing uv..."
  if command -v brew >/dev/null 2>&1; then
    brew install uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
fi
echo "✓ uv: $(uv --version)"

# 2. Virtualenv + all dependencies
echo "→ Installing dependencies (uv sync)..."
uv sync

# 3. Git hooks (skipped if this isn't a git checkout)
if [ -d .git ]; then
  echo "→ Installing pre-commit hooks..."
  uv run pre-commit install
fi

# 4. Local env file (empty GITHUB_* = local file storage, which is what you want)
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ Created .env — marks will be stored locally in data/marks/"
fi

# 5. Smoke test
echo "→ Running the test suite..."
uv run pytest -q

cat <<'EOF'

✓ Local setup complete!

  just run      → dev server at http://127.0.0.1:8000 (auto-reload)
  just check    → lint + type-check + tests
  just          → list all commands
EOF
