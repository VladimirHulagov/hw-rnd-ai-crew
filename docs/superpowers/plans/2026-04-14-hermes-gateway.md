# Hermes Gateway-in-a-Box Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `hermes chat -q` subprocess execution with a persistent Hermes gateway deployment, enabling memory nudges, Telegram integration, and full Hermes capabilities for all Paperclip agents.

**Architecture:** A single Docker container (`hermes-gateway`) runs Supervisor as PID 1, with a Python orchestrator that dynamically provisions gateway processes (one per Paperclip agent) via Hermes profiles. The Paperclip adapter is rewritten to send HTTP requests to the gateway's API_SERVER endpoint instead of spawning child processes.

**Tech Stack:** Python 3.11, Supervisor, aiohttp, python-telegram-bot, Hermes Agent (git submodule), Node.js/TypeScript (adapter rewrite)

---

## File Structure

### New files (created)

```
hermes-gateway/
  Dockerfile
  supervisord.conf
  orchestrator/
    __init__.py
    orchestrator.py
    config_generator.py
    port_manager.py
    supervisor_client.py
  config-template.yaml
  requirements.txt
```

### Modified files

```
hermes-paperclip-adapter/src/server/execute.ts    # Rewrite: HTTP instead of spawn
hermes-paperclip-adapter/src/server/index.ts       # Update detectModel, listSkills
hermes-paperclip-adapter/src/shared/constants.ts   # Add gateway constants
docker-compose.yml                                  # Add hermes-gateway service
.env                                                # Add TELEGRAM_BOT_TOKEN, HERMES_API_SERVER_KEY
```

### Compiled files (auto-generated from TS source)

```
hermes-paperclip-adapter/dist/server/execute.js
hermes-paperclip-adapter/dist/server/index.js
hermes-paperclip-adapter/dist/shared/constants.js
```

---

## Task 1: Gateway Dockerfile and Base Image

**Files:**
- Create: `hermes-gateway/Dockerfile`
- Create: `hermes-gateway/requirements.txt`

- [ ] **Step 1: Create requirements.txt**

```
supervisor>=4.2.0
python-telegram-bot>=20.0
aiohttp>=3.9.0
httpx>=0.27.0
pyyaml>=6.0
```

- [ ] **Step 2: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt

COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r /opt/requirements.txt

COPY supervisord.conf /etc/supervisor/supervisord.conf
COPY orchestrator/ /opt/orchestrator/
COPY config-template.yaml /opt/config-template.yaml

RUN mkdir -p /paperclip/hermes-instances /run/gateway-ports /var/log/supervisor

EXPOSE 8642-8673

ENTRYPOINT ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
```

- [ ] **Step 3: Verify Dockerfile builds**

Run: `docker build -t hermes-gateway:latest ./hermes-gateway`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add hermes-gateway/Dockerfile hermes-gateway/requirements.txt
git commit -m "feat(gateway): add Dockerfile and requirements for hermes-gateway container"
```

---

## Task 2: Supervisor Configuration

**Files:**
- Create: `hermes-gateway/supervisord.conf`

- [ ] **Step 1: Create supervisord.conf**

```ini
[supervisord]
nodaemon=true
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid
childlogdir=/var/log/supervisor

[supervisorctl]

[inet_http_server]
port=127.0.0.1:9001

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[program:orchestrator]
command=python -u /opt/orchestrator/orchestrator.py
autostart=true
autorestart=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
redirect_stderr=true
priority=1
```

- [ ] **Step 2: Verify supervisor starts**

