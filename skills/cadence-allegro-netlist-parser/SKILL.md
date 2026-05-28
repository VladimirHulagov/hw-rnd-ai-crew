---
name: cadence-allegro-netlist-parser
description: Parse Cadence Allegro expanded netlist files (pstxnet.dat, pstxprt.dat, pstchip.dat) for schematic cross-check analysis
version: 1.0
category: schematic-review
---

# Cadence Allegro Expanded Netlist Parser

## Overview
Parses three Cadence Allegro expanded netlist files produced by PSTWRITER to build a complete component and connectivity map for schematic review.

## File Formats

### pstchip.dat (Library Definitions)
- Header: `FILE_TYPE=LIBRARY_PARTS;`
- Each primitive: `primitive 'NAME'; ... pin ... end_pin; body ... end_body; end_primitive;`
- Pins: `'PIN_NAME': PIN_NUMBER='(NUM)'; PINUSE='UNSPEC|POWER|...';`
- Body: `PART_NAME='...'; JEDEC_TYPE='...'; CLASS='...'; VALUE='...';`

### pstxprt.dat (Component Instances)
- Header: `FILE_TYPE = EXPANDEDPARTLIST;`
- **CRITICAL**: Split by `\nPART_NAME\n` markers, NOT by complex regex
- Each part starts with ` PART_NAME\n REFDES 'PRIMITIVE_NAME':\n COMMENT='...';`
- Sections: `SECTION_NUMBER N\n 'PATH':\n C_PATH='...', P_PATH='...', ...`
- Key gotcha: regex `(PART_NAME\s*\n.*END_PART)` fails — use `re.split(r'\nPART_NAME\s*\n', content)` instead

### pstxnet.dat (Net Connectivity)
- Header: `FILE_TYPE = EXPANDEDNETLIST;`
- Split by `\nNET_NAME\n` markers
- Each net: ` 'NET_NAME'\n 'PATH':\n ... NODE_NAME REFDES PIN\n 'INST_PATH':\n 'PIN_SIGNAL':;`
- Use `re.split(r'\nNET_NAME\s*\n', content)` approach

## Parsing Pattern (Proven)

```python
# pstxprt - WORKING approach
parts = re.split(r'\nPART_NAME\s*\n', content)
for part_text in parts[1:]:
    header_match = re.match(r"\s*(\S+)\s+'([^']+)'\s*:", part_text)
    refdes = header_match.group(1)      # e.g. "C1", "U29"
    primitive_name = header_match.group(2)  # full primitive name
    
    # Sections inside
    sec_pattern = re.compile(
        r"SECTION_NUMBER\s+(\d+)\s*\n\s*'([^']+)':\s*\n(.*?)(?=\nPART_NAME|\Z)",
        re.DOTALL
    )

# pstxnet - WORKING approach
net_sections = re.split(r'\nNET_NAME\s*\n', content)
for net_text in net_sections[1:]:
    name_match = re.match(r"\s*'([^']+)'\s*\n", net_text)
    net_name = name_match.group(1)
    
    node_pattern = re.compile(
        r"NODE_NAME\s+(\S+)\s+(\S+)\s*\n\s*'[^']+'\s*:\s*\n\s*'([^']+)'\s*:;"
    )
```

## Component Classification
Use refdes prefix + primitive name keywords:
- `_IC_` in prim name → IC (takes priority)
- `CAPACITOR/RESISTOR/INDUCT/FERRITE/DIODE/TVS/LED/FET/NPN` → by type
- Fallback to prefix: U=IC, C=CAP, R=RES, L=IND, FB=FERRITE, D=DIODE, J=CONN, TP=TEST, Y=CRYSTAL, Q=TRANSISTOR

## Value Extraction from Primitive Names
- Discrete: `re.search(r'DISCRETE_([^_]+(?:\.[^_]+)?)_', prim_name)` → "0.1U", "22U", "100K"
- ICs: `re.search(r'_IC_([^_\']+)', prim_name)` → "RK3588", "SY8105"

## Common Gotchas
1. **pstchip.dat filename** — no 'x' in name! It's `pstchip.dat`, NOT `pstxchip.dat`
2. **pstxprt regex failure** — single-pass regex `(PART_NAME\s*\n.*END_PART)` returns 0 results. Must use split-by-marker approach
3. **I2S false positives** — nets like `I2S2_SCLK_M0_BT` match 'SCL' substring in I2C checks. Exclude 'I2S' prefix nets from I2C pull-up checks
4. Files may be in root `/` not in project directory

## Output Data Structure
```python
{
    'primitives': {prim_name: {pins: {name: {number, use}}, body: {prop: val}}},
    'components': {refdes: {primitive_name, comment, sections}},
    'nets': {net_name: [{refdes, pin, pin_signal}]},
    'comp_nets': {refdes: {pin: net_name}},  # reverse map
}
```

## Known Project: RK3588 Tablet
- 1142 components (683 caps, 301 res, 55 ICs, 19 inductors, 25 diodes, etc.)
- 678 nets, 113 power-related
- Key ICs: RK3588 (U1), 2x LPDDR4 (U2/U3), eMMC (U4), RK806 PMIC (U29), 3x RK860 DC-DC (U26-28), 15x INA231, 3x GW1N FPGA, PCA9501, BCT regulators, etc.
