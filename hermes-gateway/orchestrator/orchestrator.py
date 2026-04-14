import asyncio
import json
import logging
import os
import shutil
import sys
import time
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


def fetch_agents_from_db() -> list[dict]:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.set_session(autocommit=True, readonly=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, name, role, company_id FROM agents ORDER BY name")
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
            })
        return agents
    except Exception as e:
        logger.error("Failed to fetch agents from DB: %s", e)
        return []


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