Run: `docker run --rm hermes-gateway:latest`
Expected: Supervisor starts, orchestrator launches (will fail gracefully without Hermes installed — that's OK for now)

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/supervisord.conf
git commit -m "feat(gateway): add supervisord configuration"
```

---

## Task 3: Config Template Generator

**Files:**
- Create: `hermes-gateway/orchestrator/__init__.py`
- Create: `hermes-gateway/orchestrator/config_generator.py`
- Create: `hermes-gateway/config-template.yaml`

- [ ] **Step 1: Create __init__.py**

```python
```

- [ ] **Step 2: Create config-template.yaml**

This is a Jinja2-like template with `{{placeholders}}` that the config generator fills per agent. Based on the current `hermes-shared-config/config.yaml`:

```yaml
model:
  default: {{model}}
  provider: {{provider}}
terminal:
  backend: local
  cwd: .
  timeout: 180
  persistent_shell: true
compression:
  enabled: true
  threshold: 0.6
  target_ratio: 0.2
  protect_last_n: 20
  summary_model: {{summary_model}}
  summary_provider: auto
auxiliary:
  vision:
    provider: {{provider}}
    model: {{vision_model}}
  web_extract:
    provider: auto
    timeout: 360
  session_search:
    provider: auto
    timeout: 30
display:
  compact: true
  personality: {{personality}}
  streaming: false
  tool_progress: result
memory:
  memory_enabled: true
  user_profile_enabled: true
  nudge_interval: 10
approvals:
  mode: off
  timeout: 60
web:
  backend: tavily
{{platforms_section}}
mcp_servers:
  rag:
    url: https://rag.collaborationism.tech/mcp
    headers:
      Authorization: "Bearer {{mcp_rag_api_key}}"
    enabled: true
    timeout: 120
    connect_timeout: 60
  paperclip:
    url: http://paperclip-mcp:8082/mcp
    headers:
      X-Paperclip-Api-Key: "{{paperclip_api_key}}"
      X-Paperclip-Company-Id: "{{company_id}}"
      X-Paperclip-Agent-Id: "{{agent_id}}"
    enabled: true
    timeout: 60
    connect_timeout: 30
_config_version: 6
security:
  redact_secrets: true
  tirith_enabled: false
  tirith_fail_open: true
```

- [ ] **Step 3: Create config_generator.py**

```python
import os
import string
from pathlib import Path


_TEMPLATE_PATH = Path("/opt/config-template.yaml")


def _substitute(template: str, values: dict[str, str]) -> str:
    safe = string.Template(template)
    return safe.safe_substitute(values)


def generate_profile_config(
    agent_id: str,
    company_id: str,
    allocated_port: int,
    model: str = "glm-5.1",
    provider: str = "zai",
    personality: str = "kawaii",
    summary_model: str = "glm-5",
    vision_model: str = "glm-4.6v",
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_allowed_users: str | None = None,
) -> str:
    template = _TEMPLATE_PATH.read_text()

    values: dict[str, str] = {
        "model": model,
        "provider": provider,
        "personality": personality,
        "summary_model": summary_model,
        "vision_model": vision_model,
        "agent_id": agent_id,
        "company_id": company_id,
        "mcp_rag_api_key": os.environ.get("MCP_RAG_API_KEY", ""),
        "paperclip_api_key": os.environ.get("PAPERCLIP_API_KEY", ""),
    }

    platforms_lines = [
        "platforms:",
        "  api_server:",
        "    enabled: true",
        "    extra:",
        f"      key: \"{os.environ.get('HERMES_API_SERVER_KEY', '')}\"",
        "      host: \"0.0.0.0\"",
        f"      port: {allocated_port}",
    ]

    if telegram_bot_token:
        platforms_lines.extend([
            "  telegram:",
            "    enabled: true",
            f"    token: \"{telegram_bot_token}\"",
        ])
        if telegram_chat_id:
            platforms_lines.append(f"    extra:")
            platforms_lines.append(f"      home_channel: \"{telegram_chat_id}\"")
            if telegram_allowed_users:
                platforms_lines.append(f"      allowed_users: \"{telegram_allowed_users}\"")

    values["platforms_section"] = "\n".join(platforms_lines)

    return _substitute(template, values)


def ensure_profile_dirs(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "memories").mkdir(exist_ok=True)
    (profile_dir / "skills").mkdir(exist_ok=True)
    (profile_dir / "sessions").mkdir(exist_ok=True)
```

- [ ] **Step 4: Verify config generation works**

Run: `cd /mnt/services/hw-rnd-ai-crew && python3 -c "
from hermes_gateway.orchestrator.config_generator import generate_profile_config
config = generate_profile_config(
    agent_id='test-123',
    company_id='company-456',
    allocated_port=8642,
    telegram_bot_token='8674012815:test',
    telegram_chat_id='-100123456',
)
print(config)
"`
Expected: Config YAML printed with correct placeholders filled

- [ ] **Step 5: Commit**

```bash
git add hermes-gateway/orchestrator/__init__.py hermes-gateway/orchestrator/config_generator.py hermes-gateway/config-template.yaml
git commit -m "feat(gateway): add per-agent config template generator"
```

---

## Task 4: Port Manager

**Files:**
- Create: `hermes-gateway/orchestrator/port_manager.py`

- [ ] **Step 1: Create port_manager.py**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/port_manager.py
git commit -m "feat(gateway): add port allocation manager"
```

---

## Task 5: Supervisor Client Wrapper

**Files:**
- Create: `hermes-gateway/orchestrator/supervisor_client.py`

- [ ] **Step 1: Create supervisor_client.py**

```python
import xmlrpc.client
import logging

logger = logging.getLogger("gateway-orchestrator")


class SupervisorClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9001):
        url = f"http://{host}:{port}/RPC2"
        self._server = xmlrpc.client.ServerProxy(url)
        self._supervisor = self._server.supervisor

    def get_process_info(self, name: str) -> dict | None:
        try:
            return self._supervisor.getProcessInfo(name)
        except xmlrpc.client.Fault:
            return None

    def add_program(self, name: str, command: str, directory: str = "/") -> bool:
        section = f"program:{name}"
        try:
            self._supervisor.supervisord.options.__setattr__("add_programs", None)
        except Exception:
            pass

        config_lines = [
            f"[program:{name}]",
            f"command={command}",
            f"directory={directory}",
            "autostart=true",
            "autorestart=true",
            "stdout_logfile=/dev/fd/1",
            "stdout_logfile_maxbytes=0",
            "redirect_stderr=true",
        ]
        config_text = "\n".join(config_lines)

        try:
            import xmlrpc.client as xc
            self._server.supervisor.supervisord.options.read_config(config_text)
            return True
        except Exception as e:
            logger.error("Failed to add program %s via read_config: %s", name, e)

        return False

    def start_process(self, name: str) -> bool:
        try:
            self._supervisor.startProcess(name)
            return True
        except xmlrpc.client.Fault as e:
            if "ALREADY_STARTED" in str(e):
                return True
            logger.error("Failed to start %s: %s", name, e)
            return False

    def stop_process(self, name: str) -> bool:
        try:
            self._supervisor.stopProcess(name)
            return True
        except xmlrpc.client.Fault as e:
            if "NOT_RUNNING" in str(e):
                return True
            logger.error("Failed to stop %s: %s", name, e)
            return False

    def remove_process(self, name: str) -> bool:
        try:
            self._supervisor.stopProcess(name)
        except xmlrpc.client.Fault:
            pass
        return True

    def get_all_processes(self) -> list[dict]:
        try:
            return self._supervisor.getAllProcessInfo()
        except xmlrpc.client.Fault:
            return []

    def reload_config(self) -> bool:
        try:
            self._supervisor.reloadConfig()
            return True
        except Exception as e:
            logger.error("Failed to reload config: %s", e)
            return False
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/supervisor_client.py
git commit -m "feat(gateway): add supervisor XML-RPC client wrapper"
```

---

## Task 6: Main Orchestrator

**Files:**
- Create: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Create orchestrator.py**

This is the main daemon that polls Paperclip API and manages gateway processes.

```python
import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import httpx

