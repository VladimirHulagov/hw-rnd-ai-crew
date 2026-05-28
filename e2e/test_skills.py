"""E2E tests for CompanySkills grouping — agent_created vs catalog separation.

Run with: python3.12 e2e/test_skills.py
"""

import json
import sys
import time
import urllib.request
import urllib.parse
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "http://127.0.0.1:3100"
COMPANY = "11111111-1111-1111-1111-111111111111"
EMAIL = "test@test.com"
PASSWORD = "Test123456!"


def _api(method, path, data=None, cookie=None):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode()[:200]}


def _get_session_cookie():
    """Sign in and extract session cookie from Set-Cookie header."""
    url = f"{BASE}/api/auth/sign-in/email"
    body = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        cookie_header = resp.headers.get("Set-Cookie", "")
        # Parse cookie: __Secure-better-auth.session_token=...;
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("__Secure-better-auth.session_token="):
                return part
        return None
    except urllib.error.HTTPError as e:
        print(f"Login failed: HTTP {e.code}: {e.read().decode()[:200]}")
        return None


def test_skills_api_returns_correct_badges():
    """Server returns distinct sourceBadge for agent_created vs catalog skills."""
    cookie = _get_session_cookie()
    assert cookie, "Failed to get session cookie"

    data = _api("GET", f"/api/companies/{COMPANY}/skills", cookie=cookie)
    if isinstance(data, dict) and "error" in data:
        print(f"FAIL: API error: {data}")
        sys.exit(1)

    skills = data if isinstance(data, list) else data.get("skills", [])
    assert len(skills) > 0, "No skills returned"

    agent_created = [s for s in skills if s.get("sourceBadge") == "agent_created"]
    catalog = [s for s in skills if s.get("sourceBadge") == "catalog"]

    print(f"  agent_created: {len(agent_created)}")
    for s in agent_created:
        print(f"    {s['name']} (label={s.get('sourceLabel')})")
    print(f"  catalog: {len(catalog)}")
    for s in catalog:
        print(f"    {s['name']} (label={s.get('sourceLabel')})")

    assert len(agent_created) >= 3, f"Expected >= 3 agent_created, got {len(agent_created)}"
    assert len(catalog) >= 2, f"Expected >= 2 catalog, got {len(catalog)}"

    for s in agent_created:
        assert "Agent:" in (s.get("sourceLabel") or ""), f"agent_created skill '{s['name']}' has wrong label: {s.get('sourceLabel')}"

    print("PASS: Skills API returns correct badges")


def test_ui_groups_agent_created_separately():
    """Playwright test: expanding Agent group does NOT show catalog skills."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        page.goto(f"{BASE}/auth?next=%2F", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        page.fill('input[type="email"]', EMAIL)
        page.fill('input[type="password"]', PASSWORD)
        page.click('button:has-text("Sign In")')
        page.wait_for_url("**/dashboard", timeout=10000)
        page.wait_for_timeout(2000)

        current_url = page.url
        company_prefix = current_url.split("/")[3] if len(current_url.split("/")) > 3 else "SUC"
        skills_url = f"{BASE}/{company_prefix}/skills"
        page.goto(skills_url, wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        group_headers = page.locator("div.cursor-pointer").all()
        agent_group = None
        catalog_group = None
        for gh in group_headers:
            txt = gh.inner_text()
            if "Agent:" in txt:
                agent_group = gh
            elif "Hermes" in txt or "catalog" in txt.lower():
                catalog_group = gh

        assert agent_group is not None, "No 'Agent:' group found on skills page"

        agent_group.click()
        page.wait_for_timeout(1000)

        group_div = None
        parent_divs = page.locator("div.border-b").all()
        for pd in parent_divs:
            hdr = pd.locator("div.cursor-pointer").first
            if hdr.count() and "Agent:" in hdr.inner_text():
                group_div = pd
                break

        assert group_div is not None, "Agent group div not found after expand"

        skill_links = group_div.locator('a[href*="/skills/"]').all()
        skill_names = [sl.inner_text().split("\n")[-1] for sl in skill_links]

        print(f"  Agent group skills: {skill_names}")

        catalog_names = {"Docker Management", "Git Operations"}
        found_catalog = [n for n in skill_names if n in catalog_names]

        assert len(found_catalog) == 0, (
            f"Agent group contains catalog skills: {found_catalog}"
        )
        assert len(skill_names) >= 3, f"Expected >= 3 agent skills, got {len(skill_names)}"

        print("PASS: Agent-created skills grouped separately from catalog")
        browser.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in [
        ("test_skills_api_returns_correct_badges", test_skills_api_returns_correct_badges),
        ("test_ui_groups_agent_created_separately", test_ui_groups_agent_created_separately),
    ]:
        print(f"\n--- {name} ---")
        try:
            fn()
        except Exception as e:
            print(f"FAIL: {e}")
            failures += 1

    print(f"\n{'='*40}")
    if failures:
        print(f"FAILED: {failures} test(s)")
        sys.exit(1)
    else:
        print("ALL PASSED")
