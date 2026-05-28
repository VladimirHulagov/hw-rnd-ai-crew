---
name: netlist-crosscheck-analysis
category: board-design
description: "Systematic netlist cross-check: 32 checks covering I2C collisions, power tree, feedback dividers, pull-ups, duplicate components, floating pins, and more."
triggers:
  - "Cross-check netlist"
  - "Run schematic review checks"
  - "Find errors in netlist"
  - "I2C address collision check"
  - "Power tree analysis"
---

# Netlist Cross-Check Analysis

## Overview
Systematic analysis of parsed Cadence OrCAD netlist data. Run after `cadence-orcad-netlist-parse` has been loaded. Each check is independent and produces PASS/FAIL/WARN/INFO results.

## Prerequisites
- Parsed data from: nets, components, chips (see cadence-orcad-netlist-parse skill)
- Access to RAG for datasheet lookups
- BOM file for cross-reference (if available)

## BUG FIXES FROM LIVE RUN (HWQAA-77)

These issues were found during the real RK3588 laptop review. Apply these fixes:

### Bug 1: I2S false positives in I2C check
- **Problem**: Nets like `I2S2_SCLK_M0_BT` match `SCL` substring
- **Fix**: Exclude nets starting with `I2S` from I2C pull-up detection
- **Regex fix**: When scanning for I2C nets, use `^(?!I2S).*SCL` for SCL, `^(?!I2S).*SDA` for SDA

### Bug 2: INA231 collision over-counting
- **Problem**: 5 devices with same address reported as 4 separate findings (each pair)
- **Fix**: Group collisions. If devices A,B,C,D,E share an address → report as ONE finding listing all colliding devices, not C(5,2)=10 pairwise findings

### Bug 3: pstchip.dat filename
- **Problem**: Search command used wrong filename `pstxchip.dat`
- **Fix**: Filename is `pstchip.dat` (no 'x'). See cadence-orcad-netlist-parse skill.

### Bug 4: Script execution method
- **Problem**: `execute_code` has shell quoting issues with complex Python
- **Fix**: Use `write_file` to save script, then `terminal("python3 script.py")` to run

## Checks (32 total)

### Group 1: Interface Commutation (5 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 1.1 | Differential pair P/N swap detection | CRITICAL |
| 1.2 | Interface pin mapping (SDIO/USB3/DDR/eMMC) | CRITICAL |
| 1.3 | Single net to multiple pins without justification | HIGH |
| 1.4 | Bus width change through passive elements | HIGH |
| 1.5 | Diff pair to multiple component pins | HIGH |

### Group 2: Power (7 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 2.1 | LDO output shorted to another source via low-ohm R | CRITICAL |
| 2.2 | LDO dropout voltage missing | CRITICAL |
| 2.3 | LDO input voltage out of datasheet range | CRITICAL |
| 2.4 | Missing power source (net with only loads) | CRITICAL |
| 2.5 | Missing ferrite bead from source to load | MEDIUM |
| 2.6 | Cyclic power dependency (A powers B powers A) | CRITICAL |
| 2.7 | Wrong power domain connection (VDD2 etc.) | HIGH |

**Power Tree Detection Logic:**
```
Source types: PMIC output, DC-DC output, LDO output
Keywords: VOUT, SW, LDOx, DCDCx, VCCx_OUT
Load keywords: VDD, VCCQ, VIN (on IC pins), DVDD, AVDD
Passive path: ferrite bead (FB, BL), inductor (L), 0-ohm resistor

Build directed graph: source → [passive] → load
Check: every load domain has at least one path from a source
```

### Group 3: Pull-up / Pull-down (4 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 3.1 | Pull-up voltage mismatch with IC supply | HIGH |
| 3.2 | Missing pull on configuration/strap pins | HIGH |
| 3.3 | Both pull-up AND pull-down on same strap | CRITICAL |
| 3.4 | Un-pulled open-drain outputs (INT_N, GPIO) | MEDIUM |

