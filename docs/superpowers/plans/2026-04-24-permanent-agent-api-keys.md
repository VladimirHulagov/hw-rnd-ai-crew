# Permanent Agent API Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-run JWT tokens with permanent Agent API keys for Paperclip API authentication, eliminating JWT staleness (401 errors when heartbeat_run is deleted mid-run).

**Architecture:** Agents already have permanent API keys (`pcp_*` tokens in `agent_api_keys.json`, validated via `agent_api_keys` table). The problem is `api_server.py` overwrites the permanent key with a per-run JWT on every heartbeat. Fix: stop overwriting, pass `run_id` separately via `X-Paperclip-Run-ID` header, forward through paperclip-mcp.

**Tech Stack:** Python (api_server.py), TypeScript (execute.ts adapter), Python (paperclip-mcp tools.py)

---

### Task 1: Stop JWT overwrite in api_server.py

**Files:**
- Modify: `hermes-agent/gateway/platforms/api_server.py:1421-1434`

Currently `_handle_runs` overwrites `PAPERCLIP_RUN_API_KEY` with the JWT from the adapter on every heartbeat run. When the permanent API key exists, skip the overwrite and only set `PAPERCLIP_HEARTBEAT_RUN_ID`.

- [ ] **Step 1: Edit api_server.py — replace JWT overwrite with run_id passthrough**

Replace lines 1421-1434 in `api_server.py`:

```python
        _new_key = body.get("paperclip_api_key")
        _run_id_str = str(run_id) if run_id else ""
        _existing_key = os.environ.get("PAPERCLIP_RUN_API_KEY", "")
        _has_permanent_key = _existing_key.startswith("pcp_")

        if _new_key and not _has_permanent_key:
            os.environ["PAPERCLIP_RUN_API_KEY"] = _new_key
        os.environ["PAPERCLIP_HEARTBEAT_RUN_ID"] = _run_id_str

        if _new_key or _has_permanent_key:
            try:
                from tools.mcp_tool import _servers, _lock, _run_on_mcp_loop
                with _lock:
                    _old = _servers.pop("paperclip", None)
                if _old:
                    try:
                        _run_on_mcp_loop(_old.shutdown(), timeout=10)
                    except Exception:
                        _old.session = None
            except Exception:
                pass
```

Logic:
- If agent already has a `pcp_*` permanent key → don't overwrite it, just pass `run_id` via env var
- If no permanent key (fallback JWT mode) → keep old behavior for backward compatibility
- MCP reconnect still happens (forces paperclip MCP to re-read config with new headers)

- [ ] **Step 2: Copy patched file to site-packages**

```bash
docker exec hermes-gateway cp /opt/hermes-agent-build/gateway/platforms/api_server.py /usr/local/lib/python3.11/site-packages/gateway/platforms/api_server.py
```

- [ ] **Step 3: Verify no Python syntax errors**

```bash
docker exec hermes-gateway python3 -c "import gateway.platforms.api_server"
```

---

### Task 2: Add X-Paperclip-Run-ID header to agent config

**Files:**
- Modify: `hermes-gateway/orchestrator/config_generator.py` (or wherever config template is generated)
- Modify: `hermes-gateway/config-template.yaml` (if hardcoded template)

The agent's MCP config for paperclip needs an additional header `X-Paperclip-Run-ID` that uses the `PAPERCLIP_HEARTBEAT_RUN_ID` env var.

- [ ] **Step 1: Find the config template for paperclip MCP headers**

Check `hermes-gateway/config-template.yaml` for the paperclip MCP section and add the run_id header:

```yaml
paperclip:
  url: http://paperclip-mcp:8082/mcp
  headers:
    X-Paperclip-Api-Key: "${PAPERCLIP_RUN_API_KEY}"
    X-Paperclip-Company-Id: "${PAPERCLIP_COMPANY_ID}"
    X-Paperclip-Agent-Id: "${PAPERCLIP_AGENT_ID}"
    X-Paperclip-Run-ID: "${PAPERCLIP_HEARTBEAT_RUN_ID}"
```

- [ ] **Step 2: Verify config is used by checking the generated config for an agent**

```bash
docker exec hermes-gateway cat /root/.hermes/profiles/c7826470-3b08-49ad-b1d9-e73911ed64f9/config.yaml | grep -A5 "X-Paperclip"
```

- [ ] **Step 3: Restart orchestrator to pick up template change**

The orchestrator detects source changes via hash fingerprint. Verify it regenerates configs:

```bash
docker logs hermes-gateway --since 2m 2>&1 | grep "Updated config"
```

---

### Task 3: Forward X-Paperclip-Run-ID in paperclip-mcp

**Files:**
- Modify: `paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py:58-65` (context extraction)
- Modify: `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py:26-30` (_headers function)

paperclip-mcp needs to extract `X-Paperclip-Run-ID` from the incoming MCP connection and forward it as a header when calling Paperclip API.

- [ ] **Step 1: Extract run_id in main.py `_extract_context()`**

Add after the existing `agent_id` extraction (around line 65):

```python
    run_id = headers.get("x-paperclip-run-id", "")
    set_context(api_key=api_key, company_id=company_id, agent_id=agent_id, run_id=run_id)
```

Update `set_context` function signature to accept `run_id`:

