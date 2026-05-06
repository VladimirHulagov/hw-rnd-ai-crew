import hashlib
import json
import logging
import time

import psycopg2
from kubernetes import client, config as k8s_config

import config as cfg
from k8s_resources import (
    agent_resource_name,
    make_config_map,
    make_deployment,
    make_secret,
    make_service,
)

logger = logging.getLogger(__name__)


def _config_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class Reconciler:
    def __init__(self):
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()
        self._agent_hashes: dict[str, str] = {}

    def _fetch_agents(self) -> list[dict]:
        conn = psycopg2.connect(cfg.DATABASE_URL)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT a.id, a.name, a.company_id, a.adapter_type, a.adapter_config, a.status, a.role
                FROM agents a
                JOIN company_memberships cm ON cm.principal_id = a.id::text
                WHERE cm.principal_type = 'agent'
                  AND a.adapter_type = 'hermes_remote'
                  AND a.status NOT IN ('terminated', 'paused')
            """
            )
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def _apply_configmap(self, name: str, data: dict[str, str]):
        ns = cfg.NAMESPACE
        body = make_config_map(f"{name}-config", ns, data)
        try:
            self.k8s_core.patch_namespaced_config_map(f"{name}-config", ns, body)
        except Exception:
            self.k8s_core.create_namespaced_config_map(ns, body)

    def _apply_secret(self, name: str, string_data: dict[str, str]):
        ns = cfg.NAMESPACE
        body = make_secret(f"{name}-secrets", ns, string_data)
        try:
            self.k8s_core.patch_namespaced_secret(f"{name}-secrets", ns, body)
        except Exception:
            self.k8s_core.create_namespaced_secret(ns, body)

    def _apply_deployment(self, name: str, agent_id: str, image: str, adapter_config: dict, config_hash: str):
        ns = cfg.NAMESPACE
        resources = adapter_config.get("resources")
        pull_secret = adapter_config.get("imagePullSecret")
        body = make_deployment(name, ns, agent_id, image, resources, pull_secret, config_hash)
        try:
            self.k8s_apps.patch_namespaced_deployment(name, ns, body)
        except Exception:
            self.k8s_apps.create_namespaced_deployment(ns, body)

    def _apply_service(self, name: str, agent_id: str):
        ns = cfg.NAMESPACE
        body = make_service(name, ns, agent_id)
        try:
            self.k8s_core.patch_namespaced_service(name, ns, body)
        except Exception:
            self.k8s_core.create_namespaced_service(ns, body)

    def _delete_all(self, name: str):
        ns = cfg.NAMESPACE
        for fn in [
            lambda: self.k8s_apps.delete_namespaced_deployment(name, ns),
            lambda: self.k8s_core.delete_namespaced_service(name, ns),
            lambda: self.k8s_core.delete_namespaced_config_map(f"{name}-config", ns),
            lambda: self.k8s_core.delete_namespaced_secret(f"{name}-secrets", ns),
        ]:
            try:
                fn()
            except Exception:
                pass

    def _get_active_names(self) -> set[str]:
        try:
            deps = self.k8s_apps.list_namespaced_deployment(
                cfg.NAMESPACE, label_selector="managed-by=paperclip-operator"
            )
            return {d.metadata.name for d in deps.items}
        except Exception:
            return set()

    def reconcile(self):
        agents = self._fetch_agents()
        desired_names: set[str] = set()

        for agent in agents:
            agent_id = agent["id"]
            name = agent_resource_name(agent_id)
            desired_names.add(name)

            adapter_config = agent.get("adapter_config") or {}
            image = adapter_config.get("agentImage", cfg.DEFAULT_IMAGE)
            current_hash = _config_hash(adapter_config)

            if self._agent_hashes.get(agent_id) == current_hash:
                continue

            logger.info("Provisioning agent %s (%s)", agent.get("name"), agent_id[:8])

            config_data = {
                "config.yaml": "# placeholder - adapter will overwrite on first heartbeat execute",
                "SOUL.md": f"# {agent.get('name', 'Agent')}",
            }
            self._apply_configmap(name, config_data)
            self._apply_secret(name, {"PLACEHOLDER": "true"})
            self._apply_deployment(name, agent_id, image, adapter_config, current_hash)
            self._apply_service(name, agent_id)

            self._agent_hashes[agent_id] = current_hash

        active = self._get_active_names()
        for stale_name in active - desired_names:
            logger.info("Deprovisioning stale deployment %s", stale_name)
            self._delete_all(stale_name)

    def run_loop(self):
        logger.info("Starting reconciler loop (interval=%ds)", cfg.POLL_INTERVAL)
        while True:
            try:
                self.reconcile()
            except Exception as e:
                logger.error("Reconcile failed: %s", e)
            time.sleep(cfg.POLL_INTERVAL)
