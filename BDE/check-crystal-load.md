---
name: check-crystal-load
category: board-design
description: "Crystal load capacitance calculation, active oscillator detection, crystal ESR check."
triggers:
  - "Check crystal load"
  - "Crystal CL calculation"
  - "Oscillator check"
---

# Check Crystal Load

## Checks Performed
1. Crystal load capacitance (CL) calculation — verify C1/C2 values
2. Active oscillator vs passive crystal detection
3. Crystal load resistor (feedback Rf) check

## Check 1: Crystal Load Capacitance

**Formula:**
```
CL = (C1 × C2) / (C1 + C2) + Cstray
Cstray ≈ 3-5pF (PCB parasitic)
If C1 = C2 = C: CL = C/2 + Cstray
```

Typical crystal CL requirement: 8-15pF (from crystal datasheet)

**HWQAA-77 bug found**: Y1 24MHz crystal with C1=C2=100pF → CL=55pF, needed 8-12pF.

```python
def check_crystal_load_caps(nets, comp_pins, components):
    """Calculate crystal load capacitance and verify against typical range."""
    findings = []
    
    # Find crystal components (Y* or XTAL*)
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        if not any(part.startswith(kw) for kw in ['XTAL', 'CRYSTAL', 'ABM', 'ECS', 'NDK']):
            # Also check by reference designator pattern
            if not comp_ref.upper().startswith('Y'):
                continue
        
        # Find nets connected to crystal pins
        cp = comp_pins.get(comp_ref, {})
        crystal_nets = list(cp.values())
        
        if len(crystal_nets) < 2:
            continue
        
        # Find load capacitors on each crystal net
        for net_name in crystal_nets:
            net_upper = net_name.upper()
            if 'GND' in net_upper or 'VCC' in net_upper or 'VDD' in net_upper:
                continue  # Skip power/ground connections
            
            load_caps = []
            for c_comp, c_pin in nets.get(net_name, []):
                c_part = components.get(c_comp, {}).get('part', '').upper()
                if c_part.startswith('C'):
                    # Verify other pin goes to GND
                    other_net = get_other_pin_net(c_comp, c_pin, comp_pins)
                    if other_net and ('GND' in other_net.upper() or 'VSS' in other_net.upper()):
                        val = parse_cap_value(c_comp, components)
                        if val:
                            load_caps.append((c_comp, val))
            
            if len(load_caps) >= 2:
                c1_val = load_caps[0][1] * 1e12  # Convert to pF
                c2_val = load_caps[1][1] * 1e12
                c_stray = 5  # pF (conservative estimate)
                
                cl = (c1_val * c2_val) / (c1_val + c2_val) + c_stray
                
                # Check range
                if cl > 20 or cl < 5:
                    findings.append({
                        'category': '4.1',
                        'check_name': 'Crystal CL Out of Range',
                        'severity': 'HIGH',
                        'status': 'FAIL',
                        'components': [comp_ref, load_caps[0][0], load_caps[1][0]],
                        'nets': crystal_nets,
                        'description': f'{comp_ref}: CL={cl:.1f}pF (C1={c1_val:.0f}pF, C2={c2_val:.0f}pF, Cstray={c_stray}pF) — typical range 8-15pF',
                    })
    
    return findings
```

## Check 2: Active Oscillator Detection

Active oscillators (XO) don't need load capacitors. If we find load caps on an XO, that's a warning.

```python
def check_active_oscillator_with_load_caps(nets, comp_pins, components):
    """Warn if active oscillator has unnecessary load capacitors."""
    findings = []
    
    xo_prefixes = ['SG', 'ECS', 'TXC', 'ABL', 'ASEM', 'ASDM', 'ASV']
    
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        is_xo = any(part.startswith(p) for p in xo_prefixes)
        if not is_xo:
            continue
        
        # Check for load caps on output
        cp = comp_pins.get(comp_ref, {})
        out_net = cp.get('OUT', cp.get('CLK_OUT', cp.get('OUTPUT', '')))
        
        if out_net:
            for c_comp, c_pin in nets.get(out_net, []):
                c_part = components.get(c_comp, {}).get('part', '').upper()
                if c_part.startswith('C'):
                    other_net = get_other_pin_net(c_comp, c_pin, comp_pins)
                    if other_net and 'GND' in other_net.upper():
                        findings.append({
                            'category': '4.1',
                            'check_name': 'Active Oscillator with Load Caps',
                            'severity': 'LOW',
                            'status': 'WARN',
                            'components': [comp_ref, c_comp],
                            'nets': [out_net],
                            'description': f'Active oscillator {comp_ref} has load cap {c_comp} on output — XO doesn\'t need external CL caps',
                        })
    return findings
```

## Check 3: Crystal Feedback Resistor

Some crystals need a parallel feedback resistor (Rf) across XIN/XOUT (typically 1-10MΩ).

```python
def check_crystal_feedback_r(nets, comp_pins, components):
    """Check for missing or wrong feedback resistor on crystal oscillator."""
    findings = []
    
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        if not any(part.startswith(kw) for kw in ['XTAL', 'CRYSTAL', 'ABM', 'ECS']):
            if not comp_ref.upper().startswith('Y'):
                continue
        
        cp = comp_pins.get(comp_ref, {})
        crystal_nets = list(cp.values())
        
        if len(crystal_nets) < 2:
            continue
        
        # Look for resistor between the two crystal nets
        has_feedback_r = False
        for net1 in crystal_nets:
            for rc, rp in nets.get(net1, []):
                r_part = components.get(rc, {}).get('part', '').upper()
                if r_part.startswith('R'):
                    other_net = get_other_pin_net(rc, rp, comp_pins)
                    if other_net in crystal_nets:
                        val = find_resistor_value(rc, components)
                        has_feedback_r = True
                        if val and (val < 100e3 or val > 20e6):
                            findings.append({
                                'category': '4.1',
                                'check_name': 'Crystal Feedback R Value',
                                'severity': 'LOW',
                                'status': 'WARN',
                                'components': [comp_ref, rc],
                                'nets': crystal_nets,
                                'description': f'{comp_ref}: feedback R {rc} = {val/1e6:.1f}MΩ — typical 1-10MΩ',
                            })
    
    return findings
```

## Typical Crystal Parameters Reference

| Frequency | CL (typical) | ESR (max) | C1/C2 typical |
|-----------|-------------|-----------|---------------|
| 24 MHz    | 8-12 pF     | 40 Ω     | 10-15 pF      |
| 32.768 kHz| 6-12.5 pF   | 35-50 kΩ | 6-12 pF       |
| 25 MHz    | 8-12 pF     | 40 Ω     | 10-15 pF      |
| 48 MHz    | 8-12 pF     | 30 Ω     | 10-15 pF      |
| 27 MHz    | 10-15 pF    | 40 Ω     | 12-18 pF      |

## Helpers
See schematic-review-core for: parse_cap_value, find_resistor_value, get_other_pin_net

