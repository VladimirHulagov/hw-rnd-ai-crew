from __future__ import annotations

import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [team-skills] %(levelname)s: %(message)s",
    datefmt="[%H:%M:%S]",
)
logger = logging.getLogger(__name__)

PROFILES_DIR = Path("/root/.hermes/profiles")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
API_KEY = os.environ.get("TEAM_SKILLS_API_KEY", "")

app = FastAPI(title="Team Skills API")
_bearer_scheme = HTTPBearer(auto_error=False)

_agent_name_cache: dict[str, Optional[str]] = {}


async def _check_auth(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme)):
    if not API_KEY:
        return
    if not credentials or credentials.scheme.lower() != "bearer" or credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _get_agent_name(agent_id: str) -> Optional[str]:
    if agent_id in _agent_name_cache:
        return _agent_name_cache[agent_id]
    try:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM agents WHERE id = %s", (agent_id,))
                row = cur.fetchone()
                name = row[0] if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.warning("DB lookup failed for agent %s: %s", agent_id, e)
        name = None
    _agent_name_cache[agent_id] = name
    return name


def _parse_frontmatter(text: str) -> dict:
    meta: dict = {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return meta
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [t.strip().strip("'\"") for t in val[1:-1].split(",")]
            meta[key] = [i for i in items if i]
        elif val.startswith('"') and val.endswith('"'):
            meta[key] = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            meta[key] = val[1:-1]
        else:
            meta[key] = val
    return meta


def _count_files(directory: Path) -> int:
    return sum(1 for _ in directory.rglob("*") if _.is_file())


def _scan_skills() -> list[dict]:
    skills = []
    if not PROFILES_DIR.is_dir():
        return skills
    for agent_dir in sorted(PROFILES_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_id = agent_dir.name
        agent_name = _get_agent_name(agent_id) or agent_id[:8]
        skills_dir = agent_dir / "skills"
        if not skills_dir.is_dir():
            continue
        for category_dir in sorted(skills_dir.iterdir()):
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            for skill_dir in sorted(category_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_name = skill_dir.name
                skill_md = skill_dir / "SKILL.md"
                try:
                    text = skill_md.read_text(encoding="utf-8")
                    meta = _parse_frontmatter(text)
                    mtime = datetime.fromtimestamp(
                        skill_md.stat().st_mtime, tz=timezone.utc
                    )
                    ctime = datetime.fromtimestamp(
                        skill_md.stat().st_ctime, tz=timezone.utc
                    )
                except Exception:
                    continue
                skills.append(
                    {
                        "agentId": agent_id,
                        "agentName": agent_name,
                        "category": category,
                        "skillName": skill_name,
                        "path": f"skills/{category}/{skill_name}",
                        "description": meta.get("description", ""),
                        "tags": meta.get("tags", []),
                        "version": meta.get("version", ""),
                        "fileCount": _count_files(skill_dir),
                        "createdAt": ctime.isoformat(),
                        "modifiedAt": mtime.isoformat(),
                    }
                )
    return skills


def _skill_dir(agent_id: str, category: str, skill_name: str) -> Path:
    d = PROFILES_DIR / agent_id / "skills" / category / skill_name
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="Skill not found")
    return d


def _list_files(skill_dir: Path) -> list[dict]:
    files = []
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(skill_dir).as_posix()
            kind = "skill" if rel == "SKILL.md" else "other"
            files.append({"path": rel, "kind": kind})
    return files


@app.get("/team-skills")
async def list_skills(_=Depends(_check_auth)):
    return _scan_skills()


@app.get("/team-skills/{agent_id}/{category}/{skill_name}")
async def get_skill(agent_id: str, category: str, skill_name: str, _=Depends(_check_auth)):
    d = _skill_dir(agent_id, category, skill_name)
    skill_md = d / "SKILL.md"
    try:
        markdown = skill_md.read_text(encoding="utf-8")
        ctime = datetime.fromtimestamp(skill_md.stat().st_ctime, tz=timezone.utc)
        mtime = datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read SKILL.md: {e}")
    meta = _parse_frontmatter(markdown)
    return {
        "agentId": agent_id,
        "agentName": _get_agent_name(agent_id) or agent_id[:8],
        "category": category,
        "skillName": skill_name,
        "description": meta.get("description", ""),
        "tags": meta.get("tags", []),
        "version": meta.get("version", ""),
        "createdAt": ctime.isoformat(),
        "modifiedAt": mtime.isoformat(),
        "markdown": markdown,
        "files": _list_files(d),
    }


class SkillUpdate(BaseModel):
    markdown: str


@app.put("/team-skills/{agent_id}/{category}/{skill_name}")
async def update_skill(agent_id: str, category: str, skill_name: str, body: SkillUpdate, _=Depends(_check_auth)):
    d = _skill_dir(agent_id, category, skill_name)
    skill_md = d / "SKILL.md"
    skill_md.write_text(body.markdown, encoding="utf-8")
    return {"status": "ok"}


@app.delete("/team-skills/{agent_id}/{category}/{skill_name}")
async def delete_skill(agent_id: str, category: str, skill_name: str, _=Depends(_check_auth)):
    d = _skill_dir(agent_id, category, skill_name)
    shutil.rmtree(d)
    return {"status": "deleted"}


@app.get("/team-skills/{agent_id}/{category}/{skill_name}/files/{file_path:path}")
async def read_file(agent_id: str, category: str, skill_name: str, file_path: str, _=Depends(_check_auth)):
    d = _skill_dir(agent_id, category, skill_name)
    target = (d / file_path).resolve()
    if not str(target).startswith(str(d.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal denied")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read file: {e}")
    return {"path": file_path, "content": content}


class FileWrite(BaseModel):
    content: str


@app.put("/team-skills/{agent_id}/{category}/{skill_name}/files/{file_path:path}")
async def write_file(
    agent_id: str, category: str, skill_name: str, file_path: str, body: FileWrite, _=Depends(_check_auth)
):
    d = _skill_dir(agent_id, category, skill_name)
    target = (d / file_path).resolve()
    if not str(target).startswith(str(d.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal denied")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {"status": "ok", "path": file_path}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/sync-source/{source_id}")
async def sync_source(source_id: str, _=Depends(_check_auth)):
    from skill_git_sync import SkillGitSync
    if not DATABASE_URL:
        raise HTTPException(500, "DATABASE_URL not set")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, company_id, repo_url, ref, sync_path, sync_token, sync_author,
                       source_kind, source_locator
                FROM skill_sources WHERE id = %s
            """, (source_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Source not found")
        sid, company_id, repo_url, ref, sync_path, sync_token, sync_author, source_kind, source_locator = row
        if not repo_url:
            raise HTTPException(400, "Source has no repo_url")
        sync = SkillGitSync(
            source_id=sid,
            repo_url=repo_url,
            branch=ref or "main",
            path=sync_path or "skills/",
            token=sync_token or os.environ.get("SKILLS_SYNC_TOKEN", ""),
            author=sync_author or "Orchestrator <orchestrator@hermes>",
            source_kind=source_kind or "",
            source_locator=source_locator,
        )
        with conn.cursor() as cur:
            if source_kind == "agent" and source_locator:
                cur.execute("SELECT company_id FROM agents WHERE id = %s", (source_locator,))
                agent_row = cur.fetchone()
                company_ids = [agent_row[0]] if agent_row else []
            else:
                cur.execute("SELECT id FROM companies")
                company_ids = [r[0] for r in cur.fetchall()]
        results = []
        for cid in company_ids:
            push = sync.push_skills(conn, cid)
            pull = sync.pull_skills(conn, cid)
            if not push.get("skipped") or pull.get("imported") or pull.get("updated") or pull.get("removed"):
                results.append({"company_id": cid, "push": push, "pull": pull})
        return {"source_id": sid, "synced": results}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("sync-source failed: %s", exc)
        raise HTTPException(500, str(exc))
    finally:
        conn.close()


if __name__ == "__main__":
    logger.info("Team Skills API starting on port 8681")
    uvicorn.run(app, host="0.0.0.0", port=8681, log_level="info")
