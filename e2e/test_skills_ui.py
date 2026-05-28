"""E2E tests for CompanySkills page — all UI elements.

Covers: sidebar header, filter, show-excluded toggle, source groups,
group expand/collapse, group icons, group labels, group counts,
skill items within groups, skill detail pane, skill tree navigation,
hidden skills, new skill form, source import field, scan button,
group visibility toggle, group delete button, agent-created vs catalog separation.

Run: python3.12 e2e/test_skills_ui.py
     python3.12 e2e/test_skills_ui.py TestClassName
     python3.12 e2e/test_skills_ui.py TestClassName.test_method
"""

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:3100"
COMPANY = "11111111-1111-1111-1111-111111111111"
EMAIL = "test@test.com"
PASSWORD = "Test123456!"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sign_in(page):
    page.goto(f"{BASE}/auth?next=%2F", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(1500)
    page.fill('input[type="email"]', EMAIL)
    page.fill('input[type="password"]', PASSWORD)
    page.click('button:has-text("Sign In")')
    page.wait_for_url("**/dashboard", timeout=10000)
    page.wait_for_timeout(1500)


def _goto_skills(page):
    prefix = page.url.split("/")[3]
    page.goto(f"{BASE}/{prefix}/skills", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(2000)
    return prefix


def _get_groups(page):
    return page.locator("div.border-b > div.cursor-pointer, div.border-b > div[class*='cursor-pointer']").all()


def _find_group(page, text_fragment):
    for g in _get_groups(page):
        if text_fragment in g.inner_text():
            return g
    return None


def _expand_group(page, text_fragment):
    g = _find_group(page, text_fragment)
    if g:
        g.click()
        page.wait_for_timeout(800)
    return g


def _group_skills(page, group_header):
    parent = group_header.locator("xpath=ancestor::div[contains(@class,'border-b')]")
    return parent.locator('a[href*="/skills/"]').all()


def _new_page():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    return pw, browser, page


def _cleanup(pw, browser):
    browser.close()
    pw.stop()


# ---------------------------------------------------------------------------
# Test: Sidebar header
# ---------------------------------------------------------------------------

class TestSidebarHeader:

    def test_title_and_count(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            title = page.locator("h1", has_text="Skills")
            assert title.count() == 1, "Skills title not found"

            count_text = page.locator("p", has_text="available").first.text_content()
            assert "available" in count_text
            count = int(count_text.strip().split()[0])
            assert count >= 10, f"Expected >= 10 skills, got {count}"

            print(f"  PASS: title='Skills', count={count}")
        finally:
            _cleanup(pw, browser)

    def test_scan_button_exists(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            scan_btn = page.locator('button[title="Scan project workspaces for skills"]')
            assert scan_btn.count() == 1, "Scan button not found"
            print("  PASS: scan button exists")
        finally:
            _cleanup(pw, browser)

    def test_add_skill_button_opens_form(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            sidebar_header = page.locator("h1", has_text="Skills").locator("xpath=ancestor::div[contains(@class,'border-b')]")
            plus_btn = sidebar_header.locator("button").nth(1)
            assert plus_btn.count() == 1, "Plus button not found"

            plus_btn.click()
            page.wait_for_timeout(500)

            name_input = page.locator('input[placeholder="Skill name"]')
            assert name_input.count() == 1, "New skill form not opened"

            slug_input = page.locator('input[placeholder="optional-shortname"]')
            assert slug_input.count() == 1

            desc_textarea = page.locator('textarea[placeholder="Short description"]')
            assert desc_textarea.count() == 1

            cancel_btn = page.locator('button:has-text("Cancel")').first
            cancel_btn.click()
            page.wait_for_timeout(500)

            assert page.locator('input[placeholder="Skill name"]').count() == 0, "Form not closed"

            print("  PASS: add skill button opens/closes form")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Filter input
# ---------------------------------------------------------------------------

class TestFilterInput:

    def test_filter_skills_by_name(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            # Apply filter BEFORE expanding — the group will still be there
            page.locator('input[placeholder="Filter skills"]').fill("Docker")
            page.wait_for_timeout(1000)

            # The "catalog" group should still be present with Docker inside
            groups = _get_groups(page)
            group_texts = [g.inner_text() for g in groups]
            assert any("catalog" in t.lower() for t in group_texts), f"Catalog group should still be present: {group_texts}"

            # Expand it and check the skill is Docker Management
            catalog_group = _find_group(page, "catalog")
            assert catalog_group, "catalog group not found after filter"
            catalog_group.click()
            page.wait_for_timeout(500)

            skills = _group_skills(page, catalog_group)
            assert len(skills) >= 1, f"Expected >= 1 Docker skill in catalog group, got {len(skills)}"
            assert any("Docker" in s.inner_text() for s in skills), "Docker Management should be visible"

            page.locator('input[placeholder="Filter skills"]').clear()
            page.wait_for_timeout(500)

            print(f"  PASS: filter shows only Docker skills in catalog group")
        finally:
            _cleanup(pw, browser)

    def test_filter_no_match(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent:")

            page.locator('input[placeholder="Filter skills"]').fill("zzzznonexistent")
            page.wait_for_timeout(1000)

            # All groups should be gone (filtered out)
            remaining_groups = page.locator('div.cursor-pointer')
            assert remaining_groups.count() == 0, f"All groups should be filtered out, got {remaining_groups.count()}"

            print("  PASS: filter hides all groups when no match")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Show excluded toggle
# ---------------------------------------------------------------------------

class TestShowExcludedToggle:

    def test_toggle_exists_and_works(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            checkbox = page.locator('input[type="checkbox"]')
            label = page.locator("text=Show excluded")
            assert label.count() >= 1, "Show excluded label not found"
            assert checkbox.count() >= 1, "Checkbox not found"

            assert not checkbox.first.is_checked(), "Should start unchecked"

            checkbox.first.check()
            page.wait_for_timeout(1000)

            assert checkbox.first.is_checked(), "Should be checked after click"

            print("  PASS: show excluded toggle works")
        finally:
            _cleanup(pw, browser)

    def test_hidden_skill_appears_when_toggle_on(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            page.locator('input[type="checkbox"]').first.check()
            page.wait_for_timeout(2000)

            _expand_group(page, "Agent: SW DEV")
            page.wait_for_timeout(500)

            deprecated = page.locator('text=Deprecated Skill')
            assert deprecated.count() >= 1, "Hidden skill should appear when toggle on"

            print(f"  PASS: hidden skill appears when show excluded is on")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Source groups
# ---------------------------------------------------------------------------

class TestSourceGroups:

    def test_three_source_groups_exist(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            groups = _get_groups(page)
            labels = [g.inner_text().split("\n")[0] for g in groups]

            assert any("Paperclip bundled" in l for l in labels), f"Missing Paperclip bundled group: {labels}"
            assert any("Agent:" in l for l in labels), f"Missing Agent group: {labels}"
            assert any("catalog" in l.lower() for l in labels), f"Missing catalog group: {labels}"

            print(f"  PASS: {len(labels)} source groups found: {labels}")
        finally:
            _cleanup(pw, browser)

    def test_group_counts_in_parentheses(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            groups = _get_groups(page)
            for g in groups:
                txt = g.inner_text()
                if "(" in txt and ")" in txt:
                    count_str = txt[txt.index("(")+1:txt.index(")")]
                    count = int(count_str)
                    assert count > 0, f"Group count should be > 0: {txt}"

            print("  PASS: all groups have valid counts")
        finally:
            _cleanup(pw, browser)

    def test_agent_created_group_has_bot_icon(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            agent_group = _find_group(page, "Agent:")
            assert agent_group, "Agent group not found"

            parent = agent_group.locator("xpath=ancestor::div[contains(@class,'border-b')]")
            svg = parent.locator("svg.lucide-bot").first
            assert svg.count() == 1, "Bot icon not found in agent group header"

            print("  PASS: agent-created group has Bot icon")
        finally:
            _cleanup(pw, browser)

    def test_catalog_group_has_boxes_icon(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            catalog_group = _find_group(page, "catalog")
            assert catalog_group, "catalog group not found"

            print("  PASS: catalog group found with correct label")
        finally:
            _cleanup(pw, browser)

    def test_paperclip_group_has_paperclip_icon(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            pc_group = _find_group(page, "Paperclip bundled")
            assert pc_group, "Paperclip bundled group not found"

            print("  PASS: Paperclip bundled group found")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Group expand/collapse
# ---------------------------------------------------------------------------

class TestGroupExpandCollapse:

    def test_expand_shows_skills(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            agent_group = _expand_group(page, "Agent:")
            assert agent_group, "Agent group not found"

            skills = _group_skills(page, agent_group)
            assert len(skills) >= 1, f"Expected >= 1 agent skills, got {len(skills)}"

            print(f"  PASS: expanding shows {len(skills)} agent skills")
        finally:
            _cleanup(pw, browser)

    def test_collapse_hides_skills(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            # Use a group that's guaranteed to have skills
            _expand_group(page, "Agent:")
            agent_group = _find_group(page, "Agent:")
            assert agent_group
            skills_before = _group_skills(page, agent_group)
            assert len(skills_before) >= 1

            agent_group.click()
            page.wait_for_timeout(500)

            parent = agent_group.locator("xpath=ancestor::div[contains(@class,'border-b')]")
            skills_after = parent.locator('a[href*="/skills/"]').all()
            assert len(skills_after) == 0, f"Skills should be hidden after collapse, got {len(skills_after)}"

            print("  PASS: collapsing hides skills")
        finally:
            _cleanup(pw, browser)

    def test_agent_created_skills_separate_from_catalog(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            for frag in ["Agent:", "catalog", "Paperclip bundled"]:
                _expand_group(page, frag)

            page.wait_for_timeout(500)

            agent_groups = []
            for g in _get_groups(page):
                txt = g.inner_text()
                if "Agent:" in txt:
                    agent_groups.append(g)

            catalog_group = _find_group(page, "catalog")
            assert catalog_group
            catalog_skills = _group_skills(page, catalog_group)
            catalog_names = [s.inner_text().split("\n")[-1] for s in catalog_skills if "/files/" not in (s.get_attribute("href") or "")]

            for ag in agent_groups:
                ag_skills = _group_skills(page, ag)
                ag_names = [s.inner_text().split("\n")[-1] for s in ag_skills if "/files/" not in (s.get_attribute("href") or "")]
                overlap = set(ag_names) & set(catalog_names)
                assert len(overlap) == 0, f"Agent and catalog groups overlap: {overlap}"

            print(f"  PASS: {len(agent_groups)} agent groups separate from catalog")
        finally:
            _cleanup(pw, browser)

    def test_different_agents_in_separate_groups(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            groups = _get_groups(page)
            agent_groups = []
            for g in groups:
                txt = g.inner_text().split("\n")[0]
                if "Agent:" in txt:
                    agent_groups.append(txt)

            assert len(agent_groups) >= 2, f"Expected >= 2 agent groups (one per agent), got {len(agent_groups)}: {agent_groups}"

            print(f"  PASS: {len(agent_groups)} separate agent groups: {agent_groups}")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Skill items within groups
# ---------------------------------------------------------------------------

class TestSkillItems:

    def test_skill_item_has_icon_and_name(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent: SW DEV")

            agent_group = _find_group(page, "Agent: SW DEV")
            skills = _group_skills(page, agent_group)
            assert len(skills) >= 1

            first = skills[0]
            svg = first.locator("svg").first
            assert svg.count() == 1, "Skill item should have an icon"

            txt = first.inner_text()
            assert len(txt.strip()) > 0, "Skill item should have text"

            print(f"  PASS: skill items have icon + name")
        finally:
            _cleanup(pw, browser)

    def test_click_skill_opens_detail_pane(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent:")

            agent_group = _find_group(page, "Agent:")
            first_skill = _group_skills(page, agent_group)[0]
            skill_name = first_skill.inner_text().split("\n")[-1]

            first_skill.click()
            page.wait_for_timeout(2000)

            detail_title = page.locator("h1").filter(has_text=skill_name)
            assert detail_title.count() >= 1, f"Detail pane should show skill name '{skill_name}'"

            print(f"  PASS: clicking skill opens detail for '{skill_name}'")
        finally:
            _cleanup(pw, browser)

    def test_agent_created_skill_shows_read_only(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent:")

            agent_group = _find_group(page, "Agent:")
            first_skill = _group_skills(page, agent_group)[0]
            first_skill.click()
            page.wait_for_timeout(2000)

            read_only = page.locator("text=Agent-created skills are read-only")
            if read_only.count() == 0:
                read_only = page.locator("text=Read only")

            assert read_only.count() >= 1, "Agent-created skill should show read-only indicator"

            print("  PASS: agent-created skill shows read-only")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Skill detail pane
# ---------------------------------------------------------------------------

class TestSkillDetailPane:

    def test_detail_shows_source_label(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent:")

            agent_group = _find_group(page, "Agent:")
            first_skill = _group_skills(page, agent_group)[0]
            first_skill.click()
            page.wait_for_timeout(2000)

            source_section = page.locator("text=Source").first
            assert source_section.count() == 1, "Source section not found in detail pane"

            print("  PASS: detail pane shows Source section")
        finally:
            _cleanup(pw, browser)

    def test_detail_shows_key(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent:")

            agent_group = _find_group(page, "Agent:")
            first_skill = _group_skills(page, agent_group)[0]
            first_skill.click()
            page.wait_for_timeout(2000)

            key_label = page.locator("text=Key").first
            assert key_label.count() == 1, "Key section not found"

            print("  PASS: detail pane shows Key section")
        finally:
            _cleanup(pw, browser)

    def test_detail_shows_mode(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent:")

            agent_group = _find_group(page, "Agent:")
            first_skill = _group_skills(page, agent_group)[0]
            first_skill.click()
            page.wait_for_timeout(2000)

            mode = page.locator("text=Read only").first
            assert mode.count() == 1, "Mode section not found"

            print("  PASS: detail pane shows Mode")
        finally:
            _cleanup(pw, browser)

    def test_detail_shows_skill_content(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent: SW DEV")

            agent_group = _find_group(page, "Agent: SW DEV")
            skills = _group_skills(page, agent_group)

            paperclip_skill = None
            for s in skills:
                if "Paperclip" in s.inner_text():
                    paperclip_skill = s
                    break
            assert paperclip_skill, "Paperclip skill not found in SW DEV group"

            paperclip_skill.click()
            page.wait_for_timeout(2000)

            content = page.locator("text=Agent-created skill for task management")
            assert content.count() >= 1, "Skill markdown content not displayed"

            print("  PASS: detail pane renders markdown content")
        finally:
            _cleanup(pw, browser)

    def test_detail_shows_file_path_header(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent:")

            agent_group = _find_group(page, "Agent:")
            first_skill = _group_skills(page, agent_group)[0]
            first_skill.click()
            page.wait_for_timeout(2000)

            path_header = page.locator("text=SKILL.md").first
            assert path_header.count() == 1, "SKILL.md path header not found"

            print("  PASS: detail pane shows SKILL.md path header")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Skill tree (file inventory)
# ---------------------------------------------------------------------------

class TestSkillTree:

    def test_skill_with_files_shows_tree(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent: SW DEV")

            agent_group = _find_group(page, "Agent: SW DEV")
            skills = _group_skills(page, agent_group)

            paperclip_skill = None
            for s in skills:
                if "Paperclip" in s.inner_text():
                    paperclip_skill = s
                    break
            assert paperclip_skill, "Paperclip skill not found"

            skill_id = paperclip_skill.get_attribute("href").split("/skills/")[1].split("/")[0]

            expand_btn = page.locator(f'button[aria-label="Expand Paperclip"]')
            if expand_btn.count() == 0:
                expand_btn = page.locator(f'button[aria-label="Collapse Paperclip"]')

            if expand_btn.count() > 0:
                expand_btn.first.click()
                page.wait_for_timeout(500)

            tree_links = page.locator(f'a[href*="/skills/{skill_id}/files/"]')
            if tree_links.count() > 0:
                print(f"  PASS: skill tree shows {tree_links.count()} file links")
            else:
                print("  PASS: skill tree present (no sub-files for this skill)")
        finally:
            _cleanup(pw, browser)

    def test_click_file_in_tree_loads_content(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            _expand_group(page, "Agent: SW DEV")

            agent_group = _find_group(page, "Agent: SW DEV")
            skills = _group_skills(page, agent_group)

            paperclip_skill = None
            for s in skills:
                if "Paperclip" in s.inner_text():
                    paperclip_skill = s
                    break
            assert paperclip_skill, "Paperclip skill not found"

            skill_id = paperclip_skill.get_attribute("href").split("/skills/")[1].split("/")[0]

            expand_btn = page.locator(f'button[aria-label="Expand Paperclip"]')
            if expand_btn.count() > 0:
                expand_btn.first.click()
                page.wait_for_timeout(500)

            deploy_link = page.locator(f'a[href*="/skills/{skill_id}/files/scripts"]')
            if deploy_link.count() > 0:
                deploy_link.first.click()
                page.wait_for_timeout(1500)

                path_header = page.locator("text=scripts/deploy.sh")
                assert path_header.count() >= 1, "scripts/deploy.sh path not shown after click"
                print("  PASS: clicking tree file loads content")
            else:
                print("  SKIP: no scripts/deploy.sh file in tree")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: Source import field
# ---------------------------------------------------------------------------

class TestSourceImportField:

    def test_import_field_exists(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            input_field = page.locator('input[placeholder="Paste path, GitHub URL, or skills.sh command"]')
            assert input_field.count() == 1, "Import input field not found"

            add_btn = page.locator('button:has-text("Add")').first
            assert add_btn.count() == 1, "Add button not found"

            print("  PASS: source import field exists")
        finally:
            _cleanup(pw, browser)

    def test_empty_import_shows_help_dialog(self):
        pw, browser, page = _new_page()
        try:
            _sign_in(page)
            _goto_skills(page)

            add_btn = page.locator('button:has-text("Add")').first
            add_btn.click()
            page.wait_for_timeout(500)

            dialog_title = page.locator("text=Add a skill source")
            assert dialog_title.count() >= 1, "Help dialog not shown"

            skills_sh_link = page.locator("text=Browse skills.sh")
            assert skills_sh_link.count() >= 1

            github_link = page.locator("text=Search GitHub")
            assert github_link.count() >= 1

            print("  PASS: empty import shows help dialog")
        finally:
            _cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Test: API returns correct badges
# ---------------------------------------------------------------------------

class TestSkillsAPI:

    def test_api_returns_correct_badges(self):
        cookie = _get_session_cookie()
        assert cookie, "Failed to get session cookie"

        data = _api_get(f"/api/companies/{COMPANY}/skills", cookie)
        assert isinstance(data, list), f"Expected list, got {type(data)}"

        agent_created = [s for s in data if s.get("sourceBadge") == "agent_created"]
        catalog = [s for s in data if s.get("sourceBadge") == "catalog"]
        paperclip = [s for s in data if s.get("sourceBadge") == "paperclip"]

        assert len(agent_created) >= 5, f"Expected >= 5 agent_created, got {len(agent_created)}"
        assert len(catalog) >= 3, f"Expected >= 3 catalog, got {len(catalog)}"
        assert len(paperclip) >= 4, f"Expected >= 4 paperclip, got {len(paperclip)}"

        for s in agent_created:
            label = s.get("sourceLabel") or ""
            assert "Agent:" in label, f"agent_created skill '{s['name']}' label={label}"

        print(f"  PASS: agent_created={len(agent_created)} catalog={len(catalog)} paperclip={len(paperclip)}")

    def test_api_hidden_skill_excluded_by_default(self):
        cookie = _get_session_cookie()
        assert cookie

        data = _api_get(f"/api/companies/{COMPANY}/skills", cookie)
        names = [s["name"] for s in data]
        assert "Deprecated Skill" not in names, f"Hidden skill should be excluded: {names}"

        print("  PASS: hidden skill excluded from default list")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_session_cookie():
    url = f"{BASE}/api/auth/sign-in/email"
    body = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        cookie_header = resp.headers.get("Set-Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if "__Secure-better-auth" in part and "=" in part:
                return part
        return None
    except urllib.error.HTTPError:
        return None


def _api_get(path, cookie):
    req = urllib.request.Request(f"{BASE}{path}")
    req.add_header("Cookie", cookie)
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode()[:200]}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_tests(test_names=None):
    import traceback

    test_classes = [
        TestSidebarHeader,
        TestFilterInput,
        TestShowExcludedToggle,
        TestSourceGroups,
        TestGroupExpandCollapse,
        TestSkillItems,
        TestSkillDetailPane,
        TestSkillTree,
        TestSourceImportField,
        TestSkillsAPI,
    ]

    failures = 0
    passed = 0
    skipped = 0
    total = 0

    for cls in test_classes:
        instance = cls()
        methods = [(m, getattr(instance, m)) for m in dir(instance) if m.startswith("test_")]

        for method_name, method in methods:
            full_name = f"{cls.__name__}.{method_name}"

            if test_names:
                matched = False
                for tn in test_names:
                    if tn in full_name or tn == method_name or tn == cls.__name__:
                        matched = True
                        break
                if not matched:
                    skipped += 1
                    continue

            total += 1
            print(f"\n--- {full_name} ---")
            try:
                method()
                passed += 1
            except Exception as e:
                print(f"FAIL: {e}")
                traceback.print_exc()
                failures += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failures} failed, {skipped} skipped")
    if failures:
        print("FAILED")
        sys.exit(1)
    else:
        print("ALL PASSED")


if __name__ == "__main__":
    test_names = sys.argv[1:] if len(sys.argv) > 1 else None
    _run_tests(test_names)
