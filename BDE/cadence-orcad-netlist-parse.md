---
name: cadence-orcad-netlist-parse
category: board-design
description: "Parse Cadence OrCAD netlist files (pstxnet.dat, pstxprt.dat, pstchip.dat) into structured data: components, nets, pins, properties."
triggers:
  - "Parse OrCAD netlist"
  - "Extract components and nets from pstxnet/pstxprt/pstchip"
  - "Load Cadence netlist for analysis"
---

# Cadence OrCAD Netlist Parser

## Overview
Parses the three-file Cadence OrCAD netlist format into structured Python data for cross-check analysis.

## File Format

### pstxnet.dat — Net connections
```
(NET_NAME
 (COMPONENT_NAME PIN_NAME)
 (COMPONENT_NAME PIN_NAME)
 ...
)
```
- Each net block starts with `(NET_NAME`
- Each line `(COMP_NAME PIN_NAME)` is a pin connection
- Block ends with `)`
- Global power nets (VCC, GND) may appear without pin list

### pstxprt.dat — Component properties
```
(COMPONENT_NAME
 (PART_NAME PROPERTY1 VALUE1 PROPERTY2 VALUE2 ...)
)
```
- Contains part references and properties
- Property names/values alternate after PART_NAME

### pstchip.dat — Chip/pin definitions
**IMPORTANT: filename is `pstchip.dat` (NOT `pstxchip.dat` — no 'x')**

```
(CHIP_NAME
 (PIN_NAME PIN_NUMBER PIN_TYPE SWAP_INFO)
 ...
)
```
- Pin types: I=input, O=output, B=bidi, U=unspecified, P=power, G=ground

## Parsing Script Template

Save to a Python file and run via `terminal("python3 script.py")` — this is more reliable than execute_code for large scripts (avoids shell quoting issues).

```python
import re
import json
import sys
from collections import defaultdict

def parse_pstxnet(filepath):
    """Parse pstxnet.dat → {net_name: [(comp, pin), ...]}"""
    nets = {}
    current_net = None
    current_pins = []
    
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Net start: (NET_NAME
            m = re.match(r'^\((\S+)', line)
            if m and current_net is None:
                current_net = m.group(1)
                current_pins = []
                continue
            # Pin connection: (COMP PIN)
            m = re.match(r'^\s+\((\S+)\s+(\S+)\)', line)
            if m and current_net is not None:
                current_pins.append((m.group(1), m.group(2)))
                continue
            # Net end: )
            if line == ')' and current_net is not None:
                nets[current_net] = current_pins
                current_net = None
                current_pins = []
    return nets

def parse_pstxprt(filepath):
    """Parse pstxprt.dat → {comp_name: {part, properties}}"""
    components = {}
    current_comp = None
    props = {}
    
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\((\S+)', line)
            if m and current_comp is None:
                current_comp = m.group(1)
                props = {}
                continue
            m = re.match(r'^\s+\((\S+)\s+(.*)\)', line)
            if m and current_comp is not None:
                part_name = m.group(1)
                rest = m.group(2).strip().split()
                # Properties alternate key/value
                for i in range(0, len(rest)-1, 2):
                    props[rest[i]] = rest[i+1]
                components[current_comp] = {'part': part_name, 'props': props}
                continue
            if line == ')' and current_comp is not None:
                current_comp = None
    return components

def parse_pstchip(filepath):
    """Parse pstchip.dat → {chip_name: {pins: {pin_name: {num, type}}}}"""
    chips = {}
    current_chip = None
    pins = {}
    
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\((\S+)', line)
            if m and current_chip is None:
                current_chip = m.group(1)
                pins = {}
                continue
            m = re.match(r'^\s+\((\S+)\s+(\S+)\s+(\S+)', line)
            if m and current_chip is not None:
                pin_name = m.group(1)
                pin_num = m.group(2)
                pin_type = m.group(3)
                pins[pin_name] = {'num': pin_num, 'type': pin_type}
                continue
            if line == ')' and current_chip is not None:
                chips[current_chip] = {'pins': pins}
                current_chip = None
    return chips

def build_net_summary(nets, components):
    """Build summary stats for all nets."""
    stats = {
        'total_nets': len(nets),
        'total_connections': sum(len(pins) for pins in nets.values()),
        'power_nets': [],
        'signal_nets': [],
        'multi_driver_nets': [],
    }
    
    power_keywords = ['VCC', 'VDD', 'GND', 'VSS', 'VDDQ', 'VCCQ', 
                      'VIN', 'VOUT', 'VREF', 'VBUS', '3V3', '5V', '1V8',
                      '1V2', '0V8', 'PGND', 'AGND', 'DGND']
    
    for net_name, pins in nets.items():
        is_power = any(kw.lower() in net_name.lower() for kw in power_keywords)
        if is_power:
            stats['power_nets'].append(net_name)
        else:
            stats['signal_nets'].append(net_name)
    
    return stats

# Usage:
# nets = parse_pstxnet('pstxnet.dat')
# components = parse_pstxprt('pstxprt.dat')  
# chips = parse_pstchip('pstchip.dat')  # NOTE: pstchip, NOT pstxchip!
```

## Known Pitfalls

1. **pstchip.dat filename**: No 'x' in the name! It's `pstchip.dat`, not `pstxchip.dat`. The find command to locate it:
   ```bash
   find . -name "pstchip.dat" -o -name "pstxchip.dat"
   ```

2. **Net names with special chars**: Parentheses in net names are rare but possible. The regex handles `\S+` which covers most cases.

3. **Empty nets**: Some nets may have zero pins (power symbols). Don't crash on them.

4. **Case sensitivity**: Component names may be case-insensitive depending on OrCAD version. Normalize with `.upper()` or `.lower()` for lookups.

5. **Duplicate pin names**: Some chips have multiple pins with same functional name (e.g., multiple GND). Handle as lists.

## File Location Pattern
Netlist files are typically in a directory structure like:
```
project_name/allegro/
  pstxnet.dat
  pstxprt.dat
  pstchip.dat
```
Or may be uploaded to Nextcloud at a project-specific path. Always search for them first.