**I2C Pull-up Check (with Bug Fix #1):**
```python
# CORRECT: exclude I2S nets
i2c_scl_nets = [n for n in nets 
                if re.match(r'^(?!I2S).*SCL', n, re.IGNORECASE)]
i2c_sda_nets = [n for n in nets 
                if re.match(r'^(?!I2S).*SDA', n, re.IGNORECASE)]
```

### Group 4: Value Calculations (6 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 4.1 | Crystal load capacitors outside range (should give 8-12pF load) | HIGH |
| 4.2 | I2C pull-up values vs bus voltage (critical for 1.8V) | HIGH |
| 4.3 | DC-DC/LDO feedback divider calculation | CRITICAL |
| 4.4 | DDR calibration resistor ≠ 240Ω | CRITICAL |
| 4.5 | Current-limiting R giving <1mA instead of 100s mA | MEDIUM |
| 4.6 | LED resistor giving incorrect current | LOW |

**Feedback Divider Formula:**
```
Vout = Vref × (1 + R1/R2) + Iadj × R1
For most ICs: Vout ≈ Vref × (1 + R1/R2)
Where Vref is from datasheet (typically 0.6V, 0.8V, or 1.0V)
```

**Crystal Load Capacitor Formula:**
```
CL = (C1 × C2) / (C1 + C2) + Cstray
Cstray ≈ 3-5pF (PCB parasitic)
Target CL from crystal datasheet (typically 8-12pF)
If C1 = C2 = C: CL = C/2 + Cstray
Example: C = 100pF → CL = 50 + 5 = 55pF → WAY too high!
```

### Group 5: Unconnected Pins (3 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 5.1 | Floating EN/OE/DIR pins | HIGH |
| 5.2 | OE/EN pulled to inactive state without control | HIGH |
| 5.3 | Floating passive component pins | MEDIUM |

**Detection:**
- Find components with EN, OE, DIR, CS pins
- Check if pin is connected to any net
- If connected, check if net has a pull-up/pull-down or active driver

### Group 6: Duplication (3 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 6.1 | Duplicate pass-through components on different sheets | MEDIUM |
| 6.2 | Duplicate ESD/testpoints/protection | LOW |
| 6.3 | Duplicate pull-ups on same net | MEDIUM |

### Group 7: Bus Addressing (1 check)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 7.1 | I2C address collision on same bus | CRITICAL |

**I2C Address Collision (with Bug Fix #2):**
```python
def check_i2c_address_collisions(i2c_buses):
    """Group collisions, don't report pairwise."""
    findings = []
    for bus_name, devices in i2c_buses.items():
        addr_groups = defaultdict(list)
        for dev in devices:
            addr = calculate_i2c_address(dev)
            addr_groups[addr].append(dev)
        
        for addr, devs in addr_groups.items():
            if len(devs) > 1:
                # ONE finding per group, not C(n,2) pairwise
                findings.append({
                    'bus': bus_name,
                    'address': f"0x{addr:02X}",
                    'devices': ', '.join(d['ref'] for d in devs),
                    'severity': 'CRITICAL'
                })
    return findings
```

### Group 8: Level Compatibility (2 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 8.1 | Signal level vs FPGA bank voltage mismatch | HIGH |
| 8.2 | Level translator I/O voltage mismatch | HIGH |

### Group 9: Component/Symbol (3 checks)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 9.1 | Symbol pinout errors vs datasheet | CRITICAL |
| 9.2 | Package mismatch (desc vs property) | MEDIUM |
| 9.3 | Unification candidates (1% vs 5% tolerance) | LOW |

### Group 10: Stress Limits (1 check)
| # | Check | Severity if FAIL |
|---|-------|-----------------|
| 10.1 | Capacitor voltage exceeds max rating | HIGH |

## Output Format

For each finding:
```
FINDING #[N]:
  Category: [1-10].[check#]
  Sheet/Location: [net/component reference]
  Components: [U12, R45, etc.]
  Net(s): [NET_NAME]
  Description: [clear description of the issue]
  Reference: [datasheet page/section if applicable]
  Severity: CRITICAL | HIGH | MEDIUM | LOW
  Status: FAIL | WARN | INFO
```

## Report Structure (for Outline)

When creating a report in Outline, use this structure:

```markdown
# Schematic Cross-Check Report

**Project**: [name]
**Date**: [date]
**Reviewer**: Hermes Agent
**Netlist files**: pstxnet.dat, pstxprt.dat, pstchip.dat

## Executive Summary
- Total checks: 32
- PASS: [N], FAIL: [N], WARN: [N], INFO: [N]

## Critical Findings
[Only CRITICAL severity]

## High Severity Findings
[Only HIGH severity]

## Medium/Low Findings
[MEDIUM and LOW severity]

## Detailed Check Results
[Each check with status and details]

## Recommendations
[Action items]
```

## RAG Integration

For checks that need datasheet verification:
1. Search RAG: `mcp_rag_search_library(query="component_partnumber feedback divider")`
2. Search Outline: `mcp_rag_search_outline(query="RK3588 power domain requirements")`
3. If not found, download datasheet and index it

## Workflow
1. Load parsed netlist data (from cadence-orcad-netlist-parse skill)
2. Run all 32 checks sequentially
3. For each FAIL/WARN, verify against datasheet in RAG
4. Compile findings into report
5. Create/update Outline document with results
6. Update Paperclip issue with findings summary

