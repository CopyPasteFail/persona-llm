#! /bin/bash

# Link a private overlay into ./private and set PERSONA_DIR for backend dev
set -euo pipefail
PRIVATE_PATH="${1:-}"
if [[ -z "$PRIVATE_PATH" ]]; then
  echo "Usage: scripts/link-private.sh /abs/path/to/private-overlay"
  exit 1
fi
if [[ ! -d "$PRIVATE_PATH/persona" ]]; then
  echo "Expected '$PRIVATE_PATH/persona' to exist"
  exit 1
fi
ln -sfn "$PRIVATE_PATH" private
printf "PERSONA_DIR=private/persona" > backend/.env
echo "Linked ./private -> $PRIVATE_PATH"
echo "Wrote backend/.env with PERSONA_DIR=private/persona"
