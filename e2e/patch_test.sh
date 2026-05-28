#!/bin/bash
set -e
TC=hw-rnd-ai-crew-paperclip-test-server-1
TD=hw-rnd-ai-crew-paperclip-test-db-1
TMP=/tmp/test-patch
mkdir -p $TMP

echo "=== Patching test container ==="

echo "1. Extracting from production..."
docker cp paperclip-server:/app/server/src/routes/company-skills.ts $TMP/company-skills-route.ts
docker cp paperclip-server:/app/server/dist/services/company-skills.js $TMP/company-skills-svc.js
docker cp paperclip-server:/app/packages/shared/src/validators/company-skill.ts $TMP/company-skill-validator.ts
docker cp paperclip-server:/app/packages/shared/dist/validators/company-skill.js $TMP/company-skill-validator.js
docker cp paperclip-server:/app/packages/shared/dist/validators/company-skill.d.ts $TMP/company-skill-validator.d.ts
docker cp paperclip-server:/app/ui/dist $TMP/uidist

echo "2. Copying to test container..."
docker cp $TMP/company-skills-route.ts $TC:/app/server/src/routes/company-skills.ts
docker cp $TMP/company-skills-svc.js $TC:/app/server/dist/services/company-skills.js
docker cp $TMP/company-skill-validator.ts $TC:/app/packages/shared/src/validators/company-skill.ts
docker cp $TMP/company-skill-validator.js $TC:/app/packages/shared/dist/validators/company-skill.js
docker cp $TMP/company-skill-validator.d.ts $TC:/app/packages/shared/dist/validators/company-skill.d.ts
docker cp $TMP/uidist/. $TC:/app/ui/dist/

echo "3. Patching DB schema for hidden column..."
docker exec $TC python3 -c "
import pathlib
p = pathlib.Path('/app/packages/db/src/schema/company_skills.ts')
t = p.read_text()
if 'boolean' not in t:
    t = t.replace('  jsonb,\n', '  jsonb,\n  boolean,\n', 1)
    t = t.replace('    fileInventory:', '    hidden: boolean(\"hidden\").notNull().default(false),\n    fileInventory:')
    p.write_text(t)
    print('Added hidden column')
else:
    print('hidden column already exists')
"

echo "4. Rebuilding route..."
docker exec $TC node -e "
const esbuild = require('esbuild');
esbuild.buildSync({
  entryPoints: ['/app/server/src/routes/company-skills.ts'],
  outfile: '/app/server/dist/routes/company-skills.js',
  format: 'esm',
  platform: 'node',
  target: 'node20',
  bundle: false,
});
console.log('Route built');
"

echo "4b. Patching hidden-sources to use raw SQL (drizzle schema missing column)..."
docker exec $TC python3 -c "
with open('/app/server/dist/routes/company-skills.js', 'r') as f:
    content = f.read()

# Add sql import
content = content.replace(
    'import { eq } from \"drizzle-orm\";',
    'import { eq, sql } from \"drizzle-orm\";'
)

# Fix GET hidden-sources
old_get = '''router.get(\"/companies/:companyId/hidden-sources\", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    const row = await db.select({ hiddenSources: companies.hiddenSources }).from(companies).where(eq(companies.id, companyId)).then((r) => r[0]);
    res.json(row?.hiddenSources ?? []);
  });'''

new_get = '''router.get(\"/companies/:companyId/hidden-sources\", async (req, res) => {
    const companyId = req.params.companyId;
    assertCompanyAccess(req, companyId);
    const result = await db.execute(sql\`SELECT hidden_sources FROM companies WHERE id = \${companyId}\`);
    const rows = result.rows ?? result;
    const val = rows[0]?.hidden_sources;
    const parsed = typeof val === 'string' ? JSON.parse(val) : (val ?? []);
    res.json(parsed);
  });'''

content = content.replace(old_get, new_get)

# Fix PUT hidden-sources
old_put = '''await db.update(companies).set({ hiddenSources: sources }).where(eq(companies.id, companyId));'''

new_put = '''await db.execute(sql\`UPDATE companies SET hidden_sources = \${JSON.stringify(sources)} WHERE id = \${companyId}\`);'''

content = content.replace(old_put, new_put)

# Fix deleteBySource validation to only require sourceType
old_val = '''if (!sourceType || !sourceLocator) {
      res.status(400).json({ error: \"sourceType and sourceLocator query params are required\" });
      return;
    }'''

new_val = '''if (!sourceType) {
      res.status(400).json({ error: \"sourceType query param is required\" });
      return;
    }'''

content = content.replace(old_val, new_val)

with open('/app/server/dist/routes/company-skills.js', 'w') as f:
    f.write(content)

print('Patched hidden-sources + deleteBySource validation')
"

echo "5. DB patches..."
docker exec $TD psql -U paperclip_test -d paperclip_test -c "ALTER TABLE companies ADD COLUMN IF NOT EXISTS hidden_sources jsonb DEFAULT '[]'::jsonb;" 2>/dev/null || true
docker exec $TD psql -U paperclip_test -d paperclip_test -c "ALTER TABLE company_skills ADD COLUMN IF NOT EXISTS hidden boolean NOT NULL DEFAULT false;" 2>/dev/null || true

echo "6. Restarting..."
docker restart $TC
sleep 5

echo "7. Verifying..."
R=$(docker exec $TC grep -c "hidden-sources\|skills-by-source\|setVisibility" /app/server/dist/routes/company-skills.js)
echo "Routes: $R"
H=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3100/)
echo "Health: $H"

echo "=== Done ==="
