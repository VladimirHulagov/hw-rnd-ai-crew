from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import httpx
from aiohttp import web
from mcp import types
from mcp.server import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport

from skill_importer import _parse_frontmatter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [skill-sync-mcp] %(levelname)s: %(message)s",
    datefmt="[%H:%M:%S]",
)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("SKILL_SYNC_PORT", "8683"))
API_KEY = os.environ.get("SKILL_SYNC_API_KEY", "")
PROFILES_DIR = Path(os.environ.get("PROFILES_DIR", "/root/.hermes/profiles"))
FORGEJO_URL = os.environ.get("FORGEJO_URL", "")
FORGEJO_TOKEN = os.environ.get("FORGEJO_TOKEN", "")
FORGEJO_OWNER = os.environ.get("FORGEJO_OWNER", "")
FORGEJO_REPO = os.environ.get("FORGEJO_REPO", "skills")
GIT_AUTHOR = "Skill Sync <skill-sync@hermes>"


mcp_server = Server("skill-sync")


class SkillSyncServer:
    def __init__(self):
        self.forgejo_url = os.environ.get("FORGEJO_URL", FORGEJO_URL)
        self.forgejo_token = os.environ.get("FORGEJO_TOKEN", FORGEJO_TOKEN)
        self.forgejo_owner = os.environ.get("FORGEJO_OWNER", FORGEJO_OWNER)
        self.forgejo_repo = os.environ.get("FORGEJO_REPO", FORGEJO_REPO)
        self.profiles_dir = Path(os.environ.get("PROFILES_DIR", str(PROFILES_DIR)))

    def _agent_tag(self, agent_id: str) -> str:
        return hashlib.md5(agent_id.encode()).hexdigest()[:12]

    def _resolve_repo_url(self, override_url: str | None = None) -> str:
        if override_url:
            return override_url
        if self.forgejo_url and self.forgejo_owner:
            base = self.forgejo_url.rstrip("/")
            return f"{base}/{self.forgejo_owner}/{self.forgejo_repo}.git"
        return ""

    def _sync_branch(self, agent_id: str) -> str:
        return f"skills-sync/{self._agent_tag(agent_id)}"

    def _repo_dir(self, agent_id: str) -> Path:
        return Path(f"/tmp/skill-sync-{self._agent_tag(agent_id)}")

    def _git_env(self) -> dict:
        return {
            "GIT_AUTHOR_NAME": "Skill Sync",
            "GIT_AUTHOR_EMAIL": "skill-sync@hermes",
            "GIT_COMMITTER_NAME": "Skill Sync",
            "GIT_COMMITTER_EMAIL": "skill-sync@hermes",
        }

    def _git(self, *args, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, **self._git_env()}
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
        )

    def _ensure_repo(self, agent_id: str, repo_url: str) -> bool:
        repo_dir = self._repo_dir(agent_id)
        if (repo_dir / ".git").is_dir():
            r = self._git("pull", "--rebase", cwd=repo_dir)
            if r.returncode != 0:
                logger.warning("git pull failed: %s", r.stderr)
                shutil.rmtree(repo_dir, ignore_errors=True)
                return self._ensure_repo(agent_id, repo_url)
            return True
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        r = self._git("clone", repo_url, str(repo_dir), cwd=repo_dir.parent)
        if r.returncode != 0:
            shutil.rmtree(repo_dir, ignore_errors=True)
            return False
        return True

    def _parse_conflicts(self, output: str) -> list[dict]:
        conflicts = []
        current_path = None
        current_lines = []
        in_conflict = False
        for line in output.splitlines():
            if line.startswith("CONFLICT "):
                m = re.search(r"Merge conflict in (.+)", line)
                if m:
                    current_path = m.group(1).strip()
            elif line.startswith("<<<<<<<"):
                in_conflict = True
                current_lines = []
            elif line.startswith("=======") and in_conflict:
                yours = "\n".join(current_lines)
                current_lines = []
            elif line.startswith(">>>>>>>") and in_conflict:
                theirs = "\n".join(current_lines)
                if current_path:
                    conflicts.append({"path": current_path, "yours": yours, "theirs": theirs})
                in_conflict = False
                current_path = None
                current_lines = []
            elif in_conflict:
                current_lines.append(line)
        return conflicts

    def _parse_repo_info(self, repo_url: str) -> tuple[str, str]:
        m = re.match(r"(?:https?://)?([^/]+)/([^/]+)/([^/.]+)", repo_url or "")
        if m:
            return m.group(2), m.group(3)
        return "", ""

    def _forgejo_api(self, method: str, path: str, json_body: dict | None = None):
        if not self.forgejo_url or not self.forgejo_token:
            return None
        base = self.forgejo_url.rstrip("/")
        headers = {
            "Authorization": f"token {self.forgejo_token}",
            "Content-Type": "application/json",
        }
        with httpx.Client() as client:
            resp = client.request(method, f"{base}{path}", headers=headers, json=json_body)
            if resp.status_code < 300:
                try:
                    return resp.json()
                except Exception:
                    return None
            logger.warning("Forgejo API %s %s returned %d", method, path, resp.status_code)
            return None

    def _create_or_update_pr(self, agent_id: str, repo_url: str, branch: str, title: str, body: str):
        owner, repo = self._parse_repo_info(repo_url)
        if not owner or not repo:
            return None
        pulls = self._forgejo_api(
            "GET",
            f"/api/v1/repos/{owner}/{repo}/pulls?state=open&head={owner}:{branch}",
        )
        if pulls and isinstance(pulls, list) and len(pulls) > 0:
            pr = pulls[0]
            number = pr.get("number")
            return self._forgejo_api(
                "PATCH",
                f"/api/v1/repos/{owner}/{repo}/pulls/{number}",
                {"title": title, "body": body},
            )
        return self._forgejo_api(
            "POST",
            f"/api/v1/repos/{owner}/{repo}/pulls",
            {"title": title, "head": branch, "base": "main", "body": body},
        )

    def _skill_info(self, markdown: str) -> dict:
        fm = _parse_frontmatter(markdown or "")
        return {"name": fm.get("name", ""), "description": fm.get("description", "")}

    def push(self, agent_id: str, repo_url: str | None = None) -> dict:
        resolved_url = self._resolve_repo_url(repo_url)
        if not resolved_url:
            return {"error": "No repo URL configured"}
        if not self._ensure_repo(agent_id, resolved_url):
            return {"error": "Failed to clone/fetch repo"}

        repo_dir = self._repo_dir(agent_id)
        branch = self._sync_branch(agent_id)

        self._git("fetch", "origin", cwd=repo_dir)
        remote_branch = f"origin/{branch}"
        has_remote = self._git("rev-parse", "--verify", remote_branch, cwd=repo_dir).returncode == 0

        r = self._git("checkout", branch, cwd=repo_dir)
        if r.returncode != 0:
            self._git("checkout", "main", cwd=repo_dir)
            self._git("reset", "--hard", "origin/main", cwd=repo_dir)
            self._git("checkout", "-b", branch, cwd=repo_dir)
        else:
            if has_remote:
                r = self._git("merge", remote_branch, "--no-edit", cwd=repo_dir)
                if r.returncode != 0:
                    conflicts = self._parse_conflicts(r.stdout + "\n" + r.stderr)
                    self._git("merge", "--abort", cwd=repo_dir)
                    self._git("checkout", "main", cwd=repo_dir)
                    return {"conflict": True, "files": conflicts}

            r = self._git("merge", "origin/main", "--no-edit", cwd=repo_dir)
            if r.returncode != 0:
                conflicts = self._parse_conflicts(r.stdout + "\n" + r.stderr)
                self._git("merge", "--abort", cwd=repo_dir)
                self._git("checkout", "main", cwd=repo_dir)
                return {"conflict": True, "files": conflicts}

        profile_skills = self.profiles_dir / agent_id / "skills"
        if not profile_skills.is_dir():
            self._git("checkout", "main", cwd=repo_dir)
            return {"error": f"No skills directory for agent {agent_id}"}

        pushed = []
        for skill_md in sorted(profile_skills.rglob("SKILL.md")):
            if skill_md.is_symlink():
                continue
            rel = skill_md.parent.relative_to(profile_skills)
            parts = rel.parts
            if len(parts) < 2:
                continue
            category = parts[0]
            slug = parts[-1]
            dst_dir = repo_dir / "skills" / category / slug
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_md, dst_dir / "SKILL.md")
            for src_file in skill_md.parent.rglob("*"):
                if not src_file.is_file() or src_file.name == "SKILL.md":
                    continue
                if src_file.is_symlink():
                    continue
                file_rel = src_file.relative_to(skill_md.parent)
                if any(p == "__pycache__" for p in file_rel.parts):
                    continue
                dst = dst_dir / file_rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst)
            text = skill_md.read_text(encoding="utf-8")
            info = self._skill_info(text)
            pushed.append({"category": category, "slug": slug, **info})

        self._git("add", "skills", cwd=repo_dir)
        status = self._git("status", "--porcelain", "--", "skills", cwd=repo_dir)
        if not status.stdout.strip():
            self._git("checkout", "main", cwd=repo_dir)
            return {"pushed": 0, "skills": pushed, "unchanged": True}

        n = len(pushed)
        self._git("commit", "-m", f"sync skills: {n} pushed", cwd=repo_dir)
        self._git("push", "origin", branch, cwd=repo_dir)

        title = f"Skills sync: +{n}"
        body_parts = [f"### Pushed ({n})\n"]
        for s in pushed:
            body_parts.append(f"- **{s['slug']}** ({s['category']})")
        body = "\n".join(body_parts)
        self._create_or_update_pr(agent_id, resolved_url, branch, title, body)

        self._git("checkout", "main", cwd=repo_dir)
        return {"pushed": n, "skills": pushed}

    def pull(self, agent_id: str, repo_url: str | None = None) -> dict:
        resolved_url = self._resolve_repo_url(repo_url)
        if not resolved_url:
            return {"error": "No repo URL configured"}
        if not self._ensure_repo(agent_id, resolved_url):
            return {"error": "Failed to clone/fetch repo"}

        repo_dir = self._repo_dir(agent_id)
        self._git("fetch", "origin", cwd=repo_dir)
        self._git("checkout", "main", cwd=repo_dir)
        self._git("reset", "--hard", "origin/main", cwd=repo_dir)

        skills_src = repo_dir / "skills"
        if not skills_src.is_dir():
            return {"imported": 0, "skills": []}

        profile_skills = self.profiles_dir / agent_id / "skills"
        imported = []
        for skill_md in sorted(skills_src.rglob("SKILL.md")):
            rel = skill_md.parent.relative_to(skills_src)
            parts = rel.parts
            if len(parts) < 2:
                continue
            category = parts[0]
            slug = parts[-1]
            dst_dir = profile_skills / category / slug
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_md, dst_dir / "SKILL.md")
            for src_file in skill_md.parent.rglob("*"):
                if not src_file.is_file() or src_file.name == "SKILL.md":
                    continue
                file_rel = src_file.relative_to(skill_md.parent)
                if any(p == "__pycache__" for p in file_rel.parts):
                    continue
                dst = dst_dir / file_rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst)
            text = skill_md.read_text(encoding="utf-8")
            info = self._skill_info(text)
            imported.append({"category": category, "slug": slug, **info})

        return {"imported": len(imported), "skills": imported}

    def list_remote(self, repo_url: str | None = None, category: str | None = None) -> dict:
        resolved_url = self._resolve_repo_url(repo_url)
        if not resolved_url:
            return {"error": "No repo URL configured"}

        owner, repo = self._parse_repo_info(resolved_url)
        if not owner or not repo:
            return {"error": "Cannot parse repo URL"}

        path = "skills"
        if category:
            path = f"skills/{category}"

        result = self._forgejo_api(
            "GET",
            f"/api/v1/repos/{owner}/{repo}/contents/{path}",
        )
        if not result:
            return {"skills": []}

        if isinstance(result, list):
            dirs = [item for item in result if item.get("type") == "dir"]
        elif isinstance(result, dict) and result.get("type") == "dir":
            dirs = [result]
        else:
            dirs = []

        skills = []
        for d in dirs:
            cat_name = d.get("name", "")
            if category and cat_name != category:
                cat_name = category
            contents = self._forgejo_api(
                "GET",
                f"/api/v1/repos/{owner}/{repo}/contents/{d.get('path', cat_name)}",
            )
            if not isinstance(contents, list):
                continue
            for item in contents:
                if item.get("type") != "dir":
                    continue
                slug = item.get("name", "")
                skill_resp = self._forgejo_api(
                    "GET",
                    f"/api/v1/repos/{owner}/{repo}/contents/{item['path']}/SKILL.md",
                )
                if not isinstance(skill_resp, dict) or "content" not in skill_resp:
                    continue
                import base64
                try:
                    md_text = base64.b64decode(skill_resp["content"]).decode("utf-8")
                except Exception:
                    continue
                fm = _parse_frontmatter(md_text)
                skills.append({
                    "category": cat_name,
                    "slug": slug,
                    "name": fm.get("name", slug),
                    "description": fm.get("description", ""),
                })

        return {"skills": skills}


