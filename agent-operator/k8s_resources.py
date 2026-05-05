from typing import Dict, Optional


def agent_resource_name(agent_id: str) -> str:
    return f"agent-{agent_id[:8]}"


def make_deployment(
    name: str,
    namespace: str,
    agent_id: str,
    image: str,
    resources: Optional[Dict] = None,
    image_pull_secret: Optional[str] = None,
) -> dict:
    res = resources or {"cpu": "1", "memory": "2Gi"}
    spec = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "hermes-agent", "agent-id": agent_id, "managed-by": "paperclip-operator"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"agent-id": agent_id}},
            "template": {
                "metadata": {
                    "labels": {
                        "app": "hermes-agent",
                        "agent-id": agent_id,
                        "managed-by": "paperclip-operator",
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "agent",
                            "image": image,
                            "ports": [{"containerPort": 8642}],
                            "envFrom": [{"secretRef": {"name": f"{name}-secrets"}}],
                            "volumeMounts": [{"name": "config", "mountPath": "/etc/hermes"}],
                            "resources": {
                                "requests": res,
                                "limits": res,
                            },
                        }
                    ],
                    "volumes": [{"name": "config", "configMap": {"name": f"{name}-config"}}],
                },
            },
        },
    }
    if image_pull_secret:
        spec["spec"]["template"]["spec"]["imagePullSecrets"] = [{"name": image_pull_secret}]
    return spec


def make_service(name: str, namespace: str, agent_id: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace, "labels": {"managed-by": "paperclip-operator"}},
        "spec": {
            "selector": {"agent-id": agent_id},
            "ports": [{"port": 8642, "targetPort": 8642}],
            "type": "ClusterIP",
        },
    }


def make_config_map(name: str, namespace: str, data: Dict[str, str]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace, "labels": {"managed-by": "paperclip-operator"}},
        "data": data,
    }


def make_secret(name: str, namespace: str, string_data: Dict[str, str]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace, "labels": {"managed-by": "paperclip-operator"}},
        "type": "Opaque",
        "stringData": string_data,
    }
