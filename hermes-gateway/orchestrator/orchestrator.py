import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import re as re_module
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(__file__))

from config_generator import ensure_profile_dirs, generate_profile_config
from port_manager import PortManager
from supervisor_client import SupervisorClient

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
DATABASE_URL = os.environ.get("DATABASE_URL", "postgres://paperclip:paperclip@paperclip-db:5432/paperclip")
HERMES_HOME_DEFAULT = Path.home() / ".hermes"

_BWORD = "\\b"


def _mention_patterns_val(enable_telegram: bool, name: str | None) -> str:
    if not enable_telegram or not name:
        return ""
    return _BWORD + re_module.escape(name) + _BWORD


def _ensure_hermes_installed():
    if shutil.which("hermes"):
        _patch_installed_agent()
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
    _patch_installed_agent()
    logger.info("hermes-agent installed.")


def _patch_installed_agent():
    site = Path("/usr/local/lib/python3.11/site-packages")
    src_dir = Path(__file__).parent.parent / "hermes-agent"

    _patched = []
    for rel in [
        "gateway/platforms/api_server.py",
        "gateway/platforms/telegram.py",
        "model_tools.py",
        "agent/display.py",
        "agent/prompt_builder.py",
    ]:
        dst = site / rel
        src = src_dir / rel
        if dst.exists() and src.exists():
            if hashlib.md5(dst.read_bytes()).hexdigest() != hashlib.md5(src.read_bytes()).hexdigest():
                shutil.copy2(src, dst)
                _patched.append(rel)

    if _patched:
        logger.info("Patched from submodule: %s", ", ".join(_patched))

    bridge_src = Path(__file__).parent / "clarify_bridge.py"
    bridge_dst = site / "clarify_bridge.py"
    if bridge_src.exists():
        if not bridge_dst.exists() or hashlib.md5(bridge_dst.read_bytes()).hexdigest() != hashlib.md5(bridge_src.read_bytes()).hexdigest():
            shutil.copy2(bridge_src, bridge_dst)
            _patched.append("clarify_bridge.py")


def _ensure_profiles_root():
    profiles_dir = HERMES_HOME_DEFAULT / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    return profiles_dir


