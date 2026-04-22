# Issue Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a checklist (ordered list of done/todo items) to issues, set by agents via MCP, displayed read-only in the Properties panel.

**Architecture:** JSONB column on `issues` table. Agent sets full checklist via new MCP tool `paperclip_set_checklist`. Server passes `checklist` through the existing PATCH route. UI renders read-only checkbox list in IssueProperties after "Updated" row.

**Tech Stack:** Drizzle ORM (schema + migration), Zod (validation), React (UI), Python MCP server (tool)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `paperclip/packages/db/src/schema/issues.ts` | Add `checklist` jsonb column |
| Create | `paperclip/packages/db/src/migrations/0052_issue_checklist.sql` | ALTER TABLE migration |
| Modify | `paperclip/packages/db/src/migrations/meta/_journal.json` | Register migration |
| Modify | `paperclip/packages/shared/src/types/issue.ts` | Add `IssueChecklistItem` type + `checklist` field |
| Modify | `paperclip/packages/shared/src/validators/issue.ts` | Add checklist Zod schema, add `checklist` to update schema |
| Modify | `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py` | Add `set_checklist` function |
| Modify | `paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py` | Add tool definition + dispatch |
| Modify | `paperclip/ui/src/components/IssueProperties.tsx` | Render checklist section |

---

### Task 1: Database schema + migration

**Files:**
- Modify: `paperclip/packages/db/src/schema/issues.ts:49` (after `assigneeAdapterOverrides`)
- Create: `paperclip/packages/db/src/migrations/0052_issue_checklist.sql`
- Modify: `paperclip/packages/db/src/migrations/meta/_journal.json`

- [ ] **Step 1: Add `checklist` column to Drizzle schema**

In `paperclip/packages/db/src/schema/issues.ts`, after line 49 (`assigneeAdapterOverrides`), add:

```typescript
    checklist: jsonb("checklist").$type<{ text: string; done: boolean }[] | null>(),
```

- [ ] **Step 2: Create SQL migration**

Create `paperclip/packages/db/src/migrations/0052_issue_checklist.sql`:

```sql
ALTER TABLE "issues" ADD COLUMN "checklist" jsonb;
```

- [ ] **Step 3: Register migration in journal**

Append to the `"entries"` array in `paperclip/packages/db/src/migrations/meta/_journal.json`:

```json
    {
      "idx": 52,
      "version": "7",
      "when": 1745308800000,
      "tag": "0052_issue_checklist",
      "breakpoints": true
    }
```

- [ ] **Step 4: Verify DB package compiles**

Run: `docker exec paperclip-server node -e "require('./node_modules/typescript/bin/tsc')" 2>/dev/null || true`

Or simpler — check for TypeScript errors in schema file.

- [ ] **Step 5: Apply migration to running DB**

Run: `docker exec paperclip-db psql -U paperclip -d paperclip -c "ALTER TABLE issues ADD COLUMN IF NOT EXISTS checklist jsonb;"`

- [ ] **Step 6: Commit**

```bash
git add paperclip/packages/db/src/schema/issues.ts paperclip/packages/db/src/migrations/0052_issue_checklist.sql paperclip/packages/db/src/migrations/meta/_journal.json
git commit -m "feat(db): add checklist jsonb column to issues table"
```

---

### Task 2: Shared types + validators

**Files:**
- Modify: `paperclip/packages/shared/src/types/issue.ts`
- Modify: `paperclip/packages/shared/src/validators/issue.ts`

- [ ] **Step 1: Add `IssueChecklistItem` type and `checklist` field to Issue**

In `paperclip/packages/shared/src/types/issue.ts`, add before the `Issue` interface (after line 97):

```typescript
export interface IssueChecklistItem {
  text: string;
  done: boolean;
}
```

Inside the `Issue` interface, add after line 133 (`hiddenAt`):

```typescript
  checklist?: IssueChecklistItem[] | null;
```

- [ ] **Step 2: Add Zod schemas for checklist**

In `paperclip/packages/shared/src/validators/issue.ts`, add after line 1 (`import { z } from "zod"`):

```typescript
export const issueChecklistItemSchema = z.object({
  text: z.string().max(200),
  done: z.boolean(),
});

export const issueChecklistSchema = z.array(issueChecklistItemSchema).max(20).nullable();
```

- [ ] **Step 3: Add `checklist` to `updateIssueSchema`**

In `paperclip/packages/shared/src/validators/issue.ts`, modify the `updateIssueSchema` (line 69). Add `checklist` to the schema:

```typescript
export const updateIssueSchema = createIssueSchema.partial().extend({
  comment: z.string().min(1).optional(),
  reopen: z.boolean().optional(),
  interrupt: z.boolean().optional(),
  hiddenAt: z.string().datetime().nullable().optional(),
  checklist: issueChecklistSchema.optional(),
});
```

- [ ] **Step 4: Verify shared package typechecks**

The changes should be type-compatible. `checklist` field in `Issue` matches the Zod schema shape.

- [ ] **Step 5: Commit**

```bash
git add paperclip/packages/shared/src/types/issue.ts paperclip/packages/shared/src/validators/issue.ts
git commit -m "feat(shared): add IssueChecklistItem type and checklist validators"
```

---

### Task 3: MCP tool `paperclip_set_checklist`

