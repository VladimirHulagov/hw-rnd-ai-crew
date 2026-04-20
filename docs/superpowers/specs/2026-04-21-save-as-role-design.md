# Save as Role — Design Spec

## Summary

Add a "Save as role" button to the agent instructions editor (PromptsTab) that creates a new CompanyRole from the current file content. Uses the existing `POST /companies/:id/roles` API — no backend changes required.

## Requirements

- User can save the current instructions file (AGENTS.md or any bundle file) as a new CompanyRole
- Dialog collects: name (required), description (optional), category (optional)
- After save, role appears in the company roles catalog (`/roles`)
- No git push — out of scope (manual git CLI)

## Changes

### UI only — `paperclip/ui/src/pages/AgentDetail.tsx`

Add to `PromptsTab`:

1. **"Save as role" button** in the floating action bar (alongside Save/Cancel)
   - Visible when editing any bundle file
   - If dirty: sends draft content; if not dirty: sends current file content

2. **SaveAsRoleDialog** — modal with:
   - Name input (required)
   - Description textarea (optional)
   - Category input (optional)
   - Cancel / Create buttons
   - Calls `companyRolesApi.create(companyId, { name, description, category, markdown })`
   - On success: close dialog, show toast notification

### API client — `paperclip/ui/src/api/roles.ts`

Verify `companyRolesApi.create()` exists and accepts `{ name, markdown, description?, category? }`.

## No changes to

- Backend (API exists)
- Shared types/validators (schema exists)
- CompanyRoles page
- Database