from .config_generator import ensure_profile_dirs, generate_profile_config
from .port_manager import PortManager
from .supervisor_client import SupervisorClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("gateway-orchestrator")

INSTANCES_DIR = Path("/paperclip/hermes-instances")
HERMES_SRC = Path("/opt/hermes-agent")
HERMES_BUILD = Path("/opt/hermes-agent-build")
PORTS_FILE = Path("/run/gateway-ports/ports.json")
POLL_INTERVAL = int(os.environ.get("ORCHESTRATOR_POLL_INTERVAL", "60"))

PAPERCLIP_API_URL = os.environ.get("PAPERCLIP_API_URL", "http://paperclip-server:3100/api")
HERMES_HOME_DEFAULT = Path.home() / ".hermes"


def _ensure_hermes_installed():
    if shutil.which("hermes"):
        return
    logger.info("Installing hermes-agent from source...")
    if not HERMES_BUILD.exists() or not (HERMES_BUILD / "pyproject.toml").exists():
        shutil.copytree(HERMES_SRC, HERMES_BUILD, dirs_exist_ok=True)
    import subprocess
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--break-system-packages", str(HERMES_BUILD)],
        check=True,
        capture_output=True,
    )
    logger.info("hermes-agent installed.")


def _ensure_profiles_root():
    profiles_dir = HERMES_HOME_DEFAULT / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    return profiles_dir


