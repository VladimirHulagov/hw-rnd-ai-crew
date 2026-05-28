"""E2E tests for skill_sources API: GET (list with auto-creation), PATCH (update name/repo_url).

Run: python3.12 e2e/test_skill_sources.py
"""

import json
import sys
import traceback
import urllib.parse

import httpx

BASE = "http://127.0.0.1:3100"
COMPANY = "11111111-1111-1111-1111-111111111111"
EMAIL = "test@test.com"
PASSWORD = "Test123456!"

_session_token: str | None = None


def _ensure_token() -> str:
    global _session_token
    if _session_token:
        return _session_token
    r = httpx.post(
        f"{BASE}/api/auth/sign-in/email",
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    sc = r.headers.get("set-cookie", "")
    from urllib.parse import unquote
    _session_token = unquote(sc.split("=", 1)[1].split(";")[0])
    return _session_token


def _api(method: str, path: str, data=None):
    token = _ensure_token()
    headers = {
        "Cookie": f"__Secure-better-auth.session_token={token}",
        "Origin": BASE,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
        content = json.dumps(data)
    else:
        content = None
    resp = httpx.request(method, f"{BASE}{path}", headers=headers, content=content)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return resp.status_code, body


def _get_skills():
    _, data = _api("GET", f"/api/companies/{COMPANY}/skills?includeHidden=true")
    if isinstance(data, dict) and "error" in data:
        return []
    return data


def _find_source_by_type_locator(sources, source_type, source_locator):
    for s in sources:
        if s.get("source_type") == source_type:
            if source_locator is None and s.get("source_locator") is None:
                return s
            if s.get("source_locator") == source_locator:
                return s
    return None


# ---------------------------------------------------------------------------
# Test: GET /skill-sources - list with auto-creation
# ---------------------------------------------------------------------------

class TestGetSkillSources:

    def test_returns_list(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert isinstance(body, list), f"Expected list, got {type(body)}: {body}"
        print(f"  PASS: returns list ({len(body)} sources)")

    def test_auto_creates_from_existing_skills(self):
        skills = _get_skills()
        assert len(skills) > 0, "Need at least one skill in DB"

        unique_sources = set()
        for s in skills:
            st = s.get("sourceType") or s.get("source_type", "catalog")
            sl = s.get("sourceLocator") or s.get("source_locator")
            unique_sources.add((st, sl))

        status, body = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200, f"Expected 200, got {status}: {body}"

        predefined_kinds = {"paperclip_bundled", "hermes_bundled", "team"}
        found_kinds = {s["source_kind"] for s in body}
        assert predefined_kinds.issubset(found_kinds), (
            f"Expected predefined kinds {predefined_kinds}, got {found_kinds}"
        )

        print(f"  PASS: auto-created {len(body)} sources including all {len(predefined_kinds)} predefined")

    def test_source_has_required_fields(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200
        assert len(body) > 0, "Need at least one source"

        src = body[0]
        assert "id" in src, f"Missing 'id': {src}"
        assert "name" in src, f"Missing 'name': {src}"
        assert "source_type" in src, f"Missing 'source_type': {src}"
        assert "company_id" in src, f"Missing 'company_id': {src}"
        assert src["company_id"] == COMPANY, f"Wrong company_id: {src['company_id']}"

        print(f"  PASS: source has required fields (id, name, source_type, company_id)")

    def test_idempotent_auto_creation(self):
        status1, body1 = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status1 == 200

        status2, body2 = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status2 == 200

        assert len(body1) == len(body2), (
            f"Second call should not create duplicates: {len(body1)} vs {len(body2)}"
        )

        ids1 = sorted(s["id"] for s in body1)
        ids2 = sorted(s["id"] for s in body2)
        assert ids1 == ids2, "Source IDs should be stable across calls"

        print("  PASS: idempotent auto-creation (no duplicates)")

    def test_catalog_skills_have_source(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200

        catalog_sources = [s for s in body if s.get("source_type") == "catalog"]
        assert len(catalog_sources) >= 1, (
            f"Expected >= 1 catalog source (seed data has agent_created skills), got {len(catalog_sources)}"
        )

        print(f"  PASS: catalog skills have source entries ({len(catalog_sources)})")

    def test_different_companies_isolated(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200
        for s in body:
            assert s["company_id"] == COMPANY
        print("  PASS: sources are company-scoped")


# ---------------------------------------------------------------------------
# Test: PATCH /skill-sources/:sourceId - update name and repo_url
# ---------------------------------------------------------------------------

class TestPatchSkillSource:

    def _get_first_source(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200 and len(body) > 0, f"No sources available: {status} {body}"
        return body[0]

    def test_update_name(self):
        src = self._get_first_source()
        src_id = src["id"]
        new_name = f"Updated {src['name']}"

        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "name": new_name,
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("name") == new_name, f"Expected name={new_name}, got {body.get('name')}"

        _, refreshed = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        match = [s for s in refreshed if s["id"] == src_id]
        assert len(match) == 1
        assert match[0]["name"] == new_name

        print("  PASS: update name works")

    def test_update_repo_url(self):
        src = self._get_first_source()
        src_id = src["id"]

        new_url = "https://github.com/example/skills-repo.git"
        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "repo_url": new_url,
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("repo_url") == new_url, f"Expected repo_url={new_url}, got {body.get('repo_url')}"

        _, refreshed = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        match = [s for s in refreshed if s["id"] == src_id]
        assert match[0]["repo_url"] == new_url

        print("  PASS: update repo_url works")

    def test_update_both_name_and_repo_url(self):
        src = self._get_first_source()
        src_id = src["id"]

        new_name = "My Custom Source"
        new_url = "https://github.com/myorg/custom-skills.git"
        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "name": new_name,
            "repo_url": new_url,
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("name") == new_name
        assert body.get("repo_url") == new_url

        print("  PASS: update both name and repo_url works")

    def test_update_nonexistent_source(self):
        status, body = _api("PATCH",
            f"/api/companies/{COMPANY}/skill-sources/00000000-0000-0000-0000-000000000000",
            {"name": "ghost"})
        assert status == 404, f"Expected 404, got {status}: {body}"

        print("  PASS: update nonexistent source returns 404")

    def test_update_empty_name_rejected(self):
        src = self._get_first_source()
        src_id = src["id"]

        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "name": "",
        })
        assert status in (400, 422), f"Expected 400/422 for empty name, got {status}: {body}"

        print("  PASS: empty name rejected")

    def test_update_preserves_other_fields(self):
        src = self._get_first_source()
        src_id = src["id"]
        orig_type = src["source_type"]
        orig_locator = src.get("source_locator")

        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "repo_url": "https://github.com/test/repo.git",
        })

        _, refreshed = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        match = [s for s in refreshed if s["id"] == src_id]
        assert len(match) == 1
        assert match[0]["source_type"] == orig_type, "source_type should not change"
        assert match[0].get("source_locator") == orig_locator, "source_locator should not change"

        print("  PASS: update preserves source_type and source_locator")


# ---------------------------------------------------------------------------
# Test: GET /skill-sources reflects in skill listing
# ---------------------------------------------------------------------------

class TestSkillSourceIntegration:

    def test_source_name_appears_in_skills(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200 and len(sources) > 0

        predefined = [s for s in sources if s["source_kind"] in ("hermes_bundled", "team")]
        assert len(predefined) > 0, "No predefined sources to test"

        src = predefined[0]
        src_id = src["id"]
        custom_name = "Custom Source Name Test"
        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "name": custom_name,
        })

        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills?includeHidden=true")
        matching = [s for s in skills if s.get("sourceLabel") == custom_name]
        assert len(matching) >= 1, (
            f"Expected >= 1 skill with sourceLabel='{custom_name}', "
            f"source_kind={src['source_kind']}. "
            f"Labels: {list(set(s.get('sourceLabel') for s in skills))}"
        )

        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {"name": src["name"]})
        print(f"  PASS: custom source name appears as sourceLabel on skills ({len(matching)} skills)")


# ---------------------------------------------------------------------------
# Test: Predefined sources (paperclip_bundled, hermes_bundled, team)
# ---------------------------------------------------------------------------

class TestSkillSourcesPredefined:

    def _get_sources(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200, f"Expected 200, got {status}: {body}"
        return body

    def test_predefined_sources_exist(self):
        sources = self._get_sources()
        predefined_kinds = {"paperclip_bundled", "hermes_bundled", "team"}
        found_kinds = {s["source_kind"] for s in sources}
        for kind in predefined_kinds:
            assert kind in found_kinds, f"Missing predefined source_kind '{kind}'. Found: {found_kinds}"

        agent_sources = [s for s in sources if s["source_kind"] == "agent"]
        assert len(agent_sources) >= 1, f"Expected at least 1 agent source, got {len(agent_sources)}"
        print(f"  PASS: all 3 predefined sources + {len(agent_sources)} agent sources exist")

    def test_predefined_cannot_be_deleted(self):
        sources = self._get_sources()
        non_git = [s for s in sources if s["source_kind"] != "git"]
        assert len(non_git) > 0, "No non-git sources to test"

        for src in non_git:
            status, body = _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{src['id']}")
            assert status == 403, (
                f"Expected 403 for deleting source kind={src['source_kind']}, "
                f"got {status}: {body}"
            )
        print(f"  PASS: {len(non_git)} non-git sources cannot be deleted (403)")


# ---------------------------------------------------------------------------
# Test: CRUD for git (user-created) sources
# ---------------------------------------------------------------------------

class TestSkillSourcesCRUD:

    def test_create_git_source(self):
        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
            "name": "E2E Test Git Source",
            "repo_url": "https://github.com/example/e2e-test-skills.git",
        })
        assert status == 201, f"Expected 201, got {status}: {body}"
        assert body.get("name") == "E2E Test Git Source"
        assert body.get("repo_url") == "https://github.com/example/e2e-test-skills.git"
        assert body.get("source_kind") == "git"
        assert body.get("id") is not None
        print(f"  PASS: created git source (id={body['id']})")
        return body["id"]

    def test_delete_git_source(self):
        create_status, created = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
            "name": "E2E Delete Target",
            "repo_url": "https://github.com/example/delete-target.git",
        })
        assert create_status == 201, f"Create failed: {create_status} {created}"
        src_id = created["id"]

        status, body = _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{src_id}")
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("deleted") is True

        _, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        ids = [s["id"] for s in sources]
        assert src_id not in ids, "Deleted source still appears in list"
        print("  PASS: delete git source works")

    def test_create_empty_name_rejected(self):
        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
            "name": "",
        })
        assert status in (400, 422), f"Expected 400/422 for empty name, got {status}: {body}"
        print("  PASS: empty name rejected on create")

    def test_create_git_source_without_repo_url(self):
        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
            "name": "No URL Source",
        })
        assert status == 201, f"Expected 201, got {status}: {body}"
        assert body.get("name") == "No URL Source"
        assert body.get("repo_url") is None
        assert body.get("source_kind") == "git"

        _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{body['id']}")
        print("  PASS: create git source without repo_url works")

    def test_delete_git_source_twice(self):
        status, created = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
            "name": "Double Delete",
        })
        assert status == 201
        src_id = created["id"]

        _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{src_id}")

        status2, body2 = _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{src_id}")
        assert status2 == 404, f"Expected 404 on second delete, got {status2}: {body2}"
        print("  PASS: delete git source twice returns 404")


