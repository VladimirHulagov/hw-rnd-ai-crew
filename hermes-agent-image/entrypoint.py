#!/usr/bin/env python3
import os
import sys
import re
from pathlib import Path

CONFIG_DIR = Path("/etc/hermes")
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"
SOUL_FILE = CONFIG_DIR / "SOUL.md"
PROCESSED_CONFIG = Path("/tmp/config.yaml")
PROFILE_DIR = Path("/tmp/hermes-profile")


def load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        os.environ[key] = value


def substitute_env(text: str) -> str:
    def replacer(m):
        var = m.group(1)
        return os.environ.get(var, m.group(0))
    return re.sub(r"\$\{(\w+)\}", replacer, text)


def main():
    load_env(ENV_FILE)

    if CONFIG_FILE.exists():
        raw = CONFIG_FILE.read_text()
        processed = substitute_env(raw)
        PROCESSED_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_CONFIG.write_text(processed)
    else:
        print(f"WARNING: {CONFIG_FILE} not found", file=sys.stderr)
        PROCESSED_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_CONFIG.write_text("model:\n  default: glm-5.1\n")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if SOUL_FILE.exists():
        (PROFILE_DIR / "SOUL.md").symlink_to(SOUL_FILE)

    port = int(os.environ.get("API_SERVER_PORT", "8642"))
    api_key = os.environ.get("HERMES_API_SERVER_KEY", "")

    from gateway.platforms.api_server import ApiServerPlatform
    from gateway.config import PlatformConfig

    config = PlatformConfig(
        name="api_server",
        extra={
            "host": "0.0.0.0",
            "port": port,
            "key": api_key,
        },
    )

    platform = ApiServerPlatform(config)
    print(f"Starting hermes agent api_server on 0.0.0.0:{port}", flush=True)

    import asyncio
    asyncio.run(platform.connect())


if __name__ == "__main__":
    main()
