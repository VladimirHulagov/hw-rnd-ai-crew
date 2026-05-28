---
name: check-duplicates
category: board-design
description: "Duplicate detection: pull-ups on same net, ESD protection (signal + power), test points, double series resistors."
triggers:
  - "Check duplicates"
  - "Duplicate pull-ups"
  - "Duplicate ESD"
  - "Duplicate test points"
---

# Check Duplicates

## Checks Performed
1. Duplicate pull-up/pull-down resistors on same net
2. Duplicate ESD protection (signal ESD + power rail ESD on same net)
3. Duplicate test points on same net
4. Double series resistors (two Rs in series on same signal path)

## Check 1: Duplicate Pull-ups on Same Net

```python
def check_duplicate_pullups(nets, comp_pins, components):
    """Find nets with multiple pull-up or pull-down resistors."""
    findings = []
    
    for net_name, connections in nets.items():
        pull_ups = []
        pull_downs = []
        
        for comp, pin in connections:
            part = components.get(comp, {}).get('part', '').upper()
            if not part.startswith('R'):
                continue
            
            other_net = get_other_pin_net(comp, pin, comp_pins)
            if not other_net:
                continue
            
            if is_power_net(other_net):
                pull_ups.append((comp, other_net))
            elif 'GND' in other_net.upper():
                pull_downs.append((comp, other_net))
        
        if len(pull_ups) > 1:
            findings.append({
                'category': '6.3',
                'check_name': 'Duplicate Pull-ups',
                'severity': 'MEDIUM',
                'status': 'WARN',
                'components': [c for c, _ in pull_ups],
                'nets': [net_name],
                'description': f'Net {net_name} has {len(pull_ups)} pull-ups: {", ".join(c for c, _ in pull_ups)}',
            })
        
        if len(pull_downs) > 1:
            findings.append({
                'category': '6.3',
                'check_name': 'Duplicate Pull-downs',
                'severity': 'MEDIUM',
                'status': 'WARN',
                'components': [c for c, _ in pull_downs],
                'nets': [net_name],
                'description': f'Net {net_name} has {len(pull_downs)} pull-downs: {", ".join(c for c, _ in pull_downs)}',
            })
    
    return findings
```

## Check 2: Duplicate ESD Protection

ESD diodes may appear redundantly: signal ESD + power-rail ESD on the same line.

```python
def check_duplicate_esd(nets, comp_pins, components):
    """Find nets with redundant ESD protection."""
    findings = []
    
    esd_prefixes = ['TVS', 'ESD', 'PRTR', 'TPD', 'TPD4E', 'USBLC6', 'PR223', 
                    'TUSB', 'SRV05', 'PESD', 'NXP', 'AZX']
    
    for net_name, connections in nets.items():
        esd_devices = []
        
        for comp, pin in connections:
            part = components.get(comp, {}).get('part', '').upper()
            if any(part.startswith(esd) for esd in esd_prefixes):
                esd_devices.append((comp, part))
        
        if len(esd_devices) > 1:
            findings.append({
                'category': '6.2',
                'check_name': 'Duplicate ESD Protection',
                'severity': 'LOW',
                'status': 'WARN',
                'components': [c for c, _ in esd_devices],
                'nets': [net_name],
                'description': f'Net {net_name} has {len(esd_devices)} ESD devices: {", ".join(f"{c}({p})" for c, p in esd_devices)}',
            })
    
    # Also check: ESD device protecting both signal AND power rail
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        if not any(part.startswith(esd) for esd in esd_prefixes):
            continue
        
        cp = comp_pins.get(comp_ref, {})
        net_types = []
        for pin_name, net_name in cp.items():
            if is_power_net(net_name):
                net_types.append('power')
            elif 'GND' in net_name.upper():
                net_types.append('gnd')
            else:
                net_types.append('signal')
        
        signal_count = net_types.count('signal')
        power_count = net_types.count('power')
        
        if signal_count > 0 and power_count > 0:
            # ESD spanning signal and power — could be intentional, flag as info
            pass  # Not a duplicate per se, just awareness
    
    return findings
```

## Check 3: Duplicate Test Points

```python
def check_duplicate_testpoints(nets, comp_pins, components):
    """Find nets with multiple test points."""
    findings = []
    
    for net_name, connections in nets.items():
        test_points = []
        
        for comp, pin in connections:
            part = components.get(comp, {}).get('part', '').upper()
            ref_upper = comp.upper()
            if part.startswith('TP') or ref_upper.startswith('TP') or 'TEST' in part:
                test_points.append(comp)
        
        if len(test_points) > 1:
            findings.append({
                'category': '6.2',
                'check_name': 'Duplicate Test Points',
                'severity': 'LOW',
                'status': 'INFO',
                'components': test_points,
                'nets': [net_name],
                'description': f'Net {net_name} has {len(test_points)} test points: {", ".join(test_points)}',
            })
    
    return findings
```

## Check 4: Double Series Resistors

Two resistors in series on the same signal = likely a mistake or redundant.

```python
def check_double_series_r(nets, comp_pins, components):
    """Find signal paths with two series resistors (potential duplication)."""
    findings = []
    
    for net_name, connections in nets.items():
        if is_power_net(net_name) or 'GND' in net_name.upper():
            continue
        
        # Count resistors on this net
        series_r = []
        for comp, pin in connections:
            part = components.get(comp, {}).get('part', '').upper()
            if part.startswith('R'):
                other_net = get_other_pin_net(comp, pin, comp_pins)
                if other_net and not is_power_net(other_net) and 'GND' not in other_net.upper():
                    series_r.append((comp, other_net))
        
        if len(series_r) >= 2:
            # Multiple series resistors on same signal net
            findings.append({
                'category': '6.1',
                'check_name': 'Double Series Resistors',
                'severity': 'MEDIUM',
                'status': 'WARN',
                'components': [c for c, _ in series_r],
                'nets': [net_name] + [n for _, n in series_r],
                'description': f'Net {net_name} has {len(series_r)} series resistors: {", ".join(c for c, _ in series_r)}',
            })
    
    return findings
```

## Helpers
See schematic-review-core for: get_other_pin_net, is_power_net

