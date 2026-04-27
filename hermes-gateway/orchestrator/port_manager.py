from __future__ import annotations

import json
import threading
from pathlib import Path


PORTS_FILE = Path("/run/gateway-ports/ports.json")
BASE_PORT = 8642
MAX_PORT = 8673


class PortManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._ports: dict[str, int] = {}
        self._load()

    def _load(self):
        if PORTS_FILE.exists():
            try:
                self._ports = json.loads(PORTS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                self._ports = {}
        else:
            self._ports = {}

    def _save(self):
        PORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PORTS_FILE.write_text(json.dumps(self._ports, indent=2) + "\n")

    def allocate(self, agent_id: str) -> int:
        with self._lock:
            if agent_id in self._ports:
                return self._ports[agent_id]
            used = set(self._ports.values())
            for port in range(BASE_PORT, MAX_PORT + 1):
                if port not in used:
                    self._ports[agent_id] = port
                    self._save()
                    return port
            raise RuntimeError(f"No available ports (range {BASE_PORT}-{MAX_PORT} exhausted)")

    def deallocate(self, agent_id: str) -> int | None:
        with self._lock:
            port = self._ports.pop(agent_id, None)
            if port is not None:
                self._save()
            return port

    def get(self, agent_id: str) -> int | None:
        return self._ports.get(agent_id)

    def get_all(self) -> dict[str, int]:
        return dict(self._ports)
