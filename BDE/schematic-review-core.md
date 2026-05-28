---
name: schematic-review-core
category: board-design
description: "Parse Cadence OrCAD netlist (pstxnet/pstxprt/pstchip) into structured data, build reference tables for all check modules."
triggers:
  - "Parse OrCAD netlist"
  - "Load netlist for schematic review"
  - "Extract components nets pins from Cadence"
---

# Schematic Review Core — Netlist Parser & Reference Tables

## Overview
Entry point for all schematic review checks. Parses the three-file Cadence OrCAD netlist format and builds reference data structures consumed by check-* skills.

## File Format Details

### pstxnet.dat — Net connections
```
(NET_NAME
 (COMPONENT_NAME PIN_NAME)
 (COMPONENT_NAME PIN_NAME)
 ...
)
```

### pstxprt.dat — Component properties
```
(COMPONENT_NAME
 (PART_NAME PROP1 VAL1 PROP2 VAL2 ...)
)
```

### pstchip.dat — Chip/pin definitions
**IMPORTANT: filename is `pstchip.dat` — NO 'x' (not pstxchip)**

## Parser Script

Save to .py file, run via `terminal("python3 script.py")` — more reliable than execute_code for large scripts.

```python
import re, json, sys
from collections import defaultdict

def parse_pstxnet(filepath):
    """Parse pstxnet.dat → {net_name: [(comp, pin), ...]}"""
    nets = {}
    current_net = None
    current_pins = []
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\((\S+)', line)
            if m and current_net is None:
                current_net = m.group(1)
                current_pins = []
                continue
            m = re.match(r'^\s+\((\S+)\s+(\S+)\)', line)
            if m and current_net is not None:
                current_pins.append((m.group(1), m.group(2)))
                continue
            if line == ')' and current_net is not None:
                nets[current_net] = current_pins
                current_net = None
                current_pins = []
    return nets

def parse_pstxprt(filepath):
    """Parse pstxprt.dat → {comp_name: {part, props}}"""
    components = {}
    current_comp = None
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\((\S+)', line)
            if m and current_comp is None:
                current_comp = m.group(1)
                continue
            m = re.match(r'^\s+\((\S+)\s+(.*)\)', line)
            if m and current_comp is not None:
                part_name = m.group(1)
                rest = m.group(2).strip().split()
                props = {}
                for i in range(0, len(rest)-1, 2):
                    props[rest[i]] = rest[i+1]
                components[current_comp] = {'part': part_name, 'props': props}
                current_comp = None
                continue
            if line == ')' and current_comp is not None:
                current_comp = None
    return components

def parse_pstchip(filepath):
    """Parse pstchip.dat → {chip_name: {pin_name: {num, type}}}"""
    chips = {}
    current_chip = None
    pins = {}
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
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
                pins[m.group(1)] = {'num': m.group(2), 'type': m.group(3)}
                continue
            if line == ')' and current_chip is not None:
                chips[current_chip] = pins
                current_chip = None
    return chips

# === Reference Table Builders ===

def build_comp_nets(nets):
    """Reverse index: {component: {pin: net_name}}"""
    comp_pins = defaultdict(dict)
    for net, pins in nets.items():
        for comp, pin in pins:
            comp_pins[comp][pin] = net
    return dict(comp_pins)

def build_net_types(nets):
    """Classify nets: power, ground, signal"""
    power_kw = ['VCC','VDD','VDDQ','VCCQ','VIN','VOUT','VREF','VBUS',
                '3V3','5V','1V8','1V2','0V8','DVDD','AVDD','IOVDD',
                'VDD_','VCC_','_VDD','_VCC','DCDC','LDO','VPHY',
                'PLL_VDD','VDDA','VDDD']
    gnd_kw = ['GND','VSS','PGND','AGND','DGND','SGND','CHGND','EP']
    result = {'power': [], 'ground': [], 'signal': []}
    for name in nets:
        nl = name.upper()
        if any(nl.startswith(kw) or nl.endswith(kw) or f'_{kw}_' in f'_{nl}_' 
               for kw in gnd_kw):
            result['ground'].append(name)
        elif any(nl.startswith(kw) or f'_{kw}' in nl or f'{kw}_' in nl 
                 for kw in power_kw):
            result['power'].append(name)
        else:
            result['signal'].append(name)
    return result

def find_resistor_value(comp_ref, components):
    """Extract resistance value from component properties."""
    c = components.get(comp_ref, {})
    props = c.get('props', {})
    # Try VALUE property first
    val = props.get('VALUE', props.get('value', ''))
    return parse_resistance(val)

def parse_resistance(val_str):
    """Parse resistance string: '10K', '4.7K', '100R', '0R', '22pF' etc."""
    if not val_str:
        return None
    s = val_str.upper().strip().replace(' ', '')
    m = re.match(r'^([\d.]+)\s*(G|M|K|KΩ|R|OHM)?$', s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or 'R').upper()
    if unit == 'G': return num * 1e9
    if unit == 'M': return num * 1e6
    if unit in ('K', 'KΩ'): return num * 1e3
    return num  # R or OHM or bare number

def find_cap_value(comp_ref, components):
    """Extract capacitance value from component properties."""
    c = components.get(comp_ref, {})
    props = c.get('props', {})
    val = props.get('VALUE', props.get('value', ''))
    return parse_capacitance(val)

def parse_capacitance(val_str):
    """Parse capacitance: '100nF', '10uF', '22pF', '0.1uF'"""
    if not val_str:
        return None
    s = val_str.upper().strip().replace(' ', '')
    m = re.match(r'^([\d.]+)\s*(UF|NF|PF|F)$', s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit == 'UF': return num * 1e-6
    if unit == 'NF': return num * 1e-9
    if unit == 'PF': return num * 1e-12
    return num  # Farads

def is_passive_type(part_name):
    """Check if component is a passive (R, C, L, FB, etc.)"""
    return bool(re.match(r'^(R|C|L|FB|BL|D|LED|TVS|VR|FUSE|XTAL|Y)\d', 
                         part_name.upper()))

# === Main Entry Point ===
def load_netlist(netlist_dir):
    """Load all three files and return complete reference data."""
    import os
    pstxnet = os.path.join(netlist_dir, 'pstxnet.dat')
    pstxprt = os.path.join(netlist_dir, 'pstxprt.dat')
    pstchip = os.path.join(netlist_dir, 'pstchip.dat')
    
    nets = parse_pstxnet(pstxnet)
    components = parse_pstxprt(pstxprt)
    chips = parse_pstchip(pstchip)
    comp_pins = build_comp_nets(nets)
    net_types = build_net_types(nets)
    
    return {
        'nets': nets,           # {net: [(comp,pin),...]}
        'components': components,  # {comp: {part, props}}
        'chips': chips,         # {chip: {pin: {num, type}}}
        'comp_pins': comp_pins, # {comp: {pin: net}}
        'net_types': net_types, # {power/ground/signal: [net,...]}
    }

if __name__ == '__main__':
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else '.'
    data = load_netlist(d)
    print(f"Nets: {len(data['nets'])}")
    print(f"Components: {len(data['components'])}")
    print(f"Chips: {len(data['chips'])}")
    print(f"Power nets: {len(data['net_types']['power'])}")
    print(f"Ground nets: {len(data['net_types']['ground'])}")
    print(f"Signal nets: {len(data['net_types']['signal'])}")
    # Save as JSON for downstream checks
    with open(os.path.join(d, 'parsed_netlist.json'), 'w') as f:
        json.dump({k: (v if not isinstance(v, defaultdict) else dict(v)) 
                   for k, v in data.items()}, f, indent=2, default=str)
```

## Known Pitfalls

1. **pstchip.dat filename**: No 'x' — it's `pstchip.dat` not `pstxchip.dat`
2. **Case sensitivity**: Normalize with `.upper()` for cross-references
3. **Empty nets**: Power symbols may have zero pins — handle gracefully
4. **Duplicate pin names**: Some chips have multiple GND/VDD pins — handle as lists
5. **Encoding**: Use `errors='replace'` for files with special characters
6. **Script execution**: Use `write_file` + `terminal("python3 script.py")`, NOT execute_code (shell quoting issues with complex Python)

