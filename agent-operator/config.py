import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
NAMESPACE = os.environ.get("K8S_NAMESPACE", "agents")
DEFAULT_IMAGE = os.environ.get("AGENT_IMAGE", "hermes-agent-remote:latest")
PAPERCLIP_API_URL = os.environ.get("PAPERCLIP_API_URL", "http://paperclip-server:3100/api")
