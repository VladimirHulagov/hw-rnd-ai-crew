-- Comprehensive seed data for skills e2e tests
-- Run: docker exec -i <test-db> psql -U paperclip_test -d paperclip_test < seed_skills_e2e.sql

-- Ensure hidden_sources column exists
ALTER TABLE companies ADD COLUMN IF NOT EXISTS hidden_sources jsonb DEFAULT '[]'::jsonb;

-- Ensure hidden column exists
ALTER TABLE company_skills ADD COLUMN IF NOT EXISTS hidden boolean NOT NULL DEFAULT false;

-- Ensure test user exists with timestamps
-- NOTE: better-auth may create a user with different ID on first signup.
-- We create a known user but also grant access to any user with this email.
INSERT INTO "user" (id, email, name, email_verified, created_at, updated_at)
VALUES ('yRqDtjedJzOPSug84LGwjowyK06Jgb8r', 'test@test.com', 'Test User', false, now(), now())
ON CONFLICT (id) DO NOTHING;

-- Ensure admin role
INSERT INTO instance_user_roles (user_id, role) VALUES ('yRqDtjedJzOPSug84LGwjowyK06Jgb8r', 'instance_admin')
ON CONFLICT (user_id, role) DO NOTHING;

-- Company
INSERT INTO companies (id, name, issue_prefix) VALUES ('11111111-1111-1111-1111-111111111111', 'Test Company', 'SUC')
ON CONFLICT (id) DO NOTHING;

-- Membership for seeded user
INSERT INTO company_memberships (company_id, principal_type, principal_id, status)
VALUES ('11111111-1111-1111-1111-111111111111', 'user', 'yRqDtjedJzOPSug84LGwjowyK06Jgb8r', 'active')
ON CONFLICT (company_id, principal_type, principal_id) DO UPDATE SET status = 'active';

-- Grant membership to any other user with the same email (better-auth auto-created)
INSERT INTO company_memberships (id, company_id, principal_type, principal_id, status)
SELECT gen_random_uuid(), '11111111-1111-1111-1111-111111111111', 'user', u.id, 'active'
FROM "user" u
WHERE u.email = 'test@test.com' AND u.id != 'yRqDtjedJzOPSug84LGwjowyK06Jgb8r'
ON CONFLICT DO NOTHING;

-- Also grant instance_admin to any user with this email
INSERT INTO instance_user_roles (user_id, role)
SELECT u.id, 'instance_admin'
FROM "user" u
WHERE u.email = 'test@test.com' AND u.id != 'yRqDtjedJzOPSug84LGwjowyK06Jgb8r'
ON CONFLICT DO NOTHING;

-- =========================================
-- Agent-created skills (2 agents, 5 skills)
-- =========================================
INSERT INTO company_skills (company_id, key, slug, name, description, markdown, source_type, metadata, file_inventory) VALUES
('11111111-1111-1111-1111-111111111111', 'agent/sw-dev-dev/paperclip', 'paperclip', 'Paperclip',
 'Create and manage Paperclip tasks', '# Paperclip Skill\n\nAgent-created skill for task management.',
 'catalog', '{"sourceKind":"agent_created","authorAgentId":"d75fa50c-7213-4801-b04c-cf719ede5277","authorAgentName":"SW DEV"}',
 '[{"path":"SKILL.md","kind":"other"},{"path":"scripts/deploy.sh","kind":"script"}]'),

('11111111-1111-1111-1111-111111111111', 'agent/sw-dev-dev/memory', 'memory', 'Memory',
 'Manage agent memory files', '# Memory Skill\n\nManage memory persistence.',
 'catalog', '{"sourceKind":"agent_created","authorAgentId":"d75fa50c-7213-4801-b04c-cf719ede5277","authorAgentName":"SW DEV"}',
 '[{"path":"SKILL.md","kind":"other"}]'),

('11111111-1111-1111-1111-111111111111', 'agent/sw-dev-dev/outline', 'outline', 'Outline Docs',
 'Create and update Outline documents', '# Outline Docs\n\nSearch and create documents in Outline knowledge base.',
 'catalog', '{"sourceKind":"agent_created","authorAgentId":"d75fa50c-7213-4801-b04c-cf719ede5277","authorAgentName":"SW DEV"}',
 '[{"path":"SKILL.md","kind":"other"},{"path":"examples/create.md","kind":"reference"},{"path":"examples/search.md","kind":"reference"}]'),

