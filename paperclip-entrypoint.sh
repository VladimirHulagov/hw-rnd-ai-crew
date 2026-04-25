#!/bin/bash

HERMES_SRC="/opt/hermes-agent"
HERMES_BUILD="/opt/hermes-agent-build"
HERMES_INSTANCES="/paperclip/hermes-instances"
HERMES_SHARED_CONFIG="/opt/hermes-shared-config"

mkdir -p "$HERMES_INSTANCES"

if ! command -v hermes &>/dev/null; then
    echo "[entrypoint] Installing hermes-agent..."

    if [ ! -f "$HERMES_BUILD/pyproject.toml" ]; then
        echo "[entrypoint] Copying source to build directory..."
        cp -a "$HERMES_SRC"/* "$HERMES_BUILD"/ 2>/dev/null
    fi

    /paperclip/.local/bin/pip install --break-system-packages "$HERMES_BUILD" 2>&1

    if command -v hermes &>/dev/null; then
        echo "[entrypoint] Done."
    else
        echo "[entrypoint] WARNING: hermes still not found after install"
    fi
fi

if ! python3 -c "import mcp" 2>/dev/null; then
    echo "[entrypoint] Installing mcp package for MCP tool support..."
    python3 -m pip install --break-system-packages mcp httpx 2>&1
fi

# Ensure all existing agent instances have the shared config
if [ -d "$HERMES_SHARED_CONFIG" ]; then
    for instance_dir in "$HERMES_INSTANCES"/*/; do
        [ -d "$instance_dir" ] || continue
        if [ ! -f "$instance_dir/config.yaml" ] || [ "$HERMES_SHARED_CONFIG/config.yaml" -nt "$instance_dir/config.yaml" ]; then
            cp "$HERMES_SHARED_CONFIG/config.yaml" "$instance_dir/config.yaml"
        fi
    done
fi

# Patch: clear checkoutRunId on run finalization (heartbeat.js)
# releaseIssueExecutionAndPromote clears executionRunId but NOT checkoutRunId.
# When a heartbeat run finishes, the next run gets 409 because checkoutRunId
# still points to the old (completed) run. Fix: add checkoutRunId: null cleanup.
HB_JS="/app/server/dist/services/heartbeat.js"
if [ -f "$HB_JS" ] && ! grep -q "checkoutRunId: null" "$HB_JS" 2>/dev/null; then
    echo "[entrypoint] Patching heartbeat.js: adding checkoutRunId cleanup..."
    sed -i '/executionAgentNameKey: null,/{
        s/executionAgentNameKey: null,/checkoutRunId: null,\n                executionAgentNameKey: null,/
        t
        s/executionAgentNameKey: null,/checkoutRunId: null,\n                        executionAgentNameKey: null,/
    }' "$HB_JS"
    echo "[entrypoint] heartbeat.js patched."
fi

# Patch: stale actorRunId defense (auth.js + issues.js)
# When hermes MCP sends a stale X-Paperclip-Run-ID (reaped heartbeat run),
# paperclip-server 409s because the run no longer exists. Three fixes:
#   1. auth.js: clear runId if run doesn't exist (agent_key path), like JWT path
#   2. issues.js service: adopt stale checkoutRunId when actorRunId is null
#   3. issues.js route: bypass requireAgentRunId 401 when runId cleared
AUTH_JS="/app/server/dist/middleware/auth.js"
SVC_JS="/app/server/dist/services/issues.js"
ROUTE_JS="/app/server/dist/routes/issues.js"

if [ -f "$AUTH_JS" ] && ! grep -q "agent_key.*runExists\|API key run_id" "$AUTH_JS" 2>/dev/null; then
    echo "[entrypoint] Patching auth.js: stale runId cleanup for agent_key..."
    python3 -c "
import sys
with open('$AUTH_JS', 'r') as f:
    src = f.read()
marker = '''        req.actor = {
            type: \"agent\",
            agentId: key.agentId,
            companyId: key.companyId,
            keyId: key.id,
            runId: runIdHeader || undefined,
            source: \"agent_key\",
        };
        next();'''