server_instance = SkillSyncServer()


def _check_auth(request: web.Request) -> bool:
    if not API_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == API_KEY
    return False


@mcp_server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="skill_push",
            description="Push agent skills to Forgejo repository. Clones/fetches repo, copies skills from "
                        "agent profile, commits and pushes to a sync branch, creates/updates a PR.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent UUID"},
                    "repo_url": {"type": "string", "description": "Override repo URL (optional)"},
                },
                "required": ["agent_id"],
            },
        ),
        types.Tool(
            name="skill_pull",
            description="Pull skills from Forgejo repository into agent profile directory. "
                        "Fetches latest from origin/main and copies new/updated skills.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent UUID"},
                    "repo_url": {"type": "string", "description": "Override repo URL (optional)"},
                },
                "required": ["agent_id"],
            },
        ),
        types.Tool(
            name="skill_list_remote",
            description="List skills from Forgejo repository via API. Returns skill metadata "
                        "(name, slug, category, description) parsed from SKILL.md frontmatter.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_url": {"type": "string", "description": "Override repo URL (optional)"},
                    "category": {"type": "string", "description": "Filter by category (optional)"},
                },
            },
        ),
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    arguments = arguments or {}
    try:
        if name == "skill_push":
            agent_id = arguments.get("agent_id", "")
            if not agent_id:
                return [types.TextContent(type="text", text="Error: agent_id is required")]
            result = await asyncio.to_thread(
                server_instance.push, agent_id, arguments.get("repo_url")
            )
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

        elif name == "skill_pull":
            agent_id = arguments.get("agent_id", "")
            if not agent_id:
                return [types.TextContent(type="text", text="Error: agent_id is required")]
            result = await asyncio.to_thread(
                server_instance.pull, agent_id, arguments.get("repo_url")
            )
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

        elif name == "skill_list_remote":
            result = await asyncio.to_thread(
                server_instance.list_remote,
                arguments.get("repo_url"),
                arguments.get("category"),
            )
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return [types.TextContent(type="text", text=f"Error: {e}")]


