import json
import os
import re
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
    telegram_clarify_timeout: int | None = None,
    agent_name: str | None = None,
    paperclip_api_key: str = "",
    outline_api_key: str | None = None,
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
        "outline_api_key": outline_api_key or os.environ.get("MCP_OUTLINE_API_KEY", ""),
        "memory_api_key": os.environ.get("MEMORY_API_KEY", ""),
        "MCP_RAG_URL": os.environ.get("MCP_RAG_URL", ""),
        "MCP_OUTLINE_URL": os.environ.get("MCP_OUTLINE_URL", ""),
        "paperclip_api_key": paperclip_api_key,
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
        mention_patterns = []
        if agent_name:
            mention_patterns.append(f"\\b{re.escape(agent_name)}\\b")
        if telegram_chat_id:
            platforms_lines.append("    extra:")
            platforms_lines.append(f"      home_channel: \"{telegram_chat_id}\"")
            if telegram_allowed_users:
                platforms_lines.append(f"      allowed_users: \"{telegram_allowed_users}\"")
            if telegram_clarify_timeout:
                platforms_lines.append(f"      clarify_timeout: {telegram_clarify_timeout}")
            platforms_lines.append("      require_mention: true")
            if mention_patterns:
                platforms_lines.append(f"      mention_patterns: {json.dumps(mention_patterns)}")

    values["platforms_section"] = "\n".join(platforms_lines)

    return _substitute(template, values)


def ensure_profile_dirs(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "memories").mkdir(exist_ok=True)
    (profile_dir / "skills").mkdir(exist_ok=True)
    (profile_dir / "sessions").mkdir(exist_ok=True)
