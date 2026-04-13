#!/bin/bash

HERMES_SRC="/opt/hermes-agent"
HERMES_BUILD="/opt/hermes-agent-build"
HERMES_INSTANCES="/paperclip/hermes-instances"
HERMES_SHARED_CONFIG="/opt/hermes-shared-config"

mkdir -p "$HERMES_INSTANCES"

if ! command -v hermes &>/dev/null; then
    echo "[entrypoint] Installing hermes-agent..."

    if [ ! -f "$HERMES_BUILD/pyproject.toml" ]; then
        echo "[entrypoint] Copying source to build directory..."
        cp -a "$HERMES_SRC"/* "$HERMES_BUILD"/ 2>/dev/null
    fi

    /paperclip/.local/bin/pip install --break-system-packages "$HERMES_BUILD" 2>&1

    if command -v hermes &>/dev/null; then
        echo "[entrypoint] Done."
    else
        echo "[entrypoint] WARNING: hermes still not found after install"
    fi
fi

# Ensure all existing agent instances have the shared config
if [ -d "$HERMES_SHARED_CONFIG" ]; then
    for instance_dir in "$HERMES_INSTANCES"/*/; do
        [ -d "$instance_dir" ] || continue
        if [ ! -f "$instance_dir/config.yaml" ] || [ "$HERMES_SHARED_CONFIG/config.yaml" -nt "$instance_dir/config.yaml" ]; then
            cp "$HERMES_SHARED_CONFIG/config.yaml" "$instance_dir/config.yaml"
        fi
    done
fi

exec "$@"