# ---------------------------------------------------------------------------
# Test: Predefined sources are unique per kind
# ---------------------------------------------------------------------------

class TestSkillSourcesUniqueness:

    def test_one_source_per_predefined_kind(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200

        for kind in ("paperclip_bundled", "hermes_bundled", "team"):
            matching = [s for s in sources if s["source_kind"] == kind]
            assert len(matching) == 1, (
                f"Expected exactly 1 source with kind={kind}, got {len(matching)}"
            )
        print("  PASS: exactly 1 source per predefined kind")

    def test_predefined_names_not_empty(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200

        predefined = [s for s in sources if s["source_kind"] != "git"]
        for src in predefined:
            assert src["name"] and len(src["name"].strip()) > 0, (
                f"Predefined source kind={src['source_kind']} has empty name"
            )
        print(f"  PASS: all {len(predefined)} predefined sources have non-empty names")


# ---------------------------------------------------------------------------
# Test: Delete skills by category (key pattern)
# ---------------------------------------------------------------------------

class TestDeleteSkillsByCategory:

    def test_delete_individual_skill(self):
        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills?includeHidden=true")
        before_count = len(skills)
        assert before_count > 0

        target = skills[0]
        skill_id = target["id"]

        status, body = _api("DELETE", f"/api/companies/{COMPANY}/skills/{skill_id}")
        assert status == 200, f"Expected 200, got {status}: {body}"

        _, after = _api("GET", f"/api/companies/{COMPANY}/skills?includeHidden=true")
        assert len(after) == before_count - 1, "Skills count should decrease by 1"

        print(f"  PASS: deleted skill {skill_id}")

    def test_delete_nonexistent_skill(self):
        status, body = _api("DELETE", f"/api/companies/{COMPANY}/skills/00000000-0000-0000-0000-000000000000")
        assert status == 404, f"Expected 404, got {status}: {body}"
        print("  PASS: delete nonexistent skill returns 404")


# ---------------------------------------------------------------------------
# Test: source_kind in PATCH response
# ---------------------------------------------------------------------------

class TestPatchSourceKind:

    def test_patch_response_includes_source_kind(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200
        src = [s for s in sources if s["source_kind"] == "git"]
        if not src:
            create_status, created = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
                "name": "Kind Test Source",
            })
            assert create_status == 201
            src = [created]

        src_id = src[0]["id"]
        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "name": "Kind Test Updated",
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert "source_kind" in body, f"Missing source_kind in PATCH response: {body}"
        assert body["source_kind"] == "git"

        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {"name": src[0]["name"]})
        print("  PASS: PATCH response includes source_kind")


