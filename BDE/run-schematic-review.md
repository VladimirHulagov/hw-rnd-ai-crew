---
name: run-schematic-review
category: board-design
description: "Meta-orchestrator — loads netlist, runs all check-* modules, collects findings, deduplicates, publishes report to Outline."
triggers:
  - "Run full schematic review"
  - "Execute all checks"
  - "Generate review report"
---

# Run Schematic Review — Orchestrator

## Overview
Meta-skill that coordinates the full schematic review pipeline. Loads netlist data, invokes each check module, collects and deduplicates findings, then publishes a formatted report.

## Workflow

### Step 1: Load Netlist
```
Load skill: schematic-review-core
Parse pstxnet.dat, pstxprt.dat, pstchip.dat
Save parsed_netlist.json for downstream checks
```

### Step 2: Run All Checks (in order)

| # | Skill | Checks Performed |
|---|-------|-----------------|
| 1 | check-i2c-bus | SCL/SDA swap, address collision, pull-ups, open-drain |
| 2 | check-bus-routing | Bus index swap, connector dup, missing signals, USB3 TX/RX |
| 3 | check-diff-pairs | DP/DN swap, P/N swap, USB DP/DM, double AC-coupling |
| 4 | check-power-tree | Feedback dividers, dropout, overvoltage, continuity |
| 5 | check-crystal-load | Crystal CL calculation, oscillator detection |
| 6 | check-floating-pins | Floating EN/OE/DIR, straps, OE disabled |
| 7 | check-duplicates | Duplicate pull-ups, ESD, test points, series R |
| 8 | check-components | Level translator, BOM unification, ESD cap, FPGA bank |

### Step 3: Collect & Deduplicate Findings
```python
# Merge all findings, deduplicate by (net, component, category)
seen = set()
unique_findings = []
for finding in all_findings:
    key = (finding['net'], finding['component'], finding['category'])
    if key not in seen:
        seen.add(key)
        unique_findings.append(finding)
```

### Step 4: Severity Sort
CRITICAL → HIGH → MEDIUM → LOW

### Step 5: Publish Report

**Report format for Outline:**
```markdown
# Schematic Cross-Check Report v[VERSION]

**Project**: [name]
**Date**: [date]
**Netlist**: pstxnet.dat, pstxprt.dat, pstchip.dat

## Executive Summary
| Severity | Count |
|----------|-------|
| CRITICAL | N |
| HIGH | N |
| MEDIUM | N |
| LOW | N |

**Checks run**: 8 modules, ~32 individual checks

## Critical Findings
[detailed list]

## High Severity Findings
[detailed list]

## Medium/Low Findings
[combined list]

## Detailed Check Results
| Check | Status | Findings |
|-------|--------|----------|
| I2C Bus | PASS/FAIL | ... |
| Bus Routing | PASS/FAIL | ... |
| Diff Pairs | PASS/FAIL | ... |
| Power Tree | PASS/FAIL | ... |
| Crystal Load | PASS/FAIL | ... |
| Floating Pins | PASS/FAIL | ... |
| Duplicates | PASS/FAIL | ... |
| Components | PASS/FAIL | ... |

## Recommendations
[ordered action items]
```

## Finding Format (standard)
Each finding from check modules MUST follow this format:
```json
{
  "category": "1.1",
  "check_name": "SCL/SDA Swap",
  "severity": "CRITICAL",
  "status": "FAIL",
  "components": ["U5", "R12"],
  "nets": ["I2C1_SCL", "I2C1_SDA"],
  "description": "SCL and SDA signals are swapped on U5",
  "reference": "Datasheet p.45, Pin Assignment Table",
  "location": "Sheet 3, U5 area"
}
```

## Report Publishing
1. Create Outline document via `mcp_outline_create_document`
2. Use `editMode=append` or `editMode=patch` for updates
3. NEVER use `editMode=replace` on existing documents (destroys content)
4. Update Paperclip issue with summary comment

## Known Issues
- Always run checks via write_file + terminal(python3), NOT execute_code
- Group I2C address collisions (1 finding per group, not pairwise)
- Exclude I2S nets from I2C pull-up checks

