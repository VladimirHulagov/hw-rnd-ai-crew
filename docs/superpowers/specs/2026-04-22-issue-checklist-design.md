# Issue Checklist

## Summary

Add a checklist (ordered list of items with done/todo status) to each issue. Agents create and update checklists via MCP tool after analyzing an issue. Users view the checklist (read-only) in the right-side Properties panel, after the "Updated" row, separated by a divider.

## Requirements

- Checklist is an ordered list of text items, each with a `done` boolean
- Only agents create and update checklists (via MCP tool)
- Only agents mark items as done (via MCP tool)
- Users view checklists in the Properties panel (read-only, checkboxes not clickable)
- If no checklist exists (null), the section is hidden
- Maximum 20 items per checklist, item text up to 200 characters

## Data Model

### Storage: JSONB column on `issues` table

New column in `paperclip/packages/db/src/schema/issues.ts`:

```typescript
checklist: jsonb('checklist').$type<{ text: string; done: boolean }[] | null>()
```

- Default: `null` (no checklist)
- Non-null: array of `{ text: string, done: boolean }` objects
- Order of items in the array = display order

### TypeScript type

Add to `Issue` interface in `paperclip/packages/shared/src/types/issue.ts`:

```typescript
checklist?: IssueChecklistItem[] | null;

// New type
interface IssueChecklistItem {
  text: string;
  done: boolean;
}
```

### Validation

Add Zod schema in `paperclip/packages/shared/src/validators/issue.ts`:

```typescript
const issueChecklistItemSchema = z.object({
  text: z.string().max(200),
  done: z.boolean(),
});

const issueChecklistSchema = z.array(issueChecklistItemSchema).max(20).nullable();
```

## MCP Tool

### `paperclip_set_checklist`

New tool in the MCP server for agents to set/update an issue's checklist.

```python
# Parameters:
issueId: str       # required - issue UUID
items: list[dict]  # required - [{"text": "...", "done": false}, ...]
                   # empty list or null = remove checklist
```

Behavior:
- Replaces the entire checklist (full replacement, not incremental)
- Empty list or null sets `checklist` to null (removes it)
- Agent must have access to the issue (same authorization as `update_issue`)
- Returns the updated issue

### Agent instructions

Add to agent prompt template: "After analyzing an issue, call `paperclip_set_checklist` with the steps you plan to take. Update the checklist as you complete each step by calling `paperclip_set_checklist` with updated done statuses."

## Server API

No new endpoint needed. The existing `PATCH /issues/:id` endpoint is extended to accept `checklist` in the request body.

- Field: `checklist` in the update payload
- Validation: Zod schema (max 20 items, text max 200 chars)
- The field is included in the issue response automatically (already part of the issue record)

## UI

### Properties panel display

In `paperclip/ui/src/components/IssueProperties.tsx`:

After the "Updated" row (line ~616), add:

1. A `<Separator />` (divider line)
2. A "Checklist" section with:
   - Header row: "Checklist" label + progress indicator (e.g., "2/4") in gray text
   - For each item: a read-only checkbox icon + text
     - Done items: checked checkbox icon + text with `line-through` styling
     - Todo items: empty checkbox icon + normal text
- Section is hidden when `issue.checklist` is null or empty

### Styling

- Checkboxes: small square icons (not interactive), gray for todo, colored for done
- Text: done items get `text-decoration: line-through` + muted color
- Progress indicator: small gray text (e.g., "2 of 4") next to "Checklist" label
- Consistent with existing PropertyRow spacing and font sizes

## Affected files

### Database
- `paperclip/packages/db/src/schema/issues.ts` — add `checklist` column

### Shared types
- `paperclip/packages/shared/src/types/issue.ts` — add `IssueChecklistItem` type, `checklist` field
- `paperclip/packages/shared/src/validators/issue.ts` — add checklist Zod schemas

### Server
- `paperclip/server/src/services/issues.ts` — include `checklist` in update logic
- Drizzle migration file (auto-generated)

### MCP
- `paperclip-mcp/` — add `paperclip_set_checklist` tool definition and implementation

### UI
- `paperclip/ui/src/components/IssueProperties.tsx` — display checklist section