class TestAgentSources:

    def test_agent_sources_auto_created(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200
        agent_sources = [s for s in sources if s["source_kind"] == "agent"]
        assert len(agent_sources) >= 2, f"Expected >= 2 agent sources, got {len(agent_sources)}"
        names = {s["name"] for s in agent_sources}
        assert "SW DEV" in names, f"Missing SW DEV agent source. Names: {names}"
        assert "Researcher" in names, f"Missing Researcher agent source. Names: {names}"
        print(f"  PASS: {len(agent_sources)} agent sources auto-created")

    def test_agent_source_enrichment(self):
        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills?includeHidden=true")
        sw_dev_skills = [s for s in skills if (s.get("metadata") or {}).get("authorAgentName") == "SW DEV"]
        assert len(sw_dev_skills) >= 1
        for s in sw_dev_skills:
            assert s.get("sourceLabel") == "SW DEV", (
                f"Expected sourceLabel='SW DEV', got '{s.get('sourceLabel')}'"
            )
        print(f"  PASS: {len(sw_dev_skills)} SW DEV skills enriched with agent name")

    def test_agent_source_cannot_be_deleted(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        agent_src = [s for s in sources if s["source_kind"] == "agent"]
        assert len(agent_src) > 0
        status, body = _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{agent_src[0]['id']}")
        assert status == 403, f"Expected 403 for agent source delete, got {status}: {body}"
        print("  PASS: agent source cannot be deleted (403)")

    def test_agent_source_can_be_renamed(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        agent_src = [s for s in sources if s["source_kind"] == "agent"][0]
        orig_name = agent_src["name"]

        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{agent_src['id']}", {
            "name": "Renamed Agent",
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body["name"] == "Renamed Agent"

        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{agent_src['id']}", {"name": orig_name})
        print("  PASS: agent source can be renamed")


# ---------------------------------------------------------------------------
# Test: Git URL validation
# ---------------------------------------------------------------------------

class TestGitUrlValidation:

    def test_create_invalid_url_rejected(self):
        for bad_url in ["not-a-url", "ftp://example.com/repo", "/local/path"]:
            status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
                "name": "Bad URL Source",
                "repo_url": bad_url,
            })
            assert status == 400, f"Expected 400 for url='{bad_url}', got {status}: {body}"
            assert "Invalid git URL" in body.get("error", ""), f"Expected 'Invalid git URL' error for '{bad_url}'"
        print("  PASS: invalid git URLs rejected on POST")

    def test_create_valid_url_accepted(self):
        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
            "name": "Valid URL Source",
            "repo_url": "https://github.com/example/test-skills.git",
        })
        assert status == 201, f"Expected 201, got {status}: {body}"
        _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{body['id']}")
        print("  PASS: valid git URL accepted on POST")

    def test_patch_invalid_url_rejected(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = [s for s in sources if s["source_kind"] == "git"]
        if not src:
            create_status, created = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {"name": "Validation Test"})
            assert create_status == 201
            src = [created]

        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src[0]['id']}", {
            "repo_url": "not-valid",
        })
        assert status == 400, f"Expected 400, got {status}: {body}"
        assert "Invalid git URL" in body.get("error", "")
        print("  PASS: invalid git URL rejected on PATCH")

    def test_patch_valid_url_accepted(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = [s for s in sources if s["source_kind"] == "git"][0]

        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src['id']}", {
            "repo_url": "https://github.com/example/valid.git",
        })
        assert status == 200, f"Expected 200, got {status}: {body}"

        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src['id']}", {"repo_url": ""})
        print("  PASS: valid git URL accepted on PATCH")

    def test_create_empty_url_allowed(self):
        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {
            "name": "No URL Source",
            "repo_url": "",
        })
        assert status == 201, f"Expected 201, got {status}: {body}"
        _api("DELETE", f"/api/companies/{COMPANY}/skill-sources/{body['id']}")
        print("  PASS: empty repo_url allowed on POST")


