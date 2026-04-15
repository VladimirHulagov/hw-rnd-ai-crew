# Timezone & 24h Time Format

Date: 2026-04-14

## Problem

Paperclip UI displays timestamps inconsistently:
- `formatDateTime()` uses `en-US` locale (12h AM/PM format)
- Log timestamps in `AgentDetail.tsx` use hardcoded `hour12: false` (24h)
- Many inline calls use `toLocaleString()` with no options (browser locale dependent)
- No timezone configuration exists — all times shown in browser's local timezone
- No way to switch between 12h and 24h

## Scope

Add instance-level timezone and time format (12h/24h) settings. Apply consistently across the UI.

## Data Model

### InstanceGeneralSettings additions

Two new fields in the existing JSON `general` column of `instance_settings` table:

```
timezone: string     // IANA timezone identifier, e.g. "Europe/Moscow", "UTC"
timeFormat: "12h" | "24h"
```

**Defaults:** `timezone = "UTC"`, `timeFormat = "24h"`.

No database migration required — these are stored in the existing JSON column.

### Files to change

**packages/shared:**
- `src/types/instance.ts` — add `timezone` and `timeFormat` to `InstanceGeneralSettings` interface
- `src/validators/instance.ts` — add `z.string()` and `z.enum(["12h", "24h"])` to `instanceGeneralSettingsSchema`

**server:**
- `src/services/instance-settings.ts` — update `normalizeGeneralSettings()` to include defaults for new fields

**ui:**
- `src/pages/InstanceGeneralSettings.tsx` — add "Regional" section with timezone select and time format toggle
- `src/lib/utils.ts` — update `formatDateTime()` and `formatDate()` to accept optional `timezone` and `timeFormat` params
- `src/hooks/useTimeSettings.ts` (new) — hook that reads instance general settings from react-query cache and returns `{ timezone, timeFormat, formatDateTime, formatDate, formatTime }`

## UI — InstanceGeneralSettings.tsx

Add a "Regional" section with:

1. **Timezone** — `<select>` dropdown:
   - Auto-detect browser timezone via `Intl.DateTimeFormat().resolvedOptions().timeZone`
   - Show common timezones at top (UTC, US/Eastern, US/Pacific, Europe/London, Europe/Moscow, Asia/Tokyo, etc.)
   - Full IANA list below, grouped by region
   - Save via existing `updateGeneralMutation` with `{ timezone: value }`

2. **Time format** — toggle or radio buttons:
   - `12h` (e.g. "3:42 PM")
   - `24h` (e.g. "15:42")
   - Save via existing `updateGeneralMutation` with `{ timeFormat: value }`

## Consuming Hook — useTimeSettings()

```ts
interface TimeSettings {
  timezone: string;      // IANA timezone, default "UTC"
  timeFormat: "12h" | "24h";  // default "24h"
  formatDateTime: (date: Date | string) => string;
  formatDate: (date: Date | string) => string;
  formatTime: (date: Date | string) => string;
}
```

- Reads `instanceSettings.general` from react-query cache
- Falls back to `{ timezone: "UTC", timeFormat: "24h" }` when settings not loaded
- `formatDateTime`, `formatDate`, `formatTime` use `toLocaleString()` with `timeZone` and `hour12` options

## Formatting Functions

Update `formatDateTime()` in `utils.ts` to accept options:

```ts
formatDateTime(date, options?: { timezone?: string; timeFormat?: "12h" | "24h" })
```

When options provided:
- `timeZone: options.timezone`
- `hour12: options.timeFormat === "12h"`

When no options: current behavior unchanged.

## Inline Call Sites (future cleanup, not blocking)

These currently call `toLocaleString()` directly and should migrate to `useTimeSettings()` or the updated `formatDateTime()` over time:
- `pages/Routines.tsx` — `toLocaleString()`
- `pages/ProjectWorkspaceDetail.tsx` — `toLocaleString()`
- `pages/ApprovalDetail.tsx` — `toLocaleString()`
- `pages/PluginSettings.tsx` — `toLocaleString()`, `toLocaleTimeString()`
- `pages/CompanySettings.tsx` — `toLocaleString()`
- `components/ClaudeSubscriptionPanel.tsx` — `toLocaleString(undefined, { timeZoneName: "short" })`
- `components/CodexSubscriptionPanel.tsx` — same pattern

These are not blockers. The new settings will be applied when these sites are migrated.

## Not In Scope

- Per-user timezone preferences
- Date format customization (DD/MM/YYYY vs MM/DD/YYYY etc.)
- Changing `relativeTime()` / `timeAgo()` behavior
- ActivityCharts day bucketing (uses ISO UTC dates intentionally)
