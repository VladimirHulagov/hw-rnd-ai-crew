from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from skill_importer import _parse_frontmatter

logger = logging.getLogger("gateway-orchestrator")

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")


class SkillGitSync:
    def __init__(self, source_id: str = "", repo_url: str = "", branch: str = "main",
                 path: str = "skills", token: str = "", author: str = "",
                 source_kind: str = "", source_locator: str | None = None):
        self.source_id = source_id
        self.source_kind = source_kind
        self.source_locator = source_locator
        self.repo_url = repo_url
        self.branch = branch
        self.path = path
        self.token = token
        self.author = author
        self._repo_dir: Optional[Path] = Path(f"/tmp/skill-git-sync-{self._source_tag}")

    @property
    def _source_tag(self) -> str:
        return hashlib.md5(self.source_id.encode()).hexdigest()[:12]

    def _auth_url(self) -> str:
        if self.token:
            scheme = "https" if self.repo_url.startswith("https://") else "http"
            prefix = f"{scheme}://"
            if self.repo_url.startswith(prefix):
                return self.repo_url.replace(prefix, f"{scheme}://{self.token}@", 1)
        return self.repo_url

    def _sync_branch(self) -> str:
        return f"skills-sync/{self._source_tag}"

    def _manifest_path(self, skills_dir: Path) -> Path:
        return skills_dir / ".manifests" / f"{self._source_tag}.json"

    def _read_manifest(self, skills_dir: Path) -> set[str]:
        mp = self._manifest_path(skills_dir)
        if mp.is_file():
            try:
                return set(json.loads(mp.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValueError):
                return set()
        return set()

    def _write_manifest(self, skills_dir: Path, slugs: set[str]) -> None:
        mp = self._manifest_path(skills_dir)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(sorted(slugs)), encoding="utf-8")

    def _prepare_sync_branch(self) -> None:
        self._git("fetch", "origin")
        remote_branch = f"origin/{self._sync_branch()}"
        has_remote = self._git("rev-parse", "--verify", remote_branch).returncode == 0
        r = self._git("checkout", self._sync_branch())
        if r.returncode != 0:
            self._git("checkout", self.branch)
            self._git("reset", "--hard", f"origin/{self.branch}")
            self._git("checkout", "-b", self._sync_branch())
        else:
            if has_remote:
                self._git("merge", remote_branch, "--no-edit")
            self._git("merge", f"origin/{self.branch}", "--no-edit")
        gitignore = self._repo_dir / self.path / ".gitignore"
        gitignore.parent.mkdir(parents=True, exist_ok=True)
        if gitignore.is_file():
            content = gitignore.read_text(encoding="utf-8")
            if ".manifests" not in content:
                content = content.rstrip("\n") + "\n.manifests/\n"
                gitignore.write_text(content, encoding="utf-8")
        else:
            gitignore.write_text(".manifests/\n", encoding="utf-8")

    def _finish_sync_branch(self) -> None:
        self._git("push", "origin", self._sync_branch())
        self._git("checkout", self.branch)

    @staticmethod
    def _parse_repo_info(repo_url: str) -> tuple[str, str]:
        m = re.match(r"(?:https?://)?(?:[^@]+@)?[^/]+/([^/]+)/([^/.]+)", repo_url or "")
        if m:
            return m.group(1), m.group(2)
        return "", ""

    def _git_api(self, method: str, path: str, json_body=None):
        import httpx
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/json",
        }
        parsed = re.match(r"(?:https?://)?(?:[^@]+@)?([^/]+)", self.repo_url or "")
        if not parsed:
            return None
        scheme = "https" if self.repo_url.startswith("https://") else "http"
        base = f"{scheme}://{parsed.group(1)}"
        api_url = f"{base}/api/v1{path}"
        with httpx.Client() as client:
            resp = client.request(method, api_url, headers=headers, json=json_body)
            if resp.status_code < 300:
                return resp.json()
            logger.warning("Git API %s %s returned %d", method, path, resp.status_code)
            return None

    @staticmethod
    def _skill_info(markdown: str) -> dict:
        fm = _parse_frontmatter(markdown or "")
        return {"name": fm.get("name", ""), "description": fm.get("description", "")}

    def _collect_file_changes(self) -> dict[str, list[tuple[str, str]]]:
        diff = self._git("diff", f"origin/{self.branch}...HEAD", "--name-status", "--", self.path)
        changes: dict[str, list[tuple[str, str]]] = {}
        prefix = self.path.rstrip("/") + "/"
        for line in diff.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status_char, filepath = parts[0][0], parts[1]
            if not filepath.startswith(prefix):
                continue
            rel = filepath[len(prefix):]
            segs = rel.split("/")
            if len(segs) < 2:
                continue
            key = f"{segs[0]}/{segs[1]}"
            file_rel = "/".join(segs[2:]) if len(segs) > 2 else "SKILL.md"
            changes.setdefault(key, []).append((status_char, file_rel))
        return changes

    @staticmethod
    def _summarize_skill_changes(file_list: list[tuple[str, str]]) -> str:
        scripts, refs, data, md_change = [], [], [], False
        for status, fpath in file_list:
            if fpath == "SKILL.md":
                md_change = True
            elif fpath.startswith("scripts/"):
                name = fpath.split("/", 1)[-1]
                scripts.append(name)
            elif fpath.startswith("references/"):
                name = fpath.split("/", 1)[-1]
                refs.append(name)
            else:
                data.append(fpath)
        parts = []
        if md_change:
            parts.append("SKILL.md")
        if scripts:
            parts.append(f"+{len(scripts)} script{'s' if len(scripts) > 1 else ''}")
        if refs:
            parts.append(f"+{len(refs)} ref{'s' if len(refs) > 1 else ''}")
        if data:
            parts.append(f"+{len(data)} file{'s' if len(data) > 1 else ''}")
        return ", ".join(parts) if parts else "updated"

    def _build_pr_body(self, changes: dict, file_changes: dict[str, list[tuple[str, str]]]) -> str:
        parts = []
        added = changes.get("added", [])
        updated = changes.get("updated", [])
        removed = changes.get("removed", [])
        if added:
            shown = [s for s in added if f"{s['category']}/{s['slug']}" in file_changes]
            if shown:
                parts.append(f"### Added ({len(shown)})\n")
                for s in shown:
                    key = f"{s['category']}/{s['slug']}"
                    fc = file_changes.get(key, [])
                    desc = self._summarize_skill_changes(fc) if fc else "new skill"
                    parts.append(f"- **{s['slug']}** ({s['category']}) — {desc}")
                parts.append("")
        if updated:
            shown = [s for s in updated if f"{s['category']}/{s['slug']}" in file_changes]
            if shown:
                parts.append(f"### Updated ({len(shown)})\n")
                for s in shown:
                    key = f"{s['category']}/{s['slug']}"
                    fc = file_changes.get(key, [])
                    desc = self._summarize_skill_changes(fc) if fc else "no file changes"
                    parts.append(f"- **{s['slug']}** ({s['category']}) — {desc}")
                parts.append("")
        if removed:
            shown = [s for s in removed if f"{s['category']}/{s['slug']}" in file_changes]
            if shown:
                parts.append(f"### Removed ({len(shown)})\n")
                for s in shown:
                    parts.append(f"- **{s['slug']}** ({s['category']}) — all files removed")
                parts.append("")
        structured = "\n".join(parts).strip()
        summary = self._generate_summary_with_ollama(changes)
        if summary:
            return f"{summary}\n\n---\n\n{structured}"
        return structured or "No changes."

    def _generate_summary_with_ollama(self, changes: dict) -> str:
        import urllib.request
        prompt_parts = []
        added = changes.get("added", [])
        updated = changes.get("updated", [])
        removed = changes.get("removed", [])
        if not (added or updated or removed):
            return ""
        prompt_parts.append("Summarize these skill changes in 1-2 short sentences, in English. Be concise and specific about what changed.\n")
        if added:
            names = [s["slug"] for s in added]
            prompt_parts.append(f"Added skills: {', '.join(names)}\n")
        if updated:
            names = [s["slug"] for s in updated]
            prompt_parts.append(f"Updated skills: {', '.join(names)}\n")
        if removed:
            names = [s["slug"] for s in removed]
            prompt_parts.append(f"Removed skills: {', '.join(names)}\n")
        prompt = "".join(prompt_parts)
        try:
            data = json.dumps({"model": "gemma4:e4b", "prompt": prompt, "stream": False}).encode()
            req = urllib.request.Request(
                f"{OLLAMA_BASE_URL}/api/generate",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            text = result.get("response", "").strip()
            if text:
                logger.info("Ollama PR summary: %s", text[:100])
            return text
        except Exception as exc:
            logger.debug("Ollama summary skipped: %s", exc)
            return ""

    def _create_or_update_pr(self, push_result: dict):
        owner, repo = self._parse_repo_info(self.repo_url)
        if not owner or not repo:
            return None
        branch = self._sync_branch()
        pulls = self._git_api("GET", f"/repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=open")
        changes = push_result.get("changes", {})
        file_changes = push_result.get("file_changes", {})
        title = self._build_pr_title(changes)
        body = self._build_pr_body(changes, file_changes)
        if pulls and isinstance(pulls, list) and len(pulls) > 0:
            pr = pulls[0]
            number = pr.get("number")
            return self._git_api("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", {
                "title": title,
                "body": body,
            })
        else:
            return self._git_api("POST", f"/repos/{owner}/{repo}/pulls", {
                "title": title,
                "head": branch,
                "base": self.branch,
                "body": body,
            })

    @staticmethod
    def _build_pr_title(changes: dict) -> str:
        n_add = len(changes.get("added", []))
        n_upd = len(changes.get("updated", []))
        n_rem = len(changes.get("removed", []))
        parts = []
        if n_add:
            parts.append(f"+{n_add}")
        if n_upd:
            parts.append(f"~{n_upd}")
        if n_rem:
            parts.append(f"-{n_rem}")
        return f"Skills sync: {' '.join(parts)}"

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
                shutil.rmtree(self._repo_dir, ignore_errors=True)
                return self._ensure_repo()
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

    PROFILES_DIR = Path("/root/.hermes/profiles")

    def push_skills(self, conn, company_id: str) -> dict:
        result = {"pushed": 0, "removed": 0, "skipped": False}
        if not self.repo_url:
            result["skipped"] = True
            return result
        if not self._ensure_repo():
            result["skipped"] = True
            return result

        self._prepare_sync_branch()

        skills_dir = self._repo_dir / self.path
        prev_manifest = self._read_manifest(skills_dir)

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
        change_added = []
        change_updated = []
        for category, slug, markdown in rows:
            db_slugs.add(f"{category}/{slug}")
            skill_path = skills_dir / category / slug
            skill_path.mkdir(parents=True, exist_ok=True)
            skill_file = skill_path / "SKILL.md"
            is_new = f"{category}/{slug}" not in prev_manifest
            info = {"category": category, "slug": slug, **self._skill_info(markdown)}
            if is_new:
                change_added.append(info)
            else:
                change_updated.append(info)
            skill_file.write_text(markdown or "", encoding="utf-8")

            if self.source_kind == "agent" and self.source_locator:
                profile_skill = self.PROFILES_DIR / self.source_locator / "skills" / category / slug
                if profile_skill.is_dir():
                    for src_file in profile_skill.rglob("*"):
                        if not src_file.is_file():
                            continue
                        if src_file.name == "SKILL.md":
                            continue
                        rel = src_file.relative_to(profile_skill)
                        if any(p == "__pycache__" for p in rel.parts):
                            continue
                        dst = skill_path / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst)

            result["pushed"] += 1

        stale_slugs = prev_manifest - db_slugs
        change_removed = []
        for key in stale_slugs:
            parts = key.split("/", 1)
            if len(parts) != 2:
                continue
            slug_dir = skills_dir / parts[0] / parts[1]
            info = {"category": parts[0], "slug": parts[1]}
            old_md = slug_dir / "SKILL.md"
            if old_md.is_file():
                info.update(self._skill_info(old_md.read_text(encoding="utf-8")))
            change_removed.append(info)
            if slug_dir.is_dir():
                shutil.rmtree(slug_dir)
                result["removed"] += 1
            category_dir = skills_dir / parts[0]
            if category_dir.is_dir() and not any(category_dir.iterdir()):
                category_dir.rmdir()

        self._write_manifest(skills_dir, db_slugs)

        self._git("add", self.path)
        status = self._git("status", "--porcelain", "--", self.path)
        if not status.stdout.strip():
            self._git("checkout", self.branch)
            result["skipped"] = True
            return result

        self._git("commit", "-m", f"sync skills: {result['pushed']} pushed, {result['removed']} removed")
        file_changes = self._collect_file_changes()
        self._finish_sync_branch()
        result["changes"] = {"added": change_added, "updated": change_updated, "removed": change_removed}
        result["file_changes"] = file_changes
        self._create_or_update_pr(result)
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

    def copy_extra_files(self, category: str, slug: str, target_dir: Path) -> int:
        if not self._repo_dir or not (self._repo_dir / ".git").is_dir():
            return 0
        skill_src = self._repo_dir / self.path / category / slug
        if not skill_src.is_dir():
            return 0
        count = 0
        for src_file in skill_src.rglob("*"):
            if not src_file.is_file():
                continue
            if src_file.name == "SKILL.md":
                continue
            rel = src_file.relative_to(skill_src)
            if any(p == "__pycache__" for p in rel.parts):
                continue
            dst = target_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst)
            count += 1
        return count