```python
def set_context(api_key="", company_id="", agent_id="", run_id=""):
    global _current_api_key, _current_company_id, _current_agent_id, _current_run_id
    _current_api_key = api_key
    _current_company_id = company_id
    _current_agent_id = agent_id
    _current_run_id = run_id
```

Add global declaration at module level:
```python
_current_run_id = ""
```

- [ ] **Step 2: Forward run_id in tools.py `_headers()`**

Update the `_headers()` function:

```python
def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _current_api_key:
        h["Authorization"] = f"Bearer {_current_api_key}"
    if _current_run_id:
        h["X-Paperclip-Run-ID"] = _current_run_id
    return h
```

Add import of `_current_run_id` (or access from main module). Since tools.py already imports from main or they share globals, ensure `_current_run_id` is accessible. Check the import pattern and add accordingly.

- [ ] **Step 3: Deploy to paperclip-mcp container**

```bash
docker cp paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py paperclip-mcp:/app/mcp_server/main.py
docker cp paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py paperclip-mcp:/app/mcp_server/tools.py
docker restart paperclip-mcp
```

- [ ] **Step 4: Verify with MCP handshake**

```bash
docker exec hermes-gateway python3 -c "
import urllib.request, json
# Full handshake then call a tool
init = json.dumps({'jsonrpc':'2.0','method':'initialize','id':1,'params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1.0'}}}).encode()
req = urllib.request.Request('http://paperclip-mcp:8082/mcp', data=init, headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
urllib.request.urlopen(req, timeout=10)
notif = json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'}).encode()
req2 = urllib.request.Request('http://paperclip-mcp:8082/mcp', data=notif, headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
try: urllib.request.urlopen(req2, timeout=10)
except: pass
# Call list_issues with run_id header
call = json.dumps({'jsonrpc':'2.0','method':'tools/call','id':2,'params':{'name':'paperclip_list_issues','arguments':{'status':'todo'}}}).encode()
req3 = urllib.request.Request('http://paperclip-mcp:8082/mcp', data=call, headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','X-Paperclip-Api-Key':'test-key','X-Paperclip-Run-ID':'test-run-id'})
resp = urllib.request.urlopen(req3, timeout=10)
print(resp.read().decode()[:300])
"
```

---

### Task 4: Verify permanent keys exist for all agents

**Files:**
- Read: `hermes-gateway/orchestrator/agent_api_keys.json`
- DB: `agent_api_keys` table

- [ ] **Step 1: Check agent_api_keys.json has entries for all active agents**

```bash
cat hermes-gateway/orchestrator/agent_api_keys.json | python3 -m json.tool
```

Expected: entries for `c7826470` (FE), `26fca86c` (CEO), `bff38103` (007).

- [ ] **Step 2: Verify keys are registered in Paperclip DB**

```bash
docker exec paperclip-db psql -U paperclip -c "SELECT ak.name, a.name as agent_name, ak.key_hash IS NOT NULL as has_hash, ak.revoked_at IS NULL as active FROM agent_api_keys ak JOIN agents a ON a.id = ak.agent_id ORDER BY ak.created_at;"
```

If any agent is missing a key, create one via the Paperclip API.

- [ ] **Step 3: Verify orchestrator loads keys correctly**

```bash
docker exec hermes-gateway python3 -c "
import sys; sys.path.insert(0, '/opt/orchestrator')
from orchestrator import _load_agent_api_keys
keys = _load_agent_api_keys()
print(f'Loaded {len(keys)} API keys: {[k[:8] for k in keys.keys()]}')
"
```

---

### Task 5: End-to-end verification

- [ ] **Step 1: Restart hermes-gateway (source changed)**

```bash
docker restart hermes-gateway
```

Wait 2 minutes for orchestrator to provision agents.

- [ ] **Step 2: Verify agent config uses permanent key**

```bash
docker exec hermes-gateway cat /root/.hermes/profiles/c7826470-3b08-49ad-b1d9-e73911ed64f9/config.yaml | grep "X-Paperclip-Api-Key"
```

Expected: `X-Paperclip-Api-Key: "pcp_..."` (NOT a JWT starting with `eyJ`)

- [ ] **Step 3: Verify X-Paperclip-Run-ID header present**

```bash
docker exec hermes-gateway cat /root/.hermes/profiles/c7826470-3b08-49ad-b1d9-e73911ed64f9/config.yaml | grep "X-Paperclip-Run-ID"
```

- [ ] **Step 4: Trigger a heartbeat run and verify no 401**

Wait for next heartbeat (or trigger manually), then check paperclip-server logs:

```bash
docker logs paperclip-server --since 5m 2>&1 | grep "401\|agent_key"
```

Expected: No 401 errors. agent_key auth source used.

- [ ] **Step 5: Verify checklist tool works**

Check if the agent can successfully call `paperclip_set_checklist`:

```bash
docker logs paperclip-mcp --since 5m 2>&1 | tail -20
```

Expected: 200 OK responses, no 401 errors.

---

## Rollback

If something breaks:
1. Revert `api_server.py` patch — restore JWT overwrite behavior
2. `docker exec hermes-gateway cp /opt/hermes-agent-build/gateway/platforms/api_server.py /usr/local/lib/python3.11/site-packages/gateway/platforms/api_server.py`
3. Restart: `docker restart hermes-gateway`