async def fetch_agents(client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(f"{PAPERCLIP_API_URL}/agents", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("agents", data.get("items", []))
        return []
    except Exception as e:
        logger.error("Failed to fetch agents: %s", e)
        return []


async def fetch_agent_details(client: httpx.AsyncClient, agent_id: str) -> dict | None:
    try:
        resp = await client.get(f"{PAPERCLIP_API_URL}/agents/{agent_id}", timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("Failed to fetch agent %s: %s", agent_id, e)
        return None


def _write_ports_json(ports: dict[str, int]):
    PORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORTS_FILE.write_text(json.dumps(ports, indent=2) + "\n")


class Orchestrator:
    def __init__(self):
        self.port_manager = PortManager()
        self.supervisor = SupervisorClient()
        self.profiles_root = _ensure_profiles_root()
        self._running_agent_ids: set[str] = set()
        self._known_agents: dict[str, dict] = {}

    def _profile_dir(self, agent_id: str) -> Path:
        return self.profiles_root / agent_id

    def _gateway_name(self, agent_id: str) -> str:
        return f"gateway-{agent_id[:12]}"

    async def provision_agent(self, agent: dict):
        agent_id = agent.get("id", "")
        if not agent_id:
            return

        port = self.port_manager.allocate(agent_id)
        profile_dir = self._profile_dir(agent_id)

        ensure_profile_dirs(profile_dir)

        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        allowed_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "")

        role = agent.get("role", "")
        name = agent.get("name", "Agent")
        enable_telegram = bool(telegram_token and telegram_chat_id and role in ("ceo", "cto"))

        config = generate_profile_config(
            agent_id=agent_id,
            company_id=agent.get("companyId", agent.get("company_id", "")),
            allocated_port=port,
            telegram_bot_token=telegram_token if enable_telegram else None,
            telegram_chat_id=telegram_chat_id if enable_telegram else None,
            telegram_allowed_users=allowed_users if enable_telegram else None,
        )

        config_path = profile_dir / "config.yaml"
        if config_path.exists():
            existing = config_path.read_text()
            if existing != config:
                config_path.write_text(config)
                logger.info("Updated config for agent %s (%s)", name, agent_id[:8])
        else:
            config_path.write_text(config)
            logger.info("Created config for agent %s (%s)", name, agent_id[:8])

        env_content = "\n".join([
            f"GLM_API_KEY={os.environ.get('GLM_API_KEY', '')}",
            f"GLM_BASE_URL={os.environ.get('GLM_BASE_URL', '')}",
            f"GEMINI_API_KEY={os.environ.get('GEMINI_API_KEY', '')}",
            f"TAVILY_API_KEY={os.environ.get('TAVILY_API_KEY', '')}",
            f"PARALLEL_API_KEY={os.environ.get('PARALLEL_API_KEY', '')}",
            f"FAL_KEY={os.environ.get('FAL_KEY', '')}",
        ])
        (profile_dir / ".env").write_text(env_content + "\n")

        proc_name = self._gateway_name(agent_id)
        command = f"hermes -p {agent_id} gateway run"

        existing = self.supervisor.get_process_info(proc_name)
        if existing and existing.get("state", 0) > 0:
            self._running_agent_ids.add(agent_id)
            return

        logger.info("Starting gateway for %s on port %d", name, port)

        program_conf = (
            f"[program:{proc_name}]\n"
            f"command={command}\n"
            f"directory=/\n"
            f"environment=HERMES_HOME=\"{profile_dir}\"\n"
            f"autostart=true\n"
            f"autorestart=true\n"
            f"stdout_logfile=/dev/fd/1\n"
            f"stdout_logfile_maxbytes=0\n"
            f"redirect_stderr=true\n"
            f"priority=10\n"
            f"startsecs=5\n"
            f"startretries=3\n"
        )

        conf_dir = Path("/etc/supervisor/conf.d")
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / f"{proc_name}.conf").write_text(program_conf)

        self.supervisor.reload_config()
        self.supervisor.start_process(proc_name)
        self._running_agent_ids.add(agent_id)

        _write_ports_json(self.port_manager.get_all())
        logger.info("Gateway %s started (port %d, profile %s)", proc_name, port, profile_dir)

    async def deprovision_agent(self, agent_id: str):
        proc_name = self._gateway_name(agent_id)
        logger.info("Stopping gateway for agent %s", agent_id[:8])

        conf_path = Path(f"/etc/supervisor/conf.d/{proc_name}.conf")
        if conf_path.exists():
            conf_path.unlink()

        self.supervisor.stop_process(proc_name)
        self.supervisor.reload_config()

        self.port_manager.deallocate(agent_id)
        self._running_agent_ids.discard(agent_id)

        _write_ports_json(self.port_manager.get_all())
        logger.info("Gateway %s stopped and cleaned up", proc_name)

    async def reconcile(self, agents: list[dict]):
        current_ids = {a.get("id") for a in agents if a.get("id")}

        for agent in agents:
            agent_id = agent.get("id", "")
            if agent_id not in self._running_agent_ids:
                await self.provision_agent(agent)
            self._known_agents[agent_id] = agent

        for agent_id in list(self._running_agent_ids):
            if agent_id not in current_ids:
                await self.deprovision_agent(agent_id)
                del self._known_agents[agent_id]

        _write_ports_json(self.port_manager.get_all())

    async def run(self):
        logger.info("Orchestrator starting...")
        logger.info("Paperclip API: %s", PAPERCLIP_API_URL)
        logger.info("Poll interval: %ds", POLL_INTERVAL)

        _ensure_hermes_installed()
        _ensure_profiles_root()

        async with httpx.AsyncClient() as client:
            while True:
                try:
                    agents = await fetch_agents(client)
                    logger.info("Found %d agents, %d gateways running", len(agents), len(self._running_agent_ids))
                    await self.reconcile(agents)
                except Exception as e:
                    logger.error("Reconciliation failed: %s", e)

                await asyncio.sleep(POLL_INTERVAL)


async def main():
    orch = Orchestrator()
    await orch.run()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat(gateway): add main orchestrator daemon"
```

---

## Task 7: Update Constants for Gateway Mode

**Files:**
- Modify: `hermes-paperclip-adapter/src/shared/constants.ts`
- Compile: `hermes-paperclip-adapter/dist/shared/constants.js`

- [ ] **Step 1: Add gateway constants to constants.ts**

Add these exports to the end of the file:

```typescript
/** Gateway API_SERVER mode (replaces hermes chat -q). */
export const GATEWAY_MODE = true;

/** Base URL for gateway API_SERVER endpoints. */
export const GATEWAY_BASE_URL = process.env.GATEWAY_BASE_URL || "http://hermes-gateway";

/** Path to the gateway ports mapping file. */
export const GATEWAY_PORTS_FILE = "/run/gateway-ports/ports.json";

/** Default API_SERVER key for gateway auth. */
export const GATEWAY_API_KEY = process.env.HERMES_API_SERVER_KEY || "";
```

- [ ] **Step 2: Compile TypeScript**

Run: `cd /mnt/services/hw-rnd-ai-crew/hermes-paperclip-adapter && npm run build`
Expected: Build succeeds, `dist/shared/constants.js` updated

- [ ] **Step 3: Commit**

```bash
git add hermes-paperclip-adapter/src/shared/constants.ts hermes-paperclip-adapter/dist/shared/constants.js
git commit -m "feat(adapter): add gateway mode constants"
```

---

## Task 8: Rewrite Adapter execute() for Gateway HTTP

**Files:**
- Modify: `hermes-paperclip-adapter/src/server/execute.ts`
- Compile: `hermes-paperclip-adapter/dist/server/execute.js`

- [ ] **Step 1: Rewrite execute.ts**

Replace the entire file content. The key changes: HTTP fetch instead of spawn, port lookup from JSON file, OpenAI-compatible request format.

```typescript
import type {
  AdapterExecutionContext,
  AdapterExecutionResult,
  UsageSummary,
} from "@paperclipai/adapter-utils";

import { renderTemplate } from "@paperclipai/adapter-utils/server-utils";

import {
  DEFAULT_TIMEOUT_SEC,
  DEFAULT_MODEL,
  GATEWAY_MODE,
  GATEWAY_PORTS_FILE,
  GATEWAY_API_KEY,
} from "../shared/constants.js";

import { readFile } from "node:fs/promises";

function cfgString(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}
function cfgNumber(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}
function cfgBoolean(v: unknown): boolean | undefined {
  return typeof v === "boolean" ? v : undefined;
}

const DEFAULT_PROMPT_TEMPLATE = `You are "{{agentName}}", an AI agent employee in a Paperclip-managed company.

Your Paperclip identity:
  Agent ID: {{agentId}}
  Company ID: {{companyId}}

You have MCP tools for Paperclip (prefixed \`paperclip_\`). Use them instead of curl for ALL Paperclip API interactions:
- paperclip_list_issues(status?, assigneeAgentId?, projectId?, parentId?)
- paperclip_get_issue(issueId) — accepts UUID or identifier like HWQAA-1
- paperclip_create_issue(title, description?, status?, priority?, assigneeAgentId?, projectId?, parentId?)
- paperclip_update_issue(issueId, status?, priority?, assigneeAgentId?, description?, comment?)
- paperclip_delete_issue(issueId)
- paperclip_checkout_issue(issueId, expectedStatuses?) — claim an issue for work
- paperclip_release_issue(issueId) — release your checkout
- paperclip_list_comments(issueId, limit?)
- paperclip_create_comment(issueId, body)
- paperclip_list_agents()
- paperclip_get_agent(agentId) — use "me" for yourself
- paperclip_get_current_agent()
- paperclip_create_agent_hire(name, adapterType, role?, title?, icon?, reportsTo?, capabilities?, adapterConfig?, runtimeConfig?, permissions?, desiredSkills?, sourceIssueIds?, metadata?)
- paperclip_create_agent(name, adapterType, role?, title?, ...) — directly create agent (board-only)
- paperclip_list_approvals(status?) — list approval requests
- paperclip_get_approval(approvalId)
- paperclip_approve_approval(approvalId) — approve a hire request (board-only)
- paperclip_reject_approval(approvalId, reason?) — reject a request (board-only)
- paperclip_list_projects()
- paperclip_get_company()
- paperclip_list_goals()
- paperclip_get_goal(goalId)

You also have access to messaging tools. If you need to ask a human a clarifying question, use the send_message tool to send a message via Telegram. The user will reply and you will receive the answer automatically.

{{#taskId}}
## Assigned Task

Issue ID: {{taskId}}
Title: {{taskTitle}}

{{taskBody}}

## Workflow

1. Work on the task using your tools
2. When done, update the issue status to done using paperclip_update_issue
3. Report what you did
{{/taskId}}

{{#noTask}}
## Heartbeat Wake — Check for Work

1. List issues assigned to you using paperclip_list_issues
2. If issues found, pick the highest priority one and work on it
3. If no issues found, check for any unassigned issues
4. If truly nothing to do, report briefly.
{{/noTask}}`;

function buildPrompt(
  ctx: AdapterExecutionContext,
  config: Record<string, unknown>,
): string {
  const template = cfgString(config.promptTemplate) || DEFAULT_PROMPT_TEMPLATE;

  const taskId = cfgString(ctx.config?.taskId);
  const taskTitle = cfgString(ctx.config?.taskTitle) || "";
  const taskBody = cfgString(ctx.config?.taskBody) || "";
  const agentName = ctx.agent?.name || "Hermes Agent";
  const companyName = cfgString(ctx.config?.companyName) || "";

  const vars: Record<string, unknown> = {
    agentId: ctx.agent?.id || "",
    agentName,
    companyId: ctx.agent?.companyId || "",
    companyName,
    runId: ctx.runId || "",
    taskId: taskId || "",
    taskTitle,
    taskBody,
  };

  let rendered = template;
  rendered = rendered.replace(
    /\{\{#taskId\}\}([\s\S]*?)\{\{\/taskId\}\}/g,
    taskId ? "$1" : "",
  );
  rendered = rendered.replace(
    /\{\{#noTask\}\}([\s\S]*?)\{\{\/noTask\}\}/g,
    taskId ? "" : "$1",
  );
  return renderTemplate(rendered, vars);
}

interface PortMapping {
  [agentId: string]: number;
}

async function lookupGatewayPort(agentId: string): Promise<number | null> {
  try {
    const raw = await readFile(GATEWAY_PORTS_FILE, "utf-8");
    const ports: PortMapping = JSON.parse(raw);
    return ports[agentId] ?? null;
  } catch {
    return null;
  }
}

export async function execute(
  ctx: AdapterExecutionContext,
): Promise<AdapterExecutionResult> {
  const config = (ctx.agent?.adapterConfig ?? {}) as Record<string, unknown>;
  const timeoutSec = cfgNumber(config.timeoutSec) || DEFAULT_TIMEOUT_SEC;
  const agentId = cfgString(ctx.agent?.id) || "";

  if (GATEWAY_MODE && agentId) {
    return executeViaGateway(ctx, config, agentId, timeoutSec);
  }

  return {
    exitCode: 1,
    errorMessage: "Gateway mode required but no agent ID provided",
    provider: null,
    model: DEFAULT_MODEL,
  };
}

async function executeViaGateway(
  ctx: AdapterExecutionContext,
  config: Record<string, unknown>,
  agentId: string,
  timeoutSec: number,
): Promise<AdapterExecutionResult> {
  const port = await lookupGatewayPort(agentId);

  if (!port) {
    await ctx.onLog(
      "stderr",
      `[hermes] No gateway port found for agent ${agentId}. Gateway may not be provisioned yet.\n`,
    );
    return {
      exitCode: 1,
      errorMessage: `Gateway not provisioned for agent ${agentId}`,
      provider: null,
      model: DEFAULT_MODEL,
    };
  }

  const prompt = buildPrompt(ctx, config);
  const sessionId = cfgString(
    (ctx.runtime?.sessionParams as Record<string, unknown> | null)?.sessionId,
  );

  const model = cfgString(config.model) || DEFAULT_MODEL;

  await ctx.onLog(
    "stdout",
    `[hermes] Sending task to gateway on port ${port} (timeout=${timeoutSec}s)\n`,
  );

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutSec * 1000);

  try {
    const response = await fetch(
      `http://localhost:${port}/v1/chat/completions`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${GATEWAY_API_KEY}`,
          ...(sessionId
            ? { "X-Hermes-Session-Id": sessionId }
            : { "X-Hermes-Session-Id": `paperclip-${agentId}` }),
        },
        body: JSON.stringify({
          model: "hermes-agent",
          stream: false,
          messages: [
            { role: "system", content: prompt },
            { role: "user", content: "Work on the assigned task" },
          ],
        }),
        signal: controller.signal,
      },
    );

    clearTimeout(timeout);

    if (!response.ok) {
      const errorBody = await response.text();
      await ctx.onLog(
        "stderr",
        `[hermes] Gateway returned ${response.status}: ${errorBody}\n`,
      );
      return {
        exitCode: 1,
        errorMessage: `Gateway HTTP ${response.status}: ${errorBody.slice(0, 500)}`,
        provider: null,
        model,
        timedOut: false,
      };
    }

    const data = await response.json() as {
      choices: Array<{ message: { content: string } }>;
      usage: { prompt_tokens: number; completion_tokens: number };
    };

    const responseSessionId = response.headers.get("X-Hermes-Session-Id");
    const summary = data.choices?.[0]?.message?.content || "(No response)";
    const usage = data.usage;

    await ctx.onLog("stdout", `[hermes] Gateway response received (${summary.length} chars)\n`);
    if (responseSessionId) {
      await ctx.onLog("stdout", `[hermes] Session: ${responseSessionId}\n`);
    }

    const result: AdapterExecutionResult = {
      exitCode: 0,
      summary: summary.slice(0, 2000),
      provider: null,
      model,
      timedOut: false,
    };

    if (usage) {
      result.usage = {
        inputTokens: usage.prompt_tokens,
        outputTokens: usage.completion_tokens,
      };
    }

    if (responseSessionId) {
      result.sessionParams = { sessionId: responseSessionId };
      result.sessionDisplayId = responseSessionId.slice(0, 16);
    }

    return result;
  } catch (err: unknown) {
    clearTimeout(timeout);

    if (err instanceof DOMException && err.name === "AbortError") {
      await ctx.onLog("stderr", `[hermes] Gateway request timed out after ${timeoutSec}s\n`);
      return {
        exitCode: 1,
        errorMessage: `Gateway request timed out after ${timeoutSec}s`,
        provider: null,
        model,
        timedOut: true,
      };
    }

    const message = err instanceof Error ? err.message : String(err);
    await ctx.onLog("stderr", `[hermes] Gateway request failed: ${message}\n`);
    return {
      exitCode: 1,
      errorMessage: `Gateway request failed: ${message}`,
      provider: null,
      model,
      timedOut: false,
    };
  }
}
```

- [ ] **Step 2: Compile TypeScript**

Run: `cd /mnt/services/hw-rnd-ai-crew/hermes-paperclip-adapter && npm run build`
Expected: Build succeeds, `dist/server/execute.js` updated

- [ ] **Step 3: Commit**

```bash
git add hermes-paperclip-adapter/src/server/execute.ts hermes-paperclip-adapter/dist/server/execute.js
git commit -m "feat(adapter): rewrite execute() for gateway HTTP API"
```

---

## Task 9: Update Adapter index.ts for Gateway Mode

**Files:**
- Modify: `hermes-paperclip-adapter/src/server/index.ts`
- Compile: `hermes-paperclip-adapter/dist/server/index.js`

- [ ] **Step 1: Update index.ts**

Update `detectModel()` and `listSkills()`/`syncSkills()` for gateway mode. The key change: `detectModel()` no longer needs to query ZAI API — the model is configured in the gateway profile. `listSkills()` reads from the profile directory directly.

```typescript
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { DEFAULT_MODEL, GATEWAY_PORTS_FILE } from "../shared/constants.js";

export { execute } from "./execute.js";
export { testEnvironment } from "./test.js";

export async function detectModel(): Promise<{
  model: string;
  provider: string;
  source: string;
  candidates?: string[];
} | null> {
  return {
    model: DEFAULT_MODEL,
    provider: "zai",
    source: "gateway profile (static)",
    candidates: [],
  };
}

import type { AdapterSessionCodec } from "@paperclipai/adapter-utils";

function readNonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

export const sessionCodec: AdapterSessionCodec = {
  deserialize(raw: unknown) {
    if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
    const record = raw as Record<string, unknown>;
    const sessionId =
      readNonEmptyString(record.sessionId) ??
      readNonEmptyString(record.session_id);
    if (!sessionId) return null;
    return { sessionId };
  },
  serialize(params: Record<string, unknown> | null) {
    if (!params) return null;
    const sessionId =
      readNonEmptyString(params.sessionId) ??
      readNonEmptyString(params.session_id);
    if (!sessionId) return null;
    return { sessionId };
  },
  getDisplayId(params: Record<string, unknown> | null) {
    if (!params) return null;
    return readNonEmptyString(params.sessionId) ?? readNonEmptyString(params.session_id);
  },
};

interface SkillEntry {
  name: string;
  enabled: boolean;
  source: string;
}

interface AgentSkillSnapshot {
  desiredSkills: SkillEntry[];
  persistedSkills: SkillEntry[];
}

async function readSkillsFromDir(dir: string): Promise<SkillEntry[]> {
  try {
    const files = await readdir(dir);
    const skills: SkillEntry[] = [];
    for (const file of files) {
      if (file.endsWith(".md")) {
        skills.push({
          name: file.replace(/\.md$/, ""),
          enabled: true,
          source: "profile",
        });
      }
    }
    return skills;
  } catch {
    return [];
  }
}

export async function listSkills(ctx: any): Promise<AgentSkillSnapshot> {
  const agentId = ctx?.agent?.id;
  if (!agentId) {
    return { desiredSkills: [], persistedSkills: [] };
  }

  const skillsDir = `/paperclip/hermes-instances/${agentId}/skills`;
  const skills = await readSkillsFromDir(skillsDir);

  return {
    desiredSkills: skills,
    persistedSkills: skills,
  };
}

export async function syncSkills(ctx: any, desiredSkills: any[]): Promise<AgentSkillSnapshot> {
  return listSkills(ctx);
}
```

- [ ] **Step 2: Compile TypeScript**

Run: `cd /mnt/services/hw-rnd-ai-crew/hermes-paperclip-adapter && npm run build`
Expected: Build succeeds, `dist/server/index.js` updated

- [ ] **Step 3: Commit**

```bash
git add hermes-paperclip-adapter/src/server/index.ts hermes-paperclip-adapter/dist/server/index.js
git commit -m "feat(adapter): update detectModel and listSkills for gateway mode"
```

---

## Task 10: Docker Compose — Add hermes-gateway Service

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add hermes-gateway service to docker-compose.yml**

Add the `hermes-gateway` service and the `gateway_ports` volume. The service needs access to the Hermes agent source, shared config, instances directory, and the Paperclip API.

Add after the `paperclip-mcp` service:

```yaml
  hermes-gateway:
    build:
      context: ./hermes-gateway
      dockerfile: Dockerfile
    container_name: hermes-gateway
    restart: unless-stopped
    depends_on:
      paperclip-server:
        condition: service_started
    environment:
      PAPERCLIP_API_URL: "http://paperclip-server:3100/api"
      HERMES_API_SERVER_KEY: "${HERMES_API_SERVER_KEY:-}"
      TELEGRAM_BOT_TOKEN: "${TELEGRAM_BOT_TOKEN:-}"
      TELEGRAM_CHAT_ID: "${TELEGRAM_CHAT_ID:-}"
      TELEGRAM_ALLOWED_USERS: "${TELEGRAM_ALLOWED_USERS:-}"
      GLM_API_KEY: "${GLM_API_KEY:-}"
      GLM_BASE_URL: "${GLM_BASE_URL:-}"
      GEMINI_API_KEY: "${GEMINI_API_KEY:-}"
      FAL_KEY: "${FAL_KEY:-}"
      TAVILY_API_KEY: "${TAVILY_API_KEY:-}"
      PARALLEL_API_KEY: "${PARALLEL_API_KEY:-}"
      MCP_RAG_API_KEY: "${MCP_RAG_API_KEY:-}"
    volumes:
      - hermes_instances:/paperclip/hermes-instances
      - hermes_venv:/opt/hermes-venv
      - hermes_src:/opt/hermes-agent-build
      - ./hermes-agent:/opt/hermes-agent:ro
      - ./hermes-shared-config:/opt/hermes-shared-config:ro
      - gateway_ports:/run/gateway-ports
    networks:
      - local-ai-internal
```

Add the `gateway_ports` volume:

```yaml
  gateway_ports:
    name: gateway_ports
```

Add the `gateway_ports` volume as read-only to `paperclip-server`:

```yaml
      - gateway_ports:/run/gateway-ports:ro
```

- [ ] **Step 2: Verify docker-compose config is valid**

Run: `cd /mnt/services/hw-rnd-ai-crew && docker compose config --quiet`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(infra): add hermes-gateway service to docker-compose"
```

---

## Task 11: Environment Variables

**Files:**
- Modify: `.env`

- [ ] **Step 1: Add new environment variables to .env**

Add these lines to the end of `.env`:

```
HERMES_API_SERVER_KEY=hermes-gateway-secret-key
TELEGRAM_BOT_TOKEN=8674012815:AAFeQs2ZjNC5l7kmNz92vH7MSX6tK_SHBlE
TELEGRAM_CHAT_ID=
TELEGRAM_ALLOWED_USERS=
```

`TELEGRAM_CHAT_ID` will be filled in after the bot is added to a Telegram chat.

- [ ] **Step 2: Commit**

```bash
git add .env
git commit -m "feat(config): add Telegram and gateway env vars"
```

---

## Task 12: Build and Deploy Gateway

**Files:** None (operational)

- [ ] **Step 1: Build the gateway image**

Run: `docker compose build hermes-gateway`
Expected: Build succeeds

- [ ] **Step 2: Start the gateway**

Run: `docker compose up -d hermes-gateway`
Expected: Container starts, orchestrator begins polling

- [ ] **Step 3: Verify orchestrator is running**

Run: `docker logs hermes-gateway --tail 20`
Expected: Log lines showing "Orchestrator starting...", "Found N agents", provisioning attempts

- [ ] **Step 4: Check port mapping**

Run: `docker exec hermes-gateway cat /run/gateway-ports/ports.json`
Expected: JSON with agent IDs mapped to ports

- [ ] **Step 5: Verify gateway processes are running**

Run: `docker exec hermes-gateway supervisorctl status`
Expected: Multiple `gateway-<id>` processes in RUNNING state

---

## Task 13: Rebuild Paperclip Server with New Adapter

**Files:** None (operational)

- [ ] **Step 1: Restart paperclip-server with new adapter code**

The adapter JS files are bind-mounted, so the new code is already available. Restart to pick up changes:

Run: `docker compose restart paperclip-server`
Expected: Server restarts successfully

- [ ] **Step 2: Verify adapter loads**

Run: `docker logs paperclip-server --tail 20`
Expected: Server starts without adapter errors

---

## Task 14: Test Task Execution via Gateway

**Files:** None (operational)

- [ ] **Step 1: Assign a task to CEO agent via Paperclip UI**

Create a test issue in Paperclip UI and assign it to the CEO agent.

- [ ] **Step 2: Wait for heartbeat and check gateway logs**

Run: `docker logs hermes-gateway --tail 50 -f`
Expected: Gateway receives the chat completion request, agent processes it

- [ ] **Step 3: Check paperclip-server logs for adapter output**

Run: `docker logs paperclip-server --tail 30`
Expected: `[hermes] Sending task to gateway on port 8642` followed by response

- [ ] **Step 4: Verify task completes**

Check Paperclip UI — the task should show as completed with a summary from the agent.

---

## Task 15: Configure and Test Telegram

**Files:**
- Modify: `.env` (add TELEGRAM_CHAT_ID)

- [ ] **Step 1: Add bot to Telegram chat**

Add the bot (created with token `8674012815:AAFeQs2ZjNC5l7kmNz92vH7MSX6tK_SHBlE`) to the target Telegram channel/chat.

- [ ] **Step 2: Get chat ID**

Send a message to the bot/chat, then check the Telegram Bot API:
Run: `curl -s "https://api.telegram.org/bot8674012815:AAFeQs2ZjNC5l7kmNz92vH7MSX6tK_SHBlE/getUpdates" | python3 -m json.tool`
Expected: JSON with update objects containing `chat.id`

- [ ] **Step 3: Update .env with chat ID**

Set `TELEGRAM_CHAT_ID` to the chat ID found in step 2.

- [ ] **Step 4: Restart gateway to pick up new config**

Run: `docker compose restart hermes-gateway`
Expected: Gateway restarts, CEO gateway process now has Telegram enabled

- [ ] **Step 5: Verify Telegram connection**

Run: `docker logs hermes-gateway --tail 30 | grep -i telegram`
Expected: Telegram adapter startup messages

- [ ] **Step 6: Test send_message from agent**

Assign a task that would require clarification. The agent should send a question to the Telegram chat via the `send_message` tool.

- [ ] **Step 7: Commit**

```bash
git add .env
git commit -m "feat(config): set Telegram chat ID"
```

---

## Task 16: Final Verification and Cleanup

**Files:** None (operational)

- [ ] **Step 1: Verify all services are healthy**

Run: `docker compose ps`
Expected: All services Up/healthy

- [ ] **Step 2: Verify gateway handles multiple concurrent agents**

Run: `docker exec hermes-gateway supervisorctl status`
Expected: Multiple gateway processes running, one per Paperclip agent

- [ ] **Step 3: Verify memory persistence**

Run: `docker exec hermes-gateway ls /paperclip/hermes-instances/*/memories/`
Expected: MEMORY.md and USER.md files in agent profile directories

- [ ] **Step 4: Verify session persistence**

Run: `docker exec hermes-gateway ls /paperclip/hermes-instances/*/sessions/`
Expected: Session JSONL files after tasks have been executed

- [ ] **Step 5: Final commit with all changes**

```bash
git add -A
git commit -m "feat(gateway): complete Hermes Gateway-in-a-Box deployment with Telegram"
```