**Files:**
- Modify: `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py`
- Modify: `paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py`

- [ ] **Step 1: Add `set_checklist` function in tools.py**

In `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py`, after the `update_issue` function (after line 131), add:

```python
async def set_checklist(
    issueId: str,
    items: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    body: Dict[str, Any] = {}
    if items is None or len(items) == 0:
        body["checklist"] = None
    else:
        checklist = []
        for item in items:
            checklist.append({
                "text": str(item.get("text", ""))[:200],
                "done": bool(item.get("done", False)),
            })
        body["checklist"] = checklist[:20]
    return await _request("PATCH", f"/issues/{issueId}", json_body=body)
```

- [ ] **Step 2: Import `set_checklist` in main.py**

In `paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py`, modify the import block (around line 15) to include:

```python
    set_checklist,
```

Add it to the existing import from `tools` module.

- [ ] **Step 3: Add tool definition in main.py**

In `paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py`, add a new `types.Tool` entry in the `list_tools` handler (after the `paperclip_release_issue` tool definition, around line 163). Add before the closing `]` of the tools list:

```python
        types.Tool(
            name="paperclip_set_checklist",
            description="Set the checklist (todo steps) for an issue. Replaces the entire checklist. Each item has 'text' (max 200 chars) and 'done' (boolean). Pass empty items to remove the checklist.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issueId": {"type": "string", "description": "Issue UUID or identifier"},
                    "items": {
                        "type": "array",
                        "description": "Checklist items. Each item: {\"text\": \"...\", \"done\": true/false}. Max 20 items.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "Item text (max 200 chars)"},
                                "done": {"type": "boolean", "description": "Whether the item is completed"},
                            },
                            "required": ["text", "done"],
                        },
                    },
                },
                "required": ["issueId"],
            },
        ),
```

- [ ] **Step 4: Add dispatch case in main.py**

In the `_dispatch` function (after the `paperclip_release_issue` case, around line 392), add:

```python
    elif name == "paperclip_set_checklist":
        return await set_checklist(
            issueId=args["issueId"],
            items=args.get("items"),
        )
```

- [ ] **Step 5: Verify Python syntax**

Run: `python3 -c "import ast; ast.parse(open('paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py').read()); ast.parse(open('paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py').read()); print('OK')"`

- [ ] **Step 6: Commit**

```bash
git add paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py
git commit -m "feat(mcp): add paperclip_set_checklist tool"
```

---

### Task 4: UI — Checklist in Properties panel

**Files:**
- Modify: `paperclip/ui/src/components/IssueProperties.tsx`

- [ ] **Step 1: Add lucide icons import**

In `paperclip/ui/src/components/IssueProperties.tsx`, modify line 22 to add `CheckSquare`, `Square` to the lucide imports:

```typescript
import { User, Hexagon, ArrowUpRight, Tag, Plus, Trash2, CheckSquare, Square } from "lucide-react";
```

- [ ] **Step 2: Add checklist section after "Updated" row**

In `paperclip/ui/src/components/IssueProperties.tsx`, after line 616 (the "Updated" `PropertyRow`) and before line 617 (`</div>`), add:

```tsx
      {issue.checklist && issue.checklist.length > 0 && (
        <>
          <Separator />
          <div className="space-y-1">
            <div className="flex items-center justify-between py-1.5">
              <span className="text-xs text-muted-foreground">Checklist</span>
              <span className="text-xs text-muted-foreground">
                {issue.checklist.filter((i) => i.done).length}/{issue.checklist.length}
              </span>
            </div>
            <div className="space-y-0.5">
              {issue.checklist.map((item, idx) => (
                <div key={idx} className="flex items-start gap-1.5 py-0.5">
                  {item.done ? (
                    <CheckSquare className="h-3.5 w-3.5 shrink-0 mt-0.5 text-muted-foreground" />
                  ) : (
                    <Square className="h-3.5 w-3.5 shrink-0 mt-0.5 text-muted-foreground" />
                  )}
                  <span
                    className={cn(
                      "text-xs leading-snug",
                      item.done && "line-through text-muted-foreground",
                    )}
                  >
                    {item.text}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
```

This goes inside the outermost `<div className="space-y-4">` of the component, right after the closing `</div>` of the metadata section (line 617) and before the final `</div>` (line 618).

- [ ] **Step 3: Verify UI compiles**

Since UI is built inside the container, verify the file syntax is correct by checking for obvious issues.

- [ ] **Step 4: Commit**

```bash
git add paperclip/ui/src/components/IssueProperties.tsx
git commit -m "feat(ui): display issue checklist in Properties panel"
```

---

### Task 5: Build and deploy

**Files:** None (build/deploy steps only)

- [ ] **Step 1: Build shared package in container**

Run: `docker exec -w /app paperclip-server npx tsc -p packages/shared/tsconfig.json`

- [ ] **Step 2: Build UI in container**

Run: `docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build`

- [ ] **Step 3: Restart paperclip-server**

Run: `docker compose restart paperclip-server`

- [ ] **Step 4: Verify API returns checklist field**

Run: `curl -s http://localhost:3100/api/issues/<SOME_ID> -H "Authorization: Bearer <TOKEN>" | python3 -m json.tool | grep checklist`

- [ ] **Step 5: Commit any build artifacts if needed**

Only if shared package dist changed.
