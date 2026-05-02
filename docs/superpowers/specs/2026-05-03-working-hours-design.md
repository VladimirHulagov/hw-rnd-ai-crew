# Working Hours for Agents

## Summary

Add configurable working hours that suppress heartbeat triggers outside the configured window. Default is set at instance level; each agent can override with its own schedule. Wake-on-demand, manual triggers, and Telegram messages are unaffected.

## Data Model

### Types

```typescript
type DayOfWeek = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";

interface WorkingHours {
  enabled: boolean;   // default: false
  start: string;      // "HH:MM", default: "09:00"
  end: string;        // "HH:MM", default: "18:00"
  days: DayOfWeek[];  // default: ["mon","tue","wed","thu","fri"]
}
```

### Storage (two levels)

1. **Instance default** — new `workingHours` jsonb column on `instance_settings` table. Applies to all agents without an override.
2. **Agent override** — `runtimeConfig.heartbeat.workingHours` on `agents` table. Takes priority over instance default when set and `enabled: true`.

### Resolution

```
effective = agent.runtimeConfig.heartbeat.workingHours
            ?? instanceSettings.workingHours
            ?? { enabled: false }
```

When `enabled: false`, heartbeat runs 24/7 (current behavior). Empty `days` array with `enabled: true` means no working days — heartbeat is suppressed entirely (agent is effectively off-duty).

### Timezone

Taken from existing `instance_settings.general.timezone` (default "UTC"). No per-agent timezone.

## Heartbeat Enforcement

### Insertion point

`tickTimers()` in `heartbeat.ts`, after `parseHeartbeatPolicy(agent)` and before `enqueueWakeup`.

### Logic

```typescript
function isWithinWorkingHours(now: Date, wh: WorkingHours, timezone: string): boolean {
  if (!wh.enabled) return true;
  const localNow = toZonedTime(now, timezone);
  const dayKey = ["sun","mon","tue","wed","thu","fri","sat"][localNow.getDay()];
  if (!wh.days.includes(dayKey)) return false;
  const minutes = localNow.getHours() * 60 + localNow.getMinutes();
  const [startH, startM] = wh.start.split(":").map(Number);
  const [endH, endM] = wh.end.split(":").map(Number);
  return minutes >= startH * 60 + startM && minutes < endH * 60 + endM;
}
```

In `tickTimers`: load instance settings once per tick cycle (cache `workingHours` + `timezone`), pass to `parseHeartbeatPolicy`. If `isWithinWorkingHours` returns `false`, `continue` (skip heartbeat for that agent).

Wake-on-demand, manual triggers, and Telegram messages are completely unaffected.

## API

### Instance level

- `GET /api/instance/settings/working-hours` — returns current working hours config
- `PATCH /api/instance/settings/working-hours` — partial update, follows existing instance-settings route pattern

### Agent level

Uses existing agent update endpoint. Working hours are part of `runtimeConfig.heartbeat.workingHours` in the agent's config form.

## UI

### Instance Settings — "Working Hours" section

New section in `InstanceGeneralSettings.tsx`, placed after "Regional":

- Toggle: "Enable working hours" (`enabled`)
- Time inputs: Start / End (HTML `type="time"`, `HH:MM`)
- Day checkboxes: Mon-Sun (default Mon-Fri checked)
- Quick presets: "Mon-Fri 9-18", "Mon-Fri 10-19"
- Hint text: timezone is taken from Regional settings above
- Save via `instanceSettingsApi.updateWorkingHours()`

### Agent Config — "Run Policy" section in AgentConfigForm.tsx

Inside existing heartbeat block, after "Heartbeat on interval":

- Toggle: "Custom working hours" (`enabled`) — default off = inherits instance
- When enabled: same Start/End + Days inputs as instance
- Label: "Override instance working hours"
- Hint: "When off, inherits from Instance Settings"
- Data written to `runtimeConfig.heartbeat.workingHours`

## Files to Change

| Layer | File | Change |
|-------|------|--------|
| DB schema | `packages/db/src/schema/instance_settings.ts` | Add `workingHours` jsonb column |
| DB migration | `packages/db/src/migrations/0053_instance_working_hours.sql` | ALTER TABLE |
| Shared types | `packages/shared/src/types/instance.ts` | Add `WorkingHours`, `DayOfWeek`, `InstanceWorkingHoursSettings` |
| Shared validators | `packages/shared/src/validators/instance.ts` | Add Zod schemas |
| Shared exports | `packages/shared/src/types/index.ts`, `validators/index.ts` | Re-exports |
| Server service | `server/src/services/instance-settings.ts` | Add `normalizeWorkingHoursSettings`, `getWorkingHours`, `updateWorkingHours` |
| Server routes | `server/src/routes/instance-settings.ts` | Add GET/PATCH `/working-hours` |
| Heartbeat | `server/src/services/heartbeat.ts` | Add `isWithinWorkingHours`, extend `parseHeartbeatPolicy`, check in `tickTimers` |
| UI API | `ui/src/api/instanceSettings.ts` | Add `getWorkingHours`, `updateWorkingHours` |
| UI Instance | `ui/src/pages/InstanceGeneralSettings.tsx` | Add Working Hours section |
| UI Agent | `ui/src/components/AgentConfigForm.tsx` | Add Working Hours override in Run Policy |