_transport = StreamableHTTPServerTransport(
    mcp_session_id=None,
    is_json_response_enabled=True,
)
_http_task = None


async def _run_http_server():
    async with _transport.connect() as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


async def _ensure_http_server():
    global _http_task
    if _http_task is None:
        _http_task = asyncio.ensure_future(_run_http_server())
        await asyncio.sleep(0.1)


async def _asgi_handler(scope, receive, send):
    if not API_KEY:
        pass
    else:
        headers = {}
        for key, value in scope.get("headers", []):
            headers[key.decode()] = value.decode()
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header[7:] != API_KEY:
            body = b'{"error":"unauthorized"}'
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [[b"content-type", b"application/json"], [b"content-length", str(len(body)).encode()]],
            })
            await send({"type": "http.response.body", "body": body})
            return
    await _ensure_http_server()
    await _transport.handle_request(scope, receive, send)


async def _handle_aiohttp(request: web.Request) -> web.StreamResponse:
    scope = {
        "type": "http",
        "method": request.method,
        "path": request.path,
        "query_string": request.query_string.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in request.headers.items()],
        "server": ("0.0.0.0", PORT),
    }

    body = await request.read()
    body_sent = False

    async def receive():
        nonlocal body_sent
        if body_sent:
            return {"type": "http.disconnect"}
        body_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    status_code = 200
    headers_list = []
    chunks = []

    async def send(message):
        nonlocal status_code, headers_list
        if message["type"] == "http.response.start":
            status_code = message["status"]
            headers_list = [(h[0].decode(), h[1].decode()) for h in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await _asgi_handler(scope, receive, send)
    resp_body = b"".join(chunks) if chunks else b""

    return web.Response(
        status=status_code,
        headers=dict(headers_list),
        body=resp_body,
    )


async def main():
    logger.info("Skill Sync MCP server starting on port %d", PORT)

    web_app = web.Application()
    web_app.router.add_route("*", "/mcp", _handle_aiohttp)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Listening on 0.0.0.0:%d", PORT)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