('11111111-1111-1111-1111-111111111111', 'agent/researcher-dev/search', 'search', 'Search',
 'Semantic search across sources', '# Search Skill\n\nSemantic search via RAG.',
 'catalog', '{"sourceKind":"agent_created","authorAgentId":"a26e41c9-a62f-4ad2-ab18-93953affbe0b","authorAgentName":"Researcher"}',
 '[{"path":"SKILL.md","kind":"other"}]'),

('11111111-1111-1111-1111-111111111111', 'agent/researcher-dev/web-fetch', 'web-fetch', 'Web Fetch',
 'Fetch and analyze web pages', '# Web Fetch\n\nFetch URLs and extract content.',
 'catalog', '{"sourceKind":"agent_created","authorAgentId":"a26e41c9-a62f-4ad2-ab18-93953affbe0b","authorAgentName":"Researcher"}',
 '[{"path":"SKILL.md","kind":"other"}]')
ON CONFLICT (company_id, key) DO UPDATE SET
  description = EXCLUDED.description,
  markdown = EXCLUDED.markdown,
  metadata = EXCLUDED.metadata,
  file_inventory = EXCLUDED.file_inventory;

-- =========================================
-- Hermes bundled catalog skills (3 skills)
-- =========================================
INSERT INTO company_skills (company_id, key, slug, name, description, markdown, source_type, metadata, file_inventory) VALUES
('11111111-1111-1111-1111-111111111111', 'hermes/hermes-agent/devops/docker-management', 'docker-management', 'Docker Management',
 'Manage Docker containers and images', '# Docker Management\n\nContainer operations skill.',
 'catalog', '{"sourceKind":"hermes_bundled","sourceLabel":"Hermes Agent","sourcePath":"/opt/hermes-agent/skills/devops/docker-management"}',
 '[{"path":"SKILL.md","kind":"other"}]'),

('11111111-1111-1111-1111-111111111111', 'hermes/hermes-agent/development/git-operations', 'git-operations', 'Git Operations',
 'Git version control operations', '# Git Operations\n\nGit workflow management.',
 'catalog', '{"sourceKind":"hermes_bundled","sourceLabel":"Hermes Agent","sourcePath":"/opt/hermes-agent/skills/development/git-operations"}',
 '[{"path":"SKILL.md","kind":"other"}]'),

('11111111-1111-1111-1111-111111111111', 'hermes/hermes-agent/development/code-review', 'code-review', 'Code Review',
 'Automated code review workflows', '# Code Review\n\nReview code quality and suggest improvements.',
 'catalog', '{"sourceKind":"hermes_bundled","sourceLabel":"Hermes Agent","sourcePath":"/opt/hermes-agent/skills/development/code-review"}',
 '[{"path":"SKILL.md","kind":"other"}]')
ON CONFLICT (company_id, key) DO UPDATE SET
  description = EXCLUDED.description,
  markdown = EXCLUDED.markdown,
  metadata = EXCLUDED.metadata,
  file_inventory = EXCLUDED.file_inventory;

-- =========================================
-- Paperclip bundled skills (already exist via onboard, but ensure 4)
-- =========================================
-- These are auto-created by the server; just verify they exist.
-- We'll check in the test that paperclip badge skills are present.

-- =========================================
-- Hidden skill (for show/hide excluded toggle test)
-- =========================================
INSERT INTO company_skills (company_id, key, slug, name, description, markdown, source_type, metadata, file_inventory, hidden) VALUES
('11111111-1111-1111-1111-111111111111', 'agent/sw-dev-dev/deprecated-skill', 'deprecated-skill', 'Deprecated Skill',
 'A hidden/deprecated agent skill', '# Deprecated\n\nThis skill is deprecated.',
 'catalog', '{"sourceKind":"agent_created","authorAgentId":"d75fa50c-7213-4801-b04c-cf719ede5277","authorAgentName":"SW DEV"}',
 '[{"path":"SKILL.md","kind":"other"}]',
 true)
ON CONFLICT (company_id, key) DO UPDATE SET
  hidden = true,
  description = EXCLUDED.description;