def fetch_agents_from_db() -> list[dict]:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.set_session(autocommit=True, readonly=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT a.id, a.name, a.role, a.company_id, a.adapter_config
            FROM agents a
            JOIN company_memberships cm
                ON cm.principal_id = a.id::text
                AND cm.principal_type = 'agent'
            WHERE a.adapter_type = 'hermes_local'
              AND a.status NOT IN ('terminated', 'paused')
            ORDER BY a.name
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        agents = []
        for row in rows:
            agents.append({
                "id": str(row["id"]),
                "name": row["name"],
                "role": row["role"],
                "companyId": str(row["company_id"]),
                "adapter_config": row["adapter_config"] or {},
            })
        return agents
    except Exception as e:
        logger.error("Failed to fetch agents from DB: %s", e)
        return []


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _create_agent_jwt(agent_id: str, company_id: str) -> str:
    secret = os.environ.get("BETTER_AUTH_SECRET", "")
    if not secret:
        return ""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = math.floor(time.time())
    claims = _b64url(json.dumps({
        "sub": agent_id,
        "company_id": company_id,
        "adapter_type": "hermes_local",
        "run_id": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 86400,
        "iss": "paperclip",
        "aud": "paperclip-api",
    }).encode())
    signing_input = f"{header}.{claims}"
    sig = _b64url(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{sig}"


def _build_soul_md(role: str, name: str) -> str:
    if role in ("ceo", "cto"):
        return (
            f"You are {name} — a leadership agent in the Paperclip task management system.\n"
            "Your job is strategy, prioritization, coordination, and delegation.\n\n"
            "## Core behavior\n\n"
            "- **Delegate execution work.** Never code, research, or write documents yourself.\n"
            "- **Post decisions as comments.** Every triage, delegation, or approval gets a comment on the issue.\n"
            "- **Follow up on reports.** Check in on delegated tasks that have no activity.\n"
            "- **Use `clarify` to ask the board questions** when you need human judgment.\n"
            "- **Act, don't describe.** Every response must contain either tool calls or a final decision. "
            "Never describe what you plan to do without doing it.\n\n"
            "## Knowledge base (Outline)\n\n"
            "- Use `mcp_outline_search` to look up existing knowledge before making decisions.\n"
            "- When a decision or strategy is finalized, create a document in Outline "
            "to keep the knowledge base up to date.\n"
        )
    return (
        f"You are {name} — a worker agent in the Paperclip task management system.\n"
        "Your job is to execute tasks: research, code, test, document, analyze.\n\n"
        "## Core behavior\n\n"
        "- **Act, don't describe.** Every response must contain either tool calls that make progress, "
        "or a final deliverable. Never end your turn describing what you will do next — do it now.\n"
        "- **Save before you post.** Persist artifacts to disk first, then post results as a comment "
        "on the issue. Runs can be interrupted at any time — if results exist only in conversation memory, "
        "they are lost.\n"
        "- **Post final results only.** Do not post intermediate thoughts, plans, or \"I will now...\" "
        "messages as issue comments. Comments should contain completed deliverables: research findings, "
        "code changes, test results, documentation.\n"
        "- **Close tasks when done.** Move completed issues to \"done\" status with a summary comment.\n"
        "- **Escalate when blocked.** If you cannot proceed, post a blocker comment and reassign. "
        "Use `clarify` to ask the board a question in Telegram.\n\n"
        "## Knowledge base (Outline)\n\n"
        "- Before starting research, use `mcp_outline_search` to check if relevant knowledge already exists.\n"
        "- When you complete research, an investigation, or produce a how-to guide — "
        "create or update a document in Outline using `mcp_outline_create_document` or "
        "`mcp_outline_update_document`.\n"
        "- Write clear, structured documents: use headings, bullet lists, code blocks.\n"
        "- Avoid creating duplicates — search first, update existing documents when possible.\n"
    )


def _write_ports_json(ports: dict[str, int]):
    PORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORTS_FILE.write_text(json.dumps(ports, indent=2) + "\n")


def _compute_source_fingerprint() -> str:
    parts: list[str] = []
    for p in [Path("/opt/config-template.yaml"), Path(__file__), Path(__file__).parent / "config_generator.py"]:
        if p.exists():
            parts.append(p.read_text())
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:16]


class Orchestrator:
    def __init__(self):
        self.port_manager = PortManager()
        self.supervisor = SupervisorClient()
        self.profiles_root = _ensure_profiles_root()
        self._running_agent_ids: set[str] = set()
        self._known_agents: dict[str, dict] = {}
        self._source_fingerprint: str | None = None

    def _profile_dir(self, agent_id: str) -> Path:
        return self.profiles_root / agent_id

    def _gateway_name(self, agent_id: str) -> str:
        return f"gateway-{agent_id[:12]}"

    def _check_source_changed(self) -> bool:
        current = _compute_source_fingerprint()
        if self._source_fingerprint is None:
            self._source_fingerprint = current
            return False
        if current != self._source_fingerprint:
            self._source_fingerprint = current
            return True
        return False

    def _agent_data_changed(self, agent_id: str, agent: dict) -> bool:
        stored = self._known_agents.get(agent_id)
        if not stored:
            return True
        return (
            stored.get("role") != agent.get("role")
            or stored.get("name") != agent.get("name")
            or stored.get("adapter_config") != agent.get("adapter_config")
        )

    async def _restart_agent(self, agent_id: str, agent: dict):
        proc_name = self._gateway_name(agent_id)
        logger.info("Restarting agent %s (config or data changed)", agent_id[:8])
        self.supervisor.stop_process(proc_name)
        self._running_agent_ids.discard(agent_id)
        await self.provision_agent(agent)

    async def provision_agent(self, agent: dict):
        agent_id = agent.get("id", "")
        if not agent_id:
            return

        port = self.port_manager.allocate(agent_id)
        profile_dir = self._profile_dir(agent_id)

        ensure_profile_dirs(profile_dir)

        adapter_config = agent.get("adapter_config", {}) or {}
        agent_messaging = adapter_config.get("messaging", {}) or {}
        agent_telegram = agent_messaging.get("telegram", {})
        enable_telegram = (
            agent_telegram.get("enabled", False)
            and bool(agent_telegram.get("botToken"))
            and bool(agent_telegram.get("chatId"))
        )

        name = agent.get("name", "Agent")
        company_id = agent.get("companyId", agent.get("company_id", ""))
        agent_jwt = _create_agent_jwt(agent_id, company_id)

        config = generate_profile_config(
            agent_id=agent_id,
            company_id=company_id,
            allocated_port=port,
            telegram_bot_token=agent_telegram.get("botToken") if enable_telegram else None,
            telegram_chat_id=agent_telegram.get("chatId") if enable_telegram else None,
            telegram_allowed_users=agent_telegram.get("allowedUsers") if enable_telegram else None,
            telegram_clarify_timeout=agent_telegram.get("defaultTimeout", 600) if enable_telegram else None,
            agent_name=name,
            paperclip_api_key=agent_jwt,
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

        role = agent.get("role", "general")
        soul_content = _build_soul_md(role, name)
        soul_path = profile_dir / "SOUL.md"
        if not soul_path.exists() or soul_path.read_text() != soul_content:
            soul_path.write_text(soul_content)

        env_content = "\n".join([
            f"GLM_API_KEY={os.environ.get('GLM_API_KEY', '')}",
            f"GLM_BASE_URL={os.environ.get('GLM_BASE_URL', '')}",
            f"GEMINI_API_KEY={os.environ.get('GEMINI_API_KEY', '')}",
            f"TAVILY_API_KEY={os.environ.get('TAVILY_API_KEY', '')}",
            f"PARALLEL_API_KEY={os.environ.get('PARALLEL_API_KEY', '')}",
            f"FAL_KEY={os.environ.get('FAL_KEY', '')}",
            f"TELEGRAM_BOT_TOKEN={agent_telegram.get('botToken', '') if enable_telegram else ''}",
            f"TELEGRAM_CHAT_ID={agent_telegram.get('chatId', '') if enable_telegram else ''}",
            f"TELEGRAM_CLARIFY_TIMEOUT={agent_telegram.get('defaultTimeout', 600) if enable_telegram else '600'}",
            f"TELEGRAM_ALLOWED_USERS={agent_telegram.get('allowedUsers', '') if enable_telegram else ''}",
            "TELEGRAM_REQUIRE_MENTION=true",
            f"TELEGRAM_MENTION_PATTERNS={_mention_patterns_val(enable_telegram, name)}",
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
            f"environment=HERMES_HOME=\"{profile_dir}\",PAPERCLIP_RUN_API_KEY=\"{agent_jwt}\",TELEGRAM_BOT_TOKEN=\"{agent_telegram.get('botToken', '') if enable_telegram else ''}\",TELEGRAM_CHAT_ID=\"{agent_telegram.get('chatId', '') if enable_telegram else ''}\",TELEGRAM_CLARIFY_TIMEOUT=\"{agent_telegram.get('defaultTimeout', 600) if enable_telegram else '600'}\",TELEGRAM_ALLOWED_USERS=\"{agent_telegram.get('allowedUsers', '') if enable_telegram else ''}\",TELEGRAM_REQUIRE_MENTION=\"true\"\n"
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
        source_changed = self._check_source_changed()

        for agent in agents:
            agent_id = agent.get("id", "")
            try:
                if agent_id not in self._running_agent_ids:
                    await self.provision_agent(agent)
                elif source_changed or self._agent_data_changed(agent_id, agent):
                    await self._restart_agent(agent_id, agent)
            except Exception as e:
                logger.error("Failed to reconcile agent %s (%s): %s", agent.get("name", "?"), agent_id[:8], e)
            self._known_agents[agent_id] = agent

        for agent_id in list(self._running_agent_ids):
            if agent_id not in current_ids:
                await self.deprovision_agent(agent_id)
                del self._known_agents[agent_id]

        _write_ports_json(self.port_manager.get_all())

    async def run(self):
        logger.info("Orchestrator starting...")
        logger.info("Database: %s", DATABASE_URL.replace("paperclip:paperclip@", "***@") if "paperclip:paperclip" in DATABASE_URL else DATABASE_URL)
        logger.info("Poll interval: %ds", POLL_INTERVAL)

        _ensure_hermes_installed()
        _ensure_profiles_root()

        while True:
            try:
                agents = fetch_agents_from_db()
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
