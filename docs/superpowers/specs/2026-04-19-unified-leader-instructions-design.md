# Unified Leader Instructions

## Problem

CEO agent instructions in `paperclip/server/src/onboarding-assets/ceo/` contain hardcoded routing rules (CTO, CMO, UXDesigner), startup-specific language (P&L, runway, pipeline), and English text. This makes them non-reusable across different team compositions and use cases.

## Solution

Rewrite the CEO onboarding bundle (`ceo/` directory) as a universal Russian-language leader template with dynamic delegation based on the agent's actual team structure, not hardcoded roles.

## Scope

### Files to modify

1. **`paperclip/server/src/onboarding-assets/ceo/AGENTS.md`** — complete rewrite
2. **`paperclip/server/src/onboarding-assets/ceo/SOUL.md`** — complete rewrite
3. **`paperclip/server/src/onboarding-assets/ceo/HEARTBEAT.md`** — complete rewrite
4. **`paperclip/server/src/onboarding-assets/ceo/TOOLS.md`** — keep as placeholder
5. **`hermes-gateway/orchestrator/orchestrator.py`** — update `_build_soul_md()` to match new Russian content

### Files NOT modified

- `paperclip/server/src/onboarding-assets/default/AGENTS.md` — remains the simple 3-line template for non-leader agents
- `paperclip/server/src/services/default-agent-instructions.ts` — no code changes, bundle loading mechanism stays the same
- `paperclip/server/src/routes/agents.ts` — no code changes, `materializeDefaultInstructionsBundleForNewAgent()` continues to work as-is

## Design

### AGENTS.md — Universal Leader

Key principles:
- Role: "руководящий агент" (leader agent), not "CEO"
- Delegation: dynamic — inspect direct reports via API, route based on their actual roles/skills
- No hardcoded role names or routing table
- Memory system: keep `para-memory-files` skill reference
- Safety: keep exfiltration/destruction rules
- References: keep $AGENT_HOME structure

Routing logic becomes:
1. Get your direct reports via `GET /api/agents` (filter by `reportsTo`)
2. Match task to the report whose role/expertise is closest
3. If no suitable report exists, hire one using `paperclip-create-agent` skill
4. If unclear, break into subtasks and distribute

### SOUL.md — Universal Manager Persona

Key principles:
- Strategic posture without startup jargon
- Universal management principles (clarity, delegation, candor, focus)
- Direct communication style
- Russian language throughout
- No "P&L", "runway", "pipeline" — replace with universal equivalents

### HEARTBEAT.md — Universal Heartbeat

Key principles:
- Same checklist structure (identity, planning, assignments, checkout, delegation, exit)
- Remove CEO-specific items (budget awareness above 80%)
- Keep Paperclip API integration
- Add: "inspect team" step to discover direct reports
- Russian language

### TOOLS.md

Keep as empty placeholder: "Инструменты будут добавлены по мере работы."

### _build_soul_md() update

Update the fallback function in orchestrator.py to generate Russian text consistent with the new onboarding-assets, so agents without a managed bundle still get coherent instructions.

## Implementation Steps

1. Rewrite `onboarding-assets/ceo/AGENTS.md`
2. Rewrite `onboarding-assets/ceo/SOUL.md`
3. Rewrite `onboarding-assets/ceo/HEARTBEAT.md`
4. Rewrite `onboarding-assets/ceo/TOOLS.md` (minor)
5. Update `_build_soul_md()` in `orchestrator.py`
6. Rebuild paperclip-server Docker image and restart
7. Verify: create a new CEO agent and confirm the new instructions are materialized
