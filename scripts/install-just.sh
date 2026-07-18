#!/usr/bin/env bash
# Installs the `just` command runner (https://just.systems).
# This is step 1 — run it once, from the repo root:
#
#   ./scripts/install-just.sh
#
# Then everything else is a just command:  just setup-local
set -euo pipefail

if command -v just >/dev/null 2>&1; then
  echo "✓ just is already installed: $(just --version)"
  echo "  Next:  just setup-local"
  exit 0
fi

if command -v brew >/dev/null 2>&1; then
  echo "→ Installing just via Homebrew..."
  brew install just
elif command -v cargo >/dev/null 2>&1; then
  echo "→ Installing just via cargo..."
  cargo install just
else
  dest="${HOME}/.local/bin"
  mkdir -p "${dest}"
  echo "→ Installing just to ${dest} via the official installer..."
  curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh \
    | bash -s -- --to "${dest}"
  case ":${PATH}:" in
    *":${dest}:"*) ;;
    *)
      echo ""
      echo "⚠  ${dest} is not on your PATH. Add this to your shell profile:"
      echo "   export PATH=\"${dest}:\$PATH\""
      ;;
  esac
fi

echo ""
echo "✓ just installed: $(just --version 2>/dev/null || echo 'restart your shell, then run: just --version')"
echo "  Next:  just setup-local"
