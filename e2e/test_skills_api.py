"""E2E tests for skills API routes: setVisibility, deleteBySource, hiddenSources, teamSkills.

Run: python3.12 e2e/test_skills_api.py
"""

import json
import sys
import traceback
import urllib.parse
from urllib.parse import unquote

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


def _get_skill_id(name):
    _, data = _api("GET", f"/api/companies/{COMPANY}/skills?includeHidden=true")
    if isinstance(data, dict) and "error" in data:
        return None
    for s in data:
        if s["name"] == name:
            return s["id"]
    return None


# ---------------------------------------------------------------------------
# Test: PATCH /skills/:id - setVisibility
# ---------------------------------------------------------------------------

class TestSetVisibility:

    def test_hide_skill(self):
        skill_id = _get_skill_id("Search")
        assert skill_id, "Search skill not found"

        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skills/{skill_id}", {"hidden": True})
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("hidden") is True, f"Expected hidden=true: {body}"

        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills")
        names = [s["name"] for s in skills]
        assert "Search" not in names, f"Hidden skill should be excluded: {names}"

        print("  PASS: hide skill works")

    def test_show_skill(self):
        skill_id = _get_skill_id("Search")
        assert skill_id

        _api("PATCH", f"/api/companies/{COMPANY}/skills/{skill_id}", {"hidden": True})
        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skills/{skill_id}", {"hidden": False})
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("hidden") is False

        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills")
        names = [s["name"] for s in skills]
        assert "Search" in names, f"Restored skill should be visible: {names}"

        print("  PASS: show skill works")

    def test_hide_nonexistent_skill(self):
        status, body = _api("PATCH", f"/api/companies/{COMPANY}/skills/nonexistent", {"hidden": True})
        assert status in (404, 409, 500), f"Expected error for nonexistent skill, got {status}: {body}"

        print("  PASS: nonexistent skill returns error")


# ---------------------------------------------------------------------------
# Test: DELETE /skills-by-source - deleteBySource
# ---------------------------------------------------------------------------

class TestDeleteBySource:

    def test_delete_catalog_source(self):
        _, before = _api("GET", f"/api/companies/{COMPANY}/skills")
        local_before = [s for s in before if s.get("sourceBadge") == "paperclip"]
        assert len(local_before) >= 1, f"Expected >= 1 paperclip skill, got {len(local_before)}"

        first_local = local_before[0]
        locator = first_local.get("sourceLocator", "")
        status, body = _api("DELETE",
            f"/api/companies/{COMPANY}/skills-by-source?sourceType=local_path&sourceLocator={urllib.parse.quote(locator)}")
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("deletedCount", 0) >= 1, f"Expected deletedCount >= 1: {body}"

        print(f"  PASS: deleted {body['deletedCount']} skills by source")

    def test_delete_nonexistent_source(self):
        status, body = _api("DELETE",
            f"/api/companies/{COMPANY}/skills-by-source?sourceType=nonexistent&sourceLocator=xxx")
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("deletedCount") == 0, f"Expected 0 deleted: {body}"

        print("  PASS: delete nonexistent source returns 0")

    def test_delete_missing_params(self):
        status, body = _api("DELETE", f"/api/companies/{COMPANY}/skills-by-source")
        assert status in (200, 400), f"Expected 200/400, got {status}: {body}"

        print("  PASS: delete without params handled")


# ---------------------------------------------------------------------------
# Test: GET/PUT /hidden-sources
# ---------------------------------------------------------------------------

class TestHiddenSources:

    def test_get_hidden_sources(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/hidden-sources")
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert isinstance(body, list), f"Expected list: {body}"

        print(f"  PASS: GET hidden-sources returns list ({len(body)} items)")

    def test_set_hidden_sources(self):
        status, body = _api("PUT", f"/api/companies/{COMPANY}/hidden-sources",
            [{"source_type": "catalog", "source_locator": ""}])
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert isinstance(body, list)

        status2, body2 = _api("GET", f"/api/companies/{COMPANY}/hidden-sources")
        assert status2 == 200
        assert len(body2) == 1
        assert body2[0]["source_type"] == "catalog"

        _api("PUT", f"/api/companies/{COMPANY}/hidden-sources", [])

        print("  PASS: PUT then GET hidden-sources works")

    def test_set_invalid_hidden_sources(self):
        status, body = _api("PUT", f"/api/companies/{COMPANY}/hidden-sources", "not-array")
        assert status in (400, 500), f"Expected 400/500 for non-array, got {status}"

        _api("PUT", f"/api/companies/{COMPANY}/hidden-sources", [])
        print("  PASS: invalid hidden-sources rejected")


# ---------------------------------------------------------------------------
# Test: GET /team-skills
# ---------------------------------------------------------------------------

class TestTeamSkills:

    def test_get_team_skills(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/team-skills")
        assert status in (200, 502), f"Expected 200/502, got {status}: {body}"

        if status == 200:
            assert isinstance(body, list)
            print(f"  PASS: GET team-skills returns list ({len(body)} items)")
        else:
            print(f"  PASS: GET team-skills returns 502 (no backend)")

    def test_get_team_skill_detail(self):
        status, body = _api("GET", f"/api/companies/{COMPANY}/team-skills/agent1/cat/skill1")
        assert status in (404, 502), f"Expected 404/502, got {status}: {body}"

        print("  PASS: GET team-skill detail returns 404/502")


# ---------------------------------------------------------------------------
# Test: DELETE /skills/:id - single skill delete
# ---------------------------------------------------------------------------

class TestDeleteSkill:

    def test_delete_single_skill(self):
        skill_id = _get_skill_id("Web Fetch")
        assert skill_id, "Web Fetch skill not found"

        status, body = _api("DELETE", f"/api/companies/{COMPANY}/skills/{skill_id}")
        assert status == 200, f"Expected 200, got {status}: {body}"

        _, skills = _api("GET", f"/api/companies/{COMPANY}/skills")
        names = [s["name"] for s in skills]
        assert "Web Fetch" not in names, f"Deleted skill should be gone: {names}"

        print("  PASS: delete single skill works")

    def test_delete_nonexistent_skill(self):
        status, body = _api("DELETE", f"/api/companies/{COMPANY}/skills/00000000-0000-0000-0000-000000000000")
        assert status == 404, f"Expected 404, got {status}: {body}"

        print("  PASS: delete nonexistent skill returns 404")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_tests(test_names=None):
    test_classes = [
        TestSetVisibility,
        TestDeleteBySource,
        TestHiddenSources,
        TestTeamSkills,
        TestDeleteSkill,
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
