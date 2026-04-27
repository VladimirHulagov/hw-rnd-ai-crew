from __future__ import annotations

import logging
import xmlrpc.client

logger = logging.getLogger("gateway-orchestrator")


class SupervisorClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9001):
        url = f"http://{host}:{port}/RPC2"
        self._server = xmlrpc.client.ServerProxy(url)

    def get_process_info(self, name: str) -> dict | None:
        try:
            return self._server.supervisor.getProcessInfo(name)
        except xmlrpc.client.Fault:
            return None

    def start_process(self, name: str) -> bool:
        try:
            self._server.supervisor.startProcess(name)
            return True
        except xmlrpc.client.Fault as e:
            if "ALREADY_STARTED" in str(e):
                return True
            logger.error("Failed to start %s: %s", name, e)
            return False

    def stop_process(self, name: str) -> bool:
        try:
            self._server.supervisor.stopProcess(name)
            return True
        except xmlrpc.client.Fault as e:
            if "NOT_RUNNING" in str(e):
                return True
            logger.error("Failed to stop %s: %s", name, e)
            return False

    def reload_config(self) -> list[str]:
        try:
            result = self._server.supervisor.reloadConfig()
            if isinstance(result, list) and len(result) > 0:
                inner = result[0]
                added = inner[0] if isinstance(inner, (list, tuple)) and len(inner) > 0 else []
            else:
                added = []
            for group in added:
                try:
                    self._server.supervisor.addProcessGroup(group)
                except xmlrpc.client.Fault as e:
                    if "ALREADY_ADDED" not in str(e):
                        logger.error("Failed to add group %s: %s", group, e)
            return added
        except Exception as e:
            logger.error("Failed to reload config: %s", e)
            return []

    def get_all_processes(self) -> list[dict]:
        try:
            return self._server.supervisor.getAllProcessInfo()
        except xmlrpc.client.Fault:
            return []
