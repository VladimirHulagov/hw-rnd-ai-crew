import { Router } from "express";
import { execFile } from "child_process";
import { sql } from "drizzle-orm";
import { companySkillCreateSchema, companySkillFileUpdateSchema, companySkillImportSchema, companySkillProjectScanRequestSchema, } from "@paperclipai/shared";
import { trackSkillImported } from "@paperclipai/shared/telemetry";
import { validate } from "../middleware/validate.js";
import { accessService, agentService, companySkillService, logActivity } from "../services/index.js";
import { forbidden } from "../errors.js";
import { assertCompanyAccess, getActorInfo } from "./authz.js";
import { getTelemetryClient } from "../telemetry.js";
function companySkillRoutes(db) {
  const router = Router();
  const agents = agentService(db);
  const access = accessService(db);
  const svc = companySkillService(db);
  function canCreateAgents(agent) {
    if (!agent.permissions || typeof agent.permissions !== "object")
      return false;
    return Boolean(agent.permissions.canCreateAgents);
  }
  function asString(value) {
    if (typeof value !== "string")
      return null;
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  function deriveTrackedSkillRef(skill) {
    if (skill.sourceType === "skills_sh") {
      return skill.key;
    }
    if (skill.sourceType !== "github") {
      return null;
    }
    const hostname = asString(skill.metadata?.hostname);
    if (hostname !== "github.com") {
      return null;
    }
    return skill.key;
  }
  async function assertCanMutateCompanySkills(req, companyId) {
    assertCompanyAccess(req, companyId);
    if (req.actor.type === "board") {
      if (req.actor.source === "local_implicit" || req.actor.isInstanceAdmin)
        return;
      const allowed = await access.canUser(companyId, req.actor.userId, "agents:create");
      if (!allowed) {
        throw forbidden("Missing permission: agents:create");
      }
      return;
    }
    if (!req.actor.agentId) {
      throw forbidden("Agent authentication required");
    }
    const actorAgent = await agents.getById(req.actor.agentId);
    if (!actorAgent || actorAgent.companyId !== companyId) {
      throw forbidden("Agent key cannot access another company");
    }
    const allowedByGrant = await access.hasPermission(companyId, "agent", actorAgent.id, "agents:create");
    if (allowedByGrant || canCreateAgents(actorAgent)) {
      return;
    }
    throw forbidden("Missing permission: can create agents");
  }
  async function rawQuery(query, params) {
    const result = await db.execute(sql.raw(query));
    return result.rows || result;
  }
  async function getSkillSources(companyId) {
    const rows = await rawQuery(`SELECT * FROM skill_sources WHERE company_id = '${companyId}'`);
    return rows;
  }
  async function getCompanySkillsMeta(companyId) {
    const rows = await rawQuery(`SELECT source_type, source_locator, metadata FROM company_skills WHERE company_id = '${companyId}'`);
    return rows;
  }
  router.get("/companies/:companyId/skills", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    const includeHidden = req.query.includeHidden === "true";
    const result = await svc.list(companyId, { includeHidden });
    const sources = await getSkillSources(companyId);
    if (sources.length > 0) {
      for (const skill of result) {
        const meta = skill.metadata;
        const metaKind = meta?.sourceKind;
        let matchedSource;
        if (metaKind === "paperclip_bundled") {
          matchedSource = sources.find((s) => s.source_kind === "paperclip_bundled");
        } else if (metaKind === "hermes_bundled") {
          matchedSource = sources.find((s) => s.source_kind === "hermes_bundled");
        } else if (metaKind === "agent_created") {
          const aid = meta?.authorAgentId;
          if (aid) matchedSource = sources.find((s) => s.source_kind === "agent" && s.source_locator === aid);
          if (!matchedSource) matchedSource = sources.find((s) => s.source_kind === "team");
        } else {
          const key = `${skill.sourceType}::${skill.sourceLocator ?? ""}`;
          matchedSource = sources.find((s) => `${s.source_type}::${s.source_locator ?? ""}` === key);
        }
        if (matchedSource && matchedSource.name) {
          skill.sourceLabel = matchedSource.name;
        }
        if (matchedSource) {
          skill.sourceGroup = matchedSource.source_kind;
        }
        if (meta?.authorAgentId) {
          skill.authorAgentId = meta.authorAgentId;
        }
      }
    }
    res.json(result);
  });
  router.get("/companies/:companyId/skills/:skillId", async (req, res) => {
    const companyId = req.params.companyId;
    const skillId = req.params.skillId;
    assertCompanyAccess(req, companyId);
    const result = await svc.detail(companyId, skillId);
    if (!result) {
      res.status(404).json({ error: "Skill not found" });
      return;
    }
    res.json(result);
  });
  router.get("/companies/:companyId/skills/:skillId/update-status", async (req, res) => {
    const companyId = req.params.companyId;
    const skillId = req.params.skillId;
    assertCompanyAccess(req, companyId);
    const result = await svc.updateStatus(companyId, skillId);
    if (!result) {
      res.status(404).json({ error: "Skill not found" });
      return;
    }
    res.json(result);
  });
  router.get("/companies/:companyId/skills/:skillId/files", async (req, res) => {
    const companyId = req.params.companyId;
    const skillId = req.params.skillId;
    const relativePath = String(req.query.path ?? "SKILL.md");
    assertCompanyAccess(req, companyId);
    const result = await svc.readFile(companyId, skillId, relativePath);
    if (!result) {
      res.status(404).json({ error: "Skill not found" });
      return;
    }
    res.json(result);
  });
  router.post("/companies/:companyId/skills", validate(companySkillCreateSchema), async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const result = await svc.createLocalSkill(companyId, req.body);
    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skill_created",
      entityType: "company_skill",
      entityId: result.id,
      details: {
        slug: result.slug,
        name: result.name,
      },
    });
    res.status(201).json(result);
  });
  router.patch("/companies/:companyId/skills/:skillId/files", validate(companySkillFileUpdateSchema), async (req, res) => {
    const companyId = req.params.companyId;
    const skillId = req.params.skillId;
    await assertCanMutateCompanySkills(req, companyId);
    const result = await svc.updateFile(companyId, skillId, String(req.body.path ?? ""), String(req.body.content ?? ""));
    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skill_file_updated",
      entityType: "company_skill",
      entityId: skillId,
      details: {
        path: result.path,
        markdown: result.markdown,
      },
    });
    res.json(result);
  });
  router.post("/companies/:companyId/skills/import", validate(companySkillImportSchema), async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const source = String(req.body.source ?? "");
    const result = await svc.importFromSource(companyId, source);
    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skills_imported",
      entityType: "company",
      entityId: companyId,
      details: {
        source,
        importedCount: result.imported.length,
        importedSlugs: result.imported.map((skill) => skill.slug),
        warningCount: result.warnings.length,
      },
    });
    const telemetryClient = getTelemetryClient();
    if (telemetryClient) {
      for (const skill of result.imported) {
        trackSkillImported(telemetryClient, {
          sourceType: skill.sourceType,
          skillRef: deriveTrackedSkillRef(skill),
        });
      }
    }
    res.status(201).json(result);
  });
  router.post("/companies/:companyId/skills/scan-projects", validate(companySkillProjectScanRequestSchema), async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const result = await svc.scanProjectWorkspaces(companyId, req.body);
    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skills_scanned",
      entityType: "company",
      entityId: companyId,
      details: {
        scannedProjects: result.scannedProjects,
        scannedWorkspaces: result.scannedWorkspaces,
        discovered: result.discovered,
        importedCount: result.imported.length,
        updatedCount: result.updated.length,
        conflictCount: result.conflicts.length,
        warningCount: result.warnings.length,
      },
    });
    res.json(result);
  });
  router.delete("/companies/:companyId/skills/:skillId", async (req, res) => {
    const companyId = req.params.companyId;
    const skillId = req.params.skillId;
    await assertCanMutateCompanySkills(req, companyId);
    const result = await svc.deleteSkill(companyId, skillId);
    if (!result) {
      res.status(404).json({ error: "Skill not found" });
      return;
    }
    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skill_deleted",
      entityType: "company_skill",
      entityId: result.id,
      details: {
        slug: result.slug,
        name: result.name,
      },
    });
    res.json(result);
  });
  router.post("/companies/:companyId/skills/:skillId/install-update", async (req, res) => {
    const companyId = req.params.companyId;
    const skillId = req.params.skillId;
    await assertCanMutateCompanySkills(req, companyId);
    const result = await svc.installUpdate(companyId, skillId);
    if (!result) {
      res.status(404).json({ error: "Skill not found" });
      return;
    }
    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skill_update_installed",
      entityType: "company_skill",
      entityId: result.id,
      details: {
        slug: result.slug,
        sourceRef: result.sourceRef,
      },
    });
    res.json(result);
  });
  router.patch("/companies/:companyId/skills/:skillId/visibility", async (req, res) => {
    const companyId = req.params.companyId;
    const skillId = req.params.skillId;
    await assertCanMutateCompanySkills(req, companyId);
    const hidden = Boolean(req.body.hidden);
    await db.execute(sql`UPDATE company_skills SET hidden = ${hidden}, updated_at = now() WHERE id = ${skillId} AND company_id = ${companyId}`);
    res.json({ hidden });
  });
  router.get("/companies/:companyId/hidden-sources", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    const result = await db.execute(sql`SELECT hidden_sources FROM companies WHERE id = ${companyId}`);
    const rows = result.rows ?? result;
    const val = rows[0]?.hidden_sources;
    const parsed = typeof val === 'string' ? JSON.parse(val) : (val ?? []);
    res.json(parsed);
  });
  router.put("/companies/:companyId/hidden-sources", async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const sources = req.body;
    if (!Array.isArray(sources)) {
      res.status(400).json({ error: "Expected array" });
      return;
    }
    await db.execute(sql`UPDATE companies SET hidden_sources = ${JSON.stringify(sources)} WHERE id = ${companyId}`);
    res.json(sources);
  });
  router.delete("/companies/:companyId/skills-by-source", async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const sourceType = String(req.query.sourceType ?? "");
    const sourceLocator = String(req.query.sourceLocator ?? "");
    if (!sourceType || !sourceLocator) {
      res.status(400).json({ error: "sourceType and sourceLocator query params are required" });
      return;
    }
    const result = await svc.deleteBySource(companyId, sourceType, sourceLocator);
    const actor = getActorInfo(req);
    await logActivity(db, {
      companyId,
      actorType: actor.actorType,
      actorId: actor.actorId,
      agentId: actor.agentId,
      runId: actor.runId,
      action: "company.skills_deleted_by_source",
      entityType: "company",
      entityId: companyId,
      details: { sourceType, sourceLocator, deletedCount: result.deletedCount }
    });
    res.json(result);
  });
  const TEAM_SKILLS_URL = process.env.HERMES_GATEWAY_TEAM_SKILLS_URL || "http://hermes-gateway:8681";
  const TEAM_SKILLS_KEY = process.env.TEAM_SKILLS_API_KEY || "";
  router.get("/companies/:companyId/team-skills", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills`, {
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` }
      });
      const data = await resp.json();
      const agentSources = await db.execute(sql`SELECT source_locator FROM skill_sources WHERE company_id = ${companyId} AND source_kind = 'agent'`);
      const rows = agentSources.rows ?? agentSources;
      const allowedIds = new Set(rows.map(s => s.source_locator));
      const filtered = data.filter(s => allowedIds.has(s.agentId));
      res.json(filtered);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });
  router.get("/companies/:companyId/team-skills/:agentId/:category/:skillName", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    const { agentId, category, skillName } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}`, {
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` }
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });
  router.put("/companies/:companyId/team-skills/:agentId/:category/:skillName", async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const { agentId, category, skillName } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify(req.body)
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });
  router.delete("/companies/:companyId/team-skills/:agentId/:category/:skillName", async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const { agentId, category, skillName } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` }
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });
  router.get("/companies/:companyId/team-skills/:agentId/:category/:skillName/files/*filePath", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    const { agentId, category, skillName, filePath } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}/files/${filePath}`, {
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` }
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });
  router.put("/companies/:companyId/team-skills/:agentId/:category/:skillName/files/*filePath", async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const { agentId, category, skillName, filePath } = req.params;
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/team-skills/${agentId}/${category}/${skillName}/files/${filePath}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify(req.body)
      });
      const data = await resp.json();
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Team skills service unavailable" });
    }
  });
  const PREDEFINED_SOURCES = [
    { name: "Paperclip", sourceType: "local_path", sourceLocator: null, sourceKind: "paperclip_bundled" },
    { name: "Hermes Agent", sourceType: "catalog", sourceLocator: null, sourceKind: "hermes_bundled" },
    { name: "Team", sourceType: "catalog", sourceLocator: null, sourceKind: "team" }
  ];
  router.get("/companies/:companyId/skill-sources", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    for (const predefined of PREDEFINED_SOURCES) {
      const existing = await db.execute(sql`SELECT id FROM skill_sources WHERE company_id = ${companyId} AND source_kind = ${predefined.sourceKind}`);
      const rows = existing.rows ?? existing;
      if (rows.length === 0) {
        await db.execute(sql`INSERT INTO skill_sources (company_id, name, source_type, source_locator, source_kind) VALUES (${companyId}, ${predefined.name}, ${predefined.sourceType}, ${predefined.sourceLocator}, ${predefined.sourceKind})`);
      }
    }
    const skillsResult = await db.execute(sql`SELECT source_type, source_locator, metadata FROM company_skills WHERE company_id = ${companyId}`);
    const skills = skillsResult.rows ?? skillsResult;
    const existingResult = await db.execute(sql`SELECT * FROM skill_sources WHERE company_id = ${companyId}`);
    const existing = existingResult.rows ?? existingResult;
    const COVERED_KINDS = new Set(["paperclip_bundled", "hermes_bundled", "git_sync"]);
    const existingAgentIds = new Set(
      existing.filter(s => s.source_kind === "agent").map(s => s.source_locator)
    );
    const agentsMap = new Map();
    for (const s of skills) {
      const meta = typeof s.metadata === 'string' ? JSON.parse(s.metadata) : s.metadata;
      if (meta?.sourceKind !== "agent_created" || !meta?.authorAgentId) continue;
      const aid = meta.authorAgentId;
      if (!agentsMap.has(aid)) {
        agentsMap.set(aid, meta.authorAgentName || "Unknown Agent");
      }
    }
    for (const [aid, agentName] of agentsMap) {
      if (existingAgentIds.has(aid)) continue;
      await db.execute(sql`INSERT INTO skill_sources (company_id, name, source_type, source_locator, source_kind) VALUES (${companyId}, ${agentName}, 'catalog', ${aid}, 'agent')`);
    }
    const uniquePairs = new Map();
    for (const s of skills) {
      const meta = typeof s.metadata === 'string' ? JSON.parse(s.metadata) : s.metadata;
      const mk = meta?.sourceKind;
      if (COVERED_KINDS.has(mk) || mk === "agent_created") continue;
      const key = `${s.source_type}::${s.source_locator ?? ""}`;
      if (!uniquePairs.has(key)) {
        uniquePairs.set(key, { sourceType: s.source_type, sourceLocator: s.source_locator });
      }
    }
    const existingKeys = new Set(
      existing.map(s => `${s.source_type}::${s.source_locator ?? ""}`)
    );
    for (const pair of uniquePairs.values()) {
      const key = `${pair.sourceType}::${pair.sourceLocator ?? ""}`;
      if (existingKeys.has(key)) continue;
      const name = pair.sourceLocator ?? pair.sourceType;
      await db.execute(sql`INSERT INTO skill_sources (company_id, name, source_type, source_locator, source_kind) VALUES (${companyId}, ${name}, ${pair.sourceType}, ${pair.sourceLocator ?? null}, 'git')`);
    }
    const finalResult = await db.execute(sql`SELECT * FROM skill_sources WHERE company_id = ${companyId}`);
    const finalRows = finalResult.rows ?? finalResult;
    res.json(finalRows.map(r => formatSource(r)));
  });
  router.post("/companies/:companyId/skill-sources", async (req, res) => {
    const companyId = req.params.companyId;
    await assertCanMutateCompanySkills(req, companyId);
    const name = req.body.name;
    const repoUrl = req.body.repo_url;
    if (!name || typeof name !== "string" || name.trim().length === 0) {
      res.status(400).json({ error: "Name is required" });
      return;
    }
    if (repoUrl && typeof repoUrl === "string" && repoUrl.trim().length > 0 && !isValidGitUrl(repoUrl)) {
      res.status(400).json({ error: "Invalid git URL format. Expected https://, git://, git@, or ssh://" });
      return;
    }
    const result = await db.execute(sql`INSERT INTO skill_sources (company_id, name, source_type, source_locator, source_kind, repo_url) VALUES (${companyId}, ${name.trim()}, 'catalog', NULL, 'git', ${typeof repoUrl === "string" ? repoUrl.trim() : null}) RETURNING *`);
    const rows = result.rows ?? result;
    const r = rows[0];
    res.status(201).json(formatSource(r));
  });
  router.patch("/companies/:companyId/skill-sources/:sourceId", async (req, res) => {
    const companyId = req.params.companyId;
    const sourceId = req.params.sourceId;
    await assertCanMutateCompanySkills(req, companyId);
    const existingResult = await db.execute(sql`SELECT * FROM skill_sources WHERE id = ${sourceId} AND company_id = ${companyId}`);
    const existingRows = existingResult.rows ?? existingResult;
    if (existingRows.length === 0) {
      res.status(404).json({ error: "Source not found" });
      return;
    }
    const name = req.body.name;
    const repoUrl = req.body.repo_url;
    const syncToken = req.body.sync_token;
    const syncPath = req.body.sync_path;
    const syncAuthor = req.body.sync_author;
    const ref = req.body.ref;
    if (name !== undefined && typeof name === "string" && name.trim().length === 0) {
      res.status(400).json({ error: "Name cannot be empty" });
      return;
    }
    if (repoUrl && typeof repoUrl === "string" && repoUrl.trim().length > 0 && !isValidGitUrl(repoUrl)) {
      res.status(400).json({ error: "Invalid git URL format. Expected https://, git://, git@, or ssh://" });
      return;
    }
    const masked = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
    if (name !== undefined && typeof name === "string") {
      await db.execute(sql`UPDATE skill_sources SET name = ${name.trim()}, updated_at = now() WHERE id = ${sourceId}`);
    }
    if (repoUrl !== undefined) {
      const val = typeof repoUrl === "string" ? repoUrl : null;
      await db.execute(sql`UPDATE skill_sources SET repo_url = ${val}, updated_at = now() WHERE id = ${sourceId}`);
    }
    if (ref !== undefined && typeof ref === "string") {
      await db.execute(sql`UPDATE skill_sources SET ref = ${ref.trim()}, updated_at = now() WHERE id = ${sourceId}`);
    }
    if (syncToken !== undefined && syncToken !== masked) {
      const val = typeof syncToken === "string" && syncToken.trim().length > 0 ? syncToken.trim() : null;
      await db.execute(sql`UPDATE skill_sources SET sync_token = ${val}, updated_at = now() WHERE id = ${sourceId}`);
    }
    if (syncPath !== undefined) {
      const val = typeof syncPath === "string" ? syncPath.trim() : "skills/";
      await db.execute(sql`UPDATE skill_sources SET sync_path = ${val}, updated_at = now() WHERE id = ${sourceId}`);
    }
    if (syncAuthor !== undefined) {
      const val = typeof syncAuthor === "string" && syncAuthor.trim().length > 0 ? syncAuthor.trim() : null;
      await db.execute(sql`UPDATE skill_sources SET sync_author = ${val}, updated_at = now() WHERE id = ${sourceId}`);
    }
    const updatedResult = await db.execute(sql`SELECT * FROM skill_sources WHERE id = ${sourceId}`);
    const updatedRows = updatedResult.rows ?? updatedResult;
    res.json(formatSource(updatedRows[0]));
  });
  router.delete("/companies/:companyId/skill-sources/:sourceId", async (req, res) => {
    const companyId = req.params.companyId;
    const sourceId = req.params.sourceId;
    await assertCanMutateCompanySkills(req, companyId);
    const existingResult = await db.execute(sql`SELECT * FROM skill_sources WHERE id = ${sourceId} AND company_id = ${companyId}`);
    const existingRows = existingResult.rows ?? existingResult;
    if (existingRows.length === 0) {
      res.status(404).json({ error: "Source not found" });
      return;
    }
    const existing = existingRows[0];
    if (existing.source_kind !== "git") {
      res.status(403).json({ error: "Auto-managed sources cannot be deleted" });
      return;
    }
    await db.execute(sql`DELETE FROM skill_sources WHERE id = ${sourceId} AND company_id = ${companyId}`);
    res.json({ deleted: true });
  });
  function isValidGitUrl(url) {
    if (!url || typeof url !== 'string') return false;
    return /^(https?:\/\/|git@|git:\/\/|ssh:\/\/git@)/.test(url.trim());
  }
  function formatSource(r) {
    return {
      id: r.id,
      company_id: r.company_id,
      name: r.name,
      repo_url: r.repo_url,
      ref: r.ref,
      sync_token: r.sync_token ? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022" : null,
      sync_path: r.sync_path,
      sync_author: r.sync_author,
      source_type: r.source_type,
      source_locator: r.source_locator,
      source_kind: r.source_kind,
      created_at: r.created_at,
      updated_at: r.updated_at
    };
  }
  router.post('/companies/:companyId/skill-sources/:sourceId/check-repo', async (req, res) => {
    const companyId = req.params.companyId;
    const sourceId = req.params.sourceId;
    assertCompanyAccess(req, companyId);
    const existingResult = await db.execute(sql`SELECT * FROM skill_sources WHERE id = ${sourceId} AND company_id = ${companyId}`);
    const existingRows = existingResult.rows ?? existingResult;
    if (existingRows.length === 0) {
      res.status(404).json({ error: 'Source not found' });
      return;
    }
    const existing = existingRows[0];
    const repoUrl = req.body.repo_url || existing.repo_url;
    if (!repoUrl) {
      res.status(400).json({ error: 'No repo_url provided or saved' });
      return;
    }
    if (!isValidGitUrl(repoUrl)) {
      res.status(400).json({ error: 'Invalid git URL format', valid: false });
      return;
    }
    execFile('git', ['ls-remote', '--heads', repoUrl], { timeout: 15000 }, (err, stdout, stderr) => {
      if (err) {
        res.json({ accessible: false, error: err.message || 'Repository not accessible' });
        return;
      }
      const NL = String.fromCharCode(10);
      const TAB = String.fromCharCode(9);
      const branches = stdout.trim().split(NL).filter(Boolean).map((line) => {
        const parts = line.split(TAB);
        return parts[1] ? parts[1].replace('refs/heads/', '') : null;
      }).filter(Boolean);
      res.json({ accessible: true, branches });
    });
  });
  router.post('/companies/:companyId/skill-sources/:sourceId/sync', async (req, res) => {
    const companyId = req.params.companyId;
    const sourceId = req.params.sourceId;
    assertCompanyAccess(req, companyId);
    try {
      const resp = await fetch(`${TEAM_SKILLS_URL}/sync-source/${sourceId}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${TEAM_SKILLS_KEY}` }
      });
      const data = await resp.json();
      if (!resp.ok) {
        res.status(resp.status).json(data);
        return;
      }
      res.json(data);
    } catch (err) {
      res.status(502).json({ error: "Sync service unavailable" });
    }
  });
  return router;
}
export {
  companySkillRoutes
};