patch = '''        req.actor = {
            type: \"agent\",
            agentId: key.agentId,
            companyId: key.companyId,
            keyId: key.id,
            runId: runIdHeader || undefined,
            source: \"agent_key\",
        };
        if (req.actor.runId) {
            const runExists = await db
                .select({ id: heartbeatRuns.id })
                .from(heartbeatRuns)
                .where(eq(heartbeatRuns.id, req.actor.runId))
                .then((rows) => rows.length > 0)
                .catch(() => false);
            if (!runExists) {
                logger.warn({ runId: req.actor.runId, agentId: req.actor.agentId }, \"API key run_id references non-existent heartbeat_run, clearing\");
                req.actor.runId = undefined;
            }
        }
        next();'''
if marker in src:
    src = src.replace(marker, patch)
    with open('$AUTH_JS', 'w') as f:
        f.write(src)
    print('  auth.js patched.')
else:
    print('  ERROR: marker not found in auth.js', file=sys.stderr)
    sys.exit(1)
"
fi

if [ -f "$SVC_JS" ] && ! grep -q "stale checkout adoption.*null actorRunId\|no actorRunId.*stale checkout" "$SVC_JS" 2>/dev/null; then
    echo "[entrypoint] Patching issues.js service: stale checkout adoption for null actorRunId..."
    python3 -c "
import sys
with open('$SVC_JS', 'r') as f:
    src = f.read()
marker = '''            throw conflict(\"Issue run ownership conflict\", {'''
patch = '''            // no actorRunId (cleared by auth middleware) but stale checkoutRunId — try adopt
            if (!actorRunId &&
                current.status === \"in_progress\" &&
                current.assigneeAgentId === actorAgentId &&
                current.checkoutRunId) {
                const adopted = await adoptStaleCheckoutRun({
                    issueId: id,
                    actorAgentId,
                    actorRunId: null,
                    expectedCheckoutRunId: current.checkoutRunId,
                });
                if (adopted) {
                    return {
                        ...adopted,
                        adoptedFromRunId: current.checkoutRunId,
                    };
                }
            }
            throw conflict(\"Issue run ownership conflict\", {'''
if marker in src:
    src = src.replace(marker, patch)
    with open('$SVC_JS', 'w') as f:
        f.write(src)
    print('  issues.js service patched.')
else:
    print('  ERROR: marker not found in issues.js service', file=sys.stderr)
    sys.exit(1)
"
fi

if [ -f "$ROUTE_JS" ] && ! grep -q "stale runId.*allow assignee\|runId cleared.*stale" "$ROUTE_JS" 2>/dev/null; then
    echo "[entrypoint] Patching issues.js route: bypass requireAgentRunId for stale runId..."
    python3 -c "
import sys
with open('$ROUTE_JS', 'r') as f:
    src = f.read()
marker = '''        const runId = requireAgentRunId(req, res);
        if (!runId)
            return false;
        const ownership = await svc.assertCheckoutOwner(issue.id, actorAgentId, runId);'''
patch = '''        const runId = req.actor.runId?.trim() || null;
        // runId cleared by auth middleware (stale/reaped run) — agent is authenticated via permanent key
        // assertCheckoutOwner handles null actorRunId with stale checkout adoption
        if (!runId && !issue.checkoutRunId)
            return true;
        if (!runId) {
            try {
                await svc.assertCheckoutOwner(issue.id, actorAgentId, null);
                return true;
            } catch {
                return false;
            }
        }
        const ownership = await svc.assertCheckoutOwner(issue.id, actorAgentId, runId);'''
if marker in src:
    src = src.replace(marker, patch)
    with open('$ROUTE_JS', 'w') as f:
        f.write(src)
    print('  issues.js route patched.')
else:
    print('  ERROR: marker not found in issues.js route', file=sys.stderr)
    sys.exit(1)
"
fi

exec "$@"