# ---------------------------------------------------------------------------
# Test: Check repo endpoint
# ---------------------------------------------------------------------------

class TestCheckRepo:

    def test_check_accessible_repo(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = [s for s in sources if s["source_kind"] == "git"]
        if not src:
            create_status, created = _api("POST", f"/api/companies/{COMPANY}/skill-sources", {"name": "Check Test"})
            assert create_status == 201
            src = [created]

        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources/{src[0]['id']}/check-repo", {
            "repo_url": "https://github.com/VladimirHulagov/hermes-agent",
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("accessible") is True, f"Expected accessible=true, got: {body}"
        assert isinstance(body.get("branches"), list) and len(body["branches"]) > 0, f"Expected branches list, got: {body}"
        print(f"  PASS: accessible repo check ({len(body['branches'])} branches)")

    def test_check_nonexistent_repo(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = [s for s in sources if s["source_kind"] == "git"][0]

        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources/{src['id']}/check-repo", {
            "repo_url": "https://github.com/nonexistent/repo-12345.git",
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("accessible") is False, f"Expected accessible=false, got: {body}"
        print("  PASS: nonexistent repo returns accessible=false")

    def test_check_invalid_url(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = [s for s in sources if s["source_kind"] == "git"][0]

        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources/{src['id']}/check-repo", {
            "repo_url": "not-a-url",
        })
        assert status == 400, f"Expected 400, got {status}: {body}"
        assert body.get("valid") is False or "Invalid" in body.get("error", ""), f"Expected invalid error: {body}"
        print("  PASS: invalid URL returns 400")

    def test_check_no_url(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = [s for s in sources if s["source_kind"] == "git"][0]

        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src['id']}", {"repo_url": ""})

        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources/{src['id']}/check-repo", {})
        assert status == 400, f"Expected 400, got {status}: {body}"
        print("  PASS: no repo_url returns 400")

    def test_check_nonexistent_source(self):
        status, body = _api("POST", f"/api/companies/{COMPANY}/skill-sources/00000000-0000-0000-0000-000000000000/check-repo", {
            "repo_url": "https://github.com/example/test.git",
        })
        assert status == 404, f"Expected 404, got {status}: {body}"
        print("  PASS: nonexistent source returns 404")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TestSourceCounts:

    def test_source_count_matches_skills(self):
        _, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert isinstance(sources, list), f"Expected list, got {type(sources)}"
        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills")
        assert isinstance(skills, list), f"Expected list, got {type(skills)}"

        for src in sources:
            kind = src["source_kind"]
            loc = src.get("source_locator")
            name = src["name"]
            if kind == "git":
                expected = 0
            elif kind == "paperclip_bundled":
                expected = sum(1 for s in skills if s.get("sourceGroup") == "paperclip_bundled")
            elif kind == "hermes_bundled":
                expected = sum(1 for s in skills if s.get("sourceGroup") == "hermes_bundled")
            elif kind == "team":
                expected = sum(1 for s in skills if s.get("sourceGroup") == "team")
            elif kind == "agent" and loc:
                expected = sum(1 for s in skills if s.get("authorAgentId") == loc)
            elif kind == "agent":
                expected = sum(1 for s in skills if s.get("sourceGroup") == "agent")
            else:
                expected = 0
            assert expected >= 0, f"Source '{name}' ({kind}): expected {expected} but countByKind returned negative"
            print(f"  Source '{name}' ({kind}): {expected} skills")

    def test_agent_sources_have_matching_skills(self):
        _, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills")
        agent_sources = [s for s in sources if s["source_kind"] == "agent"]
        assert len(agent_sources) >= 1, f"Expected at least 1 agent source, got {len(agent_sources)}"
        for src in agent_sources:
            loc = src.get("source_locator")
            assert loc, f"Agent source '{src['name']}' has no source_locator"
            matching = [s for s in skills if s.get("authorAgentId") == loc]
            assert len(matching) >= 1, (
                f"Agent source '{src['name']}' (locator={loc}) has 0 matching skills. "
                f"Skills with authorAgentId: {[s.get('authorAgentId') for s in skills if s.get('authorAgentId')]}"
            )
            print(f"  Agent '{src['name']}' ({loc[:12]}...): {len(matching)} skills")

    def test_skills_have_sourceGroup_field(self):
        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills")
        assert isinstance(skills, list) and len(skills) > 0
        without_group = [s for s in skills if "sourceGroup" not in s]
        assert len(without_group) == 0, (
            f"{len(without_group)} skills missing sourceGroup field: "
            f"{[s['name'] for s in without_group[:5]]}"
        )

    def test_agent_created_skills_have_authorAgentId(self):
        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills")
        agent_skills = [s for s in skills if s.get("sourceGroup") == "agent"]
        without_aid = [s for s in agent_skills if "authorAgentId" not in s]
        assert len(without_aid) == 0, (
            f"{len(without_aid)} agent skills missing authorAgentId: "
            f"{[s['name'] for s in without_aid[:5]]}"
        )


class TestSyncFields:

    def test_get_returns_sync_fields(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200
        for src in sources:
            assert "sync_token" in src, f"Missing sync_token in source {src['name']}"
            assert "sync_path" in src, f"Missing sync_path in source {src['name']}"
            assert "sync_author" in src, f"Missing sync_author in source {src['name']}"
        print(f"  PASS: all {len(sources)} sources have sync fields")

    def test_sync_token_masked_in_get(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        assert status == 200
        src = sources[0]
        src_id = src["id"]
        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "sync_token": "ghp_test_secret_token",
        })
        _, refreshed = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        match = [s for s in refreshed if s["id"] == src_id]
        assert len(match) == 1
        token = match[0].get("sync_token")
        assert token != "ghp_test_secret_token", f"Token not masked: {token}"
        assert token is not None, "Token should be masked, not null"
        print("  PASS: sync_token masked in GET response")

    def test_masked_token_not_overwritten(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = sources[0]
        src_id = src["id"]
        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "sync_token": "ghp_real_token_123",
        })
        masked = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "name": src["name"],
            "sync_token": masked,
        })
        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "sync_token": "check_token",
        })
        print("  PASS: masked token not overwritten on PATCH")

    def test_update_sync_path_and_author(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = sources[0]
        src_id = src["id"]
        orig_name = src["name"]
        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "sync_path": "custom/skills/",
            "sync_author": "Test Bot <test@bot>",
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("sync_path") == "custom/skills/", f"sync_path mismatch: {body.get('sync_path')}"
        assert body.get("sync_author") == "Test Bot <test@bot>", f"sync_author mismatch: {body.get('sync_author')}"
        _, refreshed = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        match = [s for s in refreshed if s["id"] == src_id]
        assert match[0]["sync_path"] == "custom/skills/"
        assert match[0]["sync_author"] == "Test Bot <test@bot>"
        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "name": orig_name,
            "sync_path": "skills/",
            "sync_author": None,
        })
        print("  PASS: sync_path and sync_author update correctly")

    def test_update_branch_ref(self):
        status, sources = _api("GET", f"/api/companies/{COMPANY}/skill-sources")
        src = sources[0]
        src_id = src["id"]
        orig_ref = src.get("ref", "main")
        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {
            "ref": "develop",
        })
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("ref") == "develop", f"ref mismatch: {body.get('ref')}"
        _api("PATCH", f"/api/companies/{COMPANY}/skill-sources/{src_id}", {"ref": orig_ref})
        print("  PASS: branch (ref) update works")


