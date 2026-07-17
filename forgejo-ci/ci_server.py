"""
forgejo-ci: minimal external CI for schematic-review-skills.

Receives Forgejo webhooks (pull_request / push), checks out the head SHA via
the Forgejo archive API, runs the schematic-review e2e pytest suite, and
reports the result as a commit status (pending/success/failure) on the SHA.
Branch protection requires this status check context to gate merges.

Runs entirely inside this container (no DinD). Pure-stdlib toolchain.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue

FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://forgejo:3000").rstrip("/")
FORGEJO_TOKEN = os.environ.get("FORGEJO_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
REPO_FULL = os.environ.get("REPO_FULL", "skill-sync/schematic-review-skills")
CONTEXT = os.environ.get("STATUS_CONTEXT", "ci/schematic-review-tests")
PORT = int(os.environ.get("PORT", "8088"))
TEST_DIR = os.environ.get(
    "TEST_DIR",
    "skills/board-design/run-schematic-review/scripts/tests",
)
TEST_TARGET = os.environ.get("TEST_TARGET", "test_review_e2e.py")
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "600"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [forgejo-ci] %(levelname)s: %(message)s",
)
log = logging.getLogger("forgejo-ci")

_jobs: Queue[tuple[str, str]] = Queue()
_logs: dict[str, str] = {}
_LOGS_MAX = 50


def _api(method: str, path: str, body: dict | None = None) -> dict | None:
    url = f"{FORGEJO_URL}/api/v1{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {FORGEJO_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except Exception as exc:
        log.error("API %s %s failed: %s", method, path, exc)
        return None


def post_status(sha: str, state: str, desc: str = "") -> None:
    _api(
        "POST",
        f"/repos/{REPO_FULL}/statuses/{sha}",
        {
            "state": state,
            "context": CONTEXT,
            "description": (desc or state)[:140],
        },
    )
    log.info("status sha=%s state=%s desc=%s", sha[:10], state, desc[:60])


def fetch_archive(sha: str, dest: str) -> bool:
    """Download the repo tree at `sha` as a tar.gz and extract into `dest`."""
    url = f"{FORGEJO_URL}/api/v1/repos/{REPO_FULL}/archive/{sha}.tar.gz"
    req = urllib.request.Request(
        url, headers={"Authorization": f"token {FORGEJO_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            chunk = resp.read()
    except Exception as exc:
        log.error("archive fetch failed for %s: %s", sha[:10], exc)
        return False
    try:
        gz = gzip.GzipFile(fileobj=io.BytesIO(chunk))
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                # strip leading "<repo>/"" directory component
                parts = member.name.split("/", 1)
                rel = parts[1] if len(parts) == 2 else parts[0]
                if not rel or rel.startswith(".."):
                    continue
                out = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(out) or dest, exist_ok=True)
                f = tar.extractfile(member)
                if f:
                    with open(out, "wb") as fh:
                        shutil.copyfileobj(f, fh)
        return True
    except Exception as exc:
        log.error("archive extract failed: %s", exc)
        return False


def run_tests(workdir: str) -> tuple[int, str]:
    test_dir = os.path.join(workdir, TEST_DIR)
    if not os.path.isdir(test_dir):
        return 1, f"test dir missing: {TEST_DIR}\n"
    try:
        r = subprocess.run(
            ["python", "-m", "pytest", TEST_TARGET, "-v", "--tb=short"],
            cwd=test_dir,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {TEST_TIMEOUT}s\n"
    out = (r.stdout or "") + "\n-- stderr --\n" + (r.stderr or "")
    return r.returncode, out


def run_job(sha: str, ref: str) -> None:
    post_status(sha, "pending", "tests running")
    start = time.time()
    tmp = tempfile.mkdtemp(prefix="ci-")
    try:
        if not fetch_archive(sha, tmp):
            post_status(sha, "failure", "failed to fetch repo archive")
            return
        rc, output = run_tests(tmp)
        elapsed = int(time.time() - start)
        tail = output[-6000:]
        _logs[sha] = tail
        _trim_logs(sha)
        if rc == 0:
            post_status(sha, "success", f"all tests passed ({elapsed}s)")
            log.info("PASS sha=%s in %ds", sha[:10], elapsed)
        else:
            # extract failure summary line(s)
            summary = _failure_summary(output) or f"tests failed rc={rc} ({elapsed}s)"
            post_status(sha, "failure", summary)
            log.warning("FAIL sha=%s rc=%d in %ds", sha[:10], rc, elapsed)
    except Exception as exc:
        post_status(sha, "error", f"CI error: {str(exc)[:120]}")
        log.exception("job crashed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _failure_summary(output: str) -> str:
    import re

    m = re.search(r"(\d+\s*failed.*?(?:passed.*)?)", output)
    if m:
        return m.group(1).replace("=", " ").strip()[:140]
    for line in output.splitlines():
        if " failed" in line and ("::" in line or line.strip().startswith("FAILED")):
            return line.strip()[:140]
    m2 = [ln for ln in output.splitlines() if "failed" in ln.lower()]
    return (m2[-1].strip()[:140] if m2 else "tests failed")


def _trim_logs(keep_last: str) -> None:
    global _logs
    if len(_logs) > _LOGS_MAX:
        _logs = {keep_last: _logs.get(keep_last, "")} | {
            k: v for k, v in list(_logs.items())[-_LOGS_MAX:]
        }


def worker() -> None:
    while True:
        sha, ref = _jobs.get()
        try:
            run_job(sha, ref)
        except Exception:
            log.exception("worker crashed")
        finally:
            _jobs.task_done()


class Handler(BaseHTTPRequestHandler):
    server_version = "forgejo-ci/1.0"

    def _send(self, code: int, body: bytes = b"{}", ctype="application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, b'{"ok":true}')
            return
        if self.path.startswith("/logs/"):
            sha = self.path.split("/logs/", 1)[1].strip()
            body = _logs.get(sha, "(no log stored for this sha)\n")
            if isinstance(body, str):
                body = body.encode()
            self._send(200, body, ctype="text/plain; charset=utf-8")
            return
        self._send(404, b'{"error":"not found"}')

    def do_POST(self) -> None:
        if self.path != "/webhook":
            self._send(404, b'{"error":"not found"}')
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if WEBHOOK_SECRET:
            sig = (
                self.headers.get("X-Gitea-Signature")
                or self.headers.get("X-Forgejo-Signature")
                or ""
            )
            mac = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, mac):
                log.warning("webhook signature mismatch")
                self._send(401, b'{"error":"bad signature"}')
                return
        try:
            payload = json.loads(body or b"{}")
        except Exception:
            payload = {}
        event = (
            self.headers.get("X-Gitea-Event")
            or self.headers.get("X-Forgejo-Event")
            or ""
        )
        action = payload.get("action", "")
        repo = payload.get("repository", {}).get("full_name", "")
        log.info(
            "webhook event=%s action=%s repo=%s len=%d",
            event, action, repo, len(body),
        )
        if repo and repo != REPO_FULL:
            self._send(200, b'{"ignored":"other repo"}')
            return
        sha = None
        should = False
        if event == "pull_request":
            if action in ("opened", "synchronize", "synchronized", "reopened", "edited", "ready_for_review"):
                sha = payload.get("pull_request", {}).get("head", {}).get("sha")
                should = bool(sha)
        elif event == "push":
            ref = payload.get("ref", "")
            if ref.endswith("/main"):
                sha = payload.get("after")
                should = bool(sha)
        if should and sha:
            _jobs.put((sha, event))
            log.info("queued %s event for sha=%s (depth=%d)", event, sha[:10], _jobs.qsize())
            self._send(202, b'{"accepted":true}')
        else:
            self._send(200, b'{"ignored":true}')

    def log_message(self, fmt, *args) -> None:  # noqa: signature
        log.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    if not FORGEJO_TOKEN:
        log.warning("FORGEJO_TOKEN unset — status posting will fail")
    threading.Thread(target=worker, daemon=True).start()
    log.info(
        "forgejo-ci listening on :%s  repo=%s  context=%s", PORT, REPO_FULL, CONTEXT
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
