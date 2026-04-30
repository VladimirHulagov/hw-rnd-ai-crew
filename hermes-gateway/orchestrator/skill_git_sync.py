from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from skill_importer import _parse_frontmatter

logger = logging.getLogger("gateway-orchestrator")


class SkillGitSync:
    def __init__(self, source_id: str, repo_url: str, branch: str, path: str, token: str, author: str,
                 source_kind: str = "", source_locator: str | None = None):
        self.source_id = source_id
        self.source_kind = source_kind
        self.source_locator = source_locator
        self.repo_url = repo_url
        self.branch = branch
        self.path = path
        self.token = token
        self.author = author
        tag = hashlib.md5(source_id.encode()).hexdigest()[:12]
        self._repo_dir: Optional[Path] = Path(f"/tmp/skill-git-sync-{tag}")

    def _auth_url(self) -> str:
        if self.token and self.repo_url.startswith("https://"):
            return self.repo_url.replace("https://", f"https://{self.token}@", 1)
        return self.repo_url

    def _git_env(self) -> dict:
        name = self.author.split("<")[0].strip() if self.author else "Orchestrator"
        email = self.author.split("<")[1].rstrip(">").strip() if self.author and "<" in self.author else "orchestrator@hermes"
        return {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }

    def _git(self, *args, cwd=None, env=None) -> subprocess.CompletedProcess:
        git_env = {**subprocess.os.environ, **(env or {}), **self._git_env()}
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self._repo_dir,
            capture_output=True,
            text=True,
            env=git_env,
        )

    def _ensure_repo(self) -> bool:
        if not self.repo_url:
            return False
        if (self._repo_dir / ".git").is_dir():
            result = self._git("pull", "--rebase")
            if result.returncode != 0:
                logger.warning("git pull failed: %s", result.stderr)
                return False
            return True
        self._repo_dir.parent.mkdir(parents=True, exist_ok=True)
        result = self._git(
            "clone", "-b", self.branch, "--single-branch", self._auth_url(), str(self._repo_dir),
            cwd=self._repo_dir.parent,
        )
        if result.returncode != 0:
            shutil.rmtree(self._repo_dir, ignore_errors=True)
            result2 = self._git("clone", self._auth_url(), str(self._repo_dir), cwd=self._repo_dir.parent)
            if result2.returncode != 0:
                result3 = self._git("init", str(self._repo_dir), cwd=self._repo_dir.parent)
                if result3.returncode != 0:
                    logger.warning("git init failed: %s", result3.stderr)
                    return False
                self._git("remote", "add", "origin", self._auth_url())
            r = self._git("checkout", "-b", self.branch)
            if r.returncode != 0 and "already exists" not in (r.stderr or ""):
                self._git("branch", "-m", self.branch)
            (self._repo_dir / ".gitkeep").write_text("", encoding="utf-8")
            self._git("add", ".gitkeep")
            self._git("commit", "-m", "init")
            r = self._git("push", "-u", "origin", self.branch)
            if r.returncode != 0:
                logger.warning("git push init failed: %s", r.stderr)
                return False
        return True

    def push_skills(self, conn, company_id: str) -> dict:
        result = {"pushed": 0, "removed": 0, "skipped": False}
        if not self.repo_url:
            result["skipped"] = True
            return result
        if not self._ensure_repo():
            result["skipped"] = True
            return result

        skills_dir = self._repo_dir / self.path

        with conn.cursor() as cur:
            if self.source_kind == "agent":
                cur.execute("""
                    SELECT metadata->>'category' AS category, slug, markdown
                    FROM company_skills
                    WHERE company_id = %s
                      AND metadata->>'sourceKind' = 'agent_created'
                      AND metadata->>'authorAgentId' = %s
                """, (company_id, self.source_locator))
            elif self.source_kind == "git":
                cur.execute("""
                    SELECT metadata->>'category' AS category, slug, markdown
                    FROM company_skills
                    WHERE company_id = %s
                      AND metadata->>'sourceId' = %s
                """, (company_id, self.source_id))
            else:
                cur.execute("""
                    SELECT metadata->>'category' AS category, slug, markdown
                    FROM company_skills
                    WHERE company_id = %s
                      AND metadata->>'sourceKind' IN ('agent_created', 'git_sync')
                """, (company_id,))
            rows = cur.fetchall()

        db_slugs: set[str] = set()
        for category, slug, markdown in rows:
            db_slugs.add(f"{category}/{slug}")
            skill_path = skills_dir / category / slug
            skill_path.mkdir(parents=True, exist_ok=True)
            skill_file = skill_path / "SKILL.md"
            skill_file.write_text(markdown or "", encoding="utf-8")
            result["pushed"] += 1

        if skills_dir.is_dir():
            for category_dir in sorted(skills_dir.iterdir()):
                if not category_dir.is_dir():
                    continue
                for slug_dir in sorted(category_dir.iterdir()):
                    if not slug_dir.is_dir():
                        continue
                    key = f"{category_dir.name}/{slug_dir.name}"
                    if key not in db_slugs:
                        shutil.rmtree(slug_dir)
                        result["removed"] += 1
                if category_dir.is_dir() and not any(category_dir.iterdir()):
                    category_dir.rmdir()

        self._git("add", self.path)
        status = self._git("status", "--porcelain", "--", self.path)
        if not status.stdout.strip():
            result["skipped"] = True
            return result

        self._git("commit", "-m", f"sync skills: {result['pushed']} pushed, {result['removed']} removed")
        push_result = self._git("push", "origin", self.branch)
        if push_result.returncode != 0:
            logger.warning("git push failed: %s", push_result.stderr)
        return result

    def pull_skills(self, conn, company_id: str) -> dict:
        result = {"imported": 0, "updated": 0, "removed": 0}
        if not self.repo_url:
            return result
        if not self._ensure_repo():
            return result

        skills_dir = self._repo_dir / self.path
        if not skills_dir.is_dir():
            return result

        repo_skills: dict[str, dict] = {}
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            rel = skill_md.parent.relative_to(skills_dir)
            parts = rel.parts
            if len(parts) < 2:
                continue
            category = parts[0]
            slug = parts[-1]
            text = skill_md.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            name = fm.get("name", slug)
            description = fm.get("description", "")
            key = f"git/{category}/{slug}"
            repo_skills[key] = {
                "category": category,
                "slug": slug,
                "name": name,
                "description": description,
                "markdown": text,
            }

        with conn.cursor() as cur:
            if self.source_kind == "agent" and self.source_locator:
                for key, skill in repo_skills.items():
                    agent_key = f"agent/{self.source_locator}/{skill['category']}/{skill['slug']}"
                    cur.execute("""
                        SELECT 1 FROM company_skills
                        WHERE company_id = %s AND key = %s
                    """, (company_id, agent_key))
                    if cur.fetchone():
                        cur.execute("""
                            UPDATE company_skills SET
                                markdown = %s,
                                updated_at = now()
                            WHERE company_id = %s AND key = %s
                        """, (skill["markdown"], company_id, agent_key))
                        result["updated"] += 1
                    else:
                        cur.execute("""
                            INSERT INTO company_skills (
                                id, company_id, key, slug, name, description, markdown,
                                source_type, source_locator, trust_level, compatibility,
                                file_inventory, metadata, hidden
                            ) VALUES (
                                gen_random_uuid(), %s, %s, %s, %s, %s, %s,
                                'catalog', NULL, 'markdown_only', 'compatible',
                                %s::jsonb, %s::jsonb, false
                            )
                        """, (
                            company_id,
                            agent_key,
                            skill["slug"],
                            skill["name"],
                            skill["description"],
                            skill["markdown"],
                            json.dumps([{"path": "SKILL.md", "kind": "markdown"}]),
                            json.dumps({
                                "sourceKind": "agent_created",
                                "sourceId": self.source_id,
                                "authorAgentId": self.source_locator,
                                "category": skill["category"],
                                "sourceLabel": "Git Pull",
                            }),
                        ))
                        result["imported"] += 1
            else:
                for key, skill in repo_skills.items():
                    cur.execute("""
                        INSERT INTO company_skills (
                            id, company_id, key, slug, name, description, markdown,
                            source_type, source_locator, trust_level, compatibility,
                            file_inventory, metadata, hidden
                        ) VALUES (
                            gen_random_uuid(), %s, %s, %s, %s, %s, %s,
                            'catalog', NULL, 'markdown_only', 'compatible',
                            %s::jsonb, %s::jsonb, false
                        )
                        ON CONFLICT (company_id, key) DO UPDATE SET
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            markdown = EXCLUDED.markdown,
                            file_inventory = EXCLUDED.file_inventory,
                            metadata = EXCLUDED.metadata,
                            hidden = false,
                            updated_at = now()
                    """, (
                        company_id,
                        key,
                        skill["slug"],
                        skill["name"],
                        skill["description"],
                        skill["markdown"],
                        json.dumps([{"path": "SKILL.md", "kind": "markdown"}]),
                        json.dumps({
                            "sourceKind": "git_sync",
                            "sourceId": self.source_id,
                            "category": skill["category"],
                            "sourceLabel": "Git Sync",
                        }),
                    ))
                    result["imported"] += 1

                cur.execute("""
                    SELECT key FROM company_skills
                    WHERE company_id = %s AND metadata->>'sourceId' = %s AND metadata->>'sourceKind' = 'git_sync'
                """, (company_id, self.source_id))
                existing_keys = {row[0] for row in cur.fetchall()}

                stale_keys = existing_keys - set(repo_skills.keys())
                for stale_key in stale_keys:
                    cur.execute("""
                        UPDATE company_skills SET hidden = true, updated_at = now()
                        WHERE company_id = %s AND key = %s
                    """, (company_id, stale_key))
                    result["removed"] += 1

        conn.commit()
        return result