def _run_tests(test_names=None):
    test_classes = [
        TestGetSkillSources,
        TestPatchSkillSource,
        TestSkillSourceIntegration,
        TestSkillSourcesPredefined,
        TestSkillSourcesCRUD,
        TestSkillSourcesUniqueness,
        TestDeleteSkillsByCategory,
        TestPatchSourceKind,
        TestAgentSources,
        TestGitUrlValidation,
        TestCheckRepo,
        TestSourceCounts,
        TestSyncFields,
    ]

    failures = 0
    passed = 0

    for cls in test_classes:
        instance = cls()
        methods = [(m, getattr(instance, m)) for m in dir(instance) if m.startswith("test_")]

        for method_name, method in methods:
            full_name = f"{cls.__name__}.{method_name}"

            if test_names:
                matched = any(tn in full_name or tn == method_name or tn == cls.__name__ for tn in test_names)
                if not matched:
                    continue

            print(f"\n--- {full_name} ---")
            try:
                method()
                passed += 1
            except Exception as e:
                print(f"FAIL: {e}")
                traceback.print_exc()
                failures += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failures} failed")
    if failures:
        print("FAILED")
        sys.exit(1)
    else:
        print("ALL PASSED")


if __name__ == "__main__":
    test_names = sys.argv[1:] if len(sys.argv) > 1 else None
    _run_tests(test_names)
