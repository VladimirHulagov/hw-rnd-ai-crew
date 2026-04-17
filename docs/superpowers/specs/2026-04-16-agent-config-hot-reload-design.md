# Agent Config Hot-Reload via Fingerprint

## Problem

`reconcile()` in `orchestrator.py` only calls `provision_agent()` for agents NOT in `_running_agent_ids`. Running agents are skipped entirely — `config.yaml` and `SOUL.md` are never regenerated or updated for them. Code changes to `_build_soul_md()` or `generate_profile_config()` require a full container restart to take effect.

## Solution

Track a hash fingerprint of each agent's generated config.yaml + SOUL.md content in `_known_agents`. On each reconcile cycle (every 60s), for running agents, regenerate both files, hash the result, and compare with the stored fingerprint. If changed — write new files to disk and restart the supervisor process.

## Design

### Fingerprint

```python
_known_agents: dict[str, dict]  # {"data": agent_dict, "fingerprint": "sha256hex"}
```

Fingerprint = `hashlib.sha256(config_text + soul_text).hexdigest()`.

### Reconcile flow

```
for each agent in DB:
    generate config_text and soul_text
    fingerprint = sha256(config_text + soul_text)

    if agent not in _running_agent_ids:
        provision_agent(agent)           # full provision (write + start)
        _known_agents[agent_id] = {"data": agent, "fingerprint": fingerprint}
    else:
        # running agent — check for config drift
        stored = _known_agents.get(agent_id, {}).get("fingerprint")
        if stored != fingerprint:
            write config.yaml + SOUL.md to disk
            restart supervisor process
            update _known_agents[agent_id]["fingerprint"]
```

### Files changed

- `hermes-gateway/orchestrator/orchestrator.py` — `_known_agents` structure, `reconcile()`, new `_restart_agent()` helper

## Edge cases

- Agent process in STOPPING/BACKOFF state: treat as running, still restart (supervisor restart handles this)
- First reconcile after container restart: `_known_agents` is empty, all agents get full provision (existing behavior preserved)
- Config generation fails: log error, skip this agent, keep old fingerprint

## Deployment

```bash
docker compose up -d --force-recreate --build hermes-gateway
```
