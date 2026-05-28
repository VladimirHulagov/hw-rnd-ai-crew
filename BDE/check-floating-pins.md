---
name: check-floating-pins
category: board-design
description: "Floating pin checks: EN/OE/DIR unconnected, passive component floating pins, strap pins (missing pull, both PU+PD), OE pulled inactive."
triggers:
  - "Check floating pins"
  - "Floating EN OE DIR"
  - "Strap pin check"
  - "Pull-up pull-down both"
---

# Check Floating Pins

## Checks Performed
1. Floating EN/OE/DIR pins (control inputs without connection)
2. Floating passive component pins (unconnected R/C/L terminals)
3. Strap/config pins missing pull-up or pull-down
4. Strap pins with BOTH pull-up AND pull-down simultaneously
5. OE/EN pins pulled to inactive state without active control

## Check 1: Floating EN/OE/DIR Pins

```python
def check_floating_control_pins(nets, comp_pins, chips):
    """Find EN, OE, DIR, CS pins that are unconnected."""
    findings = []
    
    control_pin_names = ['EN', 'ENABLE', 'OE', 'OUTPUT_ENABLE', 'DIR', 
                         'CS', 'CHIP_SELECT', 'WR', 'RD', 'RESET_N', 
                         'RST_N', 'SHDN', 'SHUTDOWN', 'STBY', 'STANDBY',
                         'PD_N', 'PU_N', 'SUSPEND']
    
    for comp_ref, pin_map in comp_pins.items():
        for pin_name, net_name in pin_map.items():
            pin_upper = pin_name.upper()
            
            is_control = any(ctrl == pin_upper or pin_upper.startswith(ctrl + '_') 
                           for ctrl in control_pin_names)
            if not is_control:
                continue
            
            # Check: is this pin connected to anything meaningful?
            net_connections = nets.get(net_name, [])
            
            if len(net_connections) <= 1:
                # Only this pin on the net — floating!
                findings.append({
                    'category': '5.1',
                    'check_name': 'Floating Control Pin',
                    'severity': 'HIGH',
                    'status': 'FAIL',
                    'components': [comp_ref],
                    'nets': [net_name],
                    'description': f'{comp_ref}.{pin_name} is floating (only connection on net {net_name})',
                })
            else:
                # Check if connected only to passive without pull
                has_active_drive = False
                has_pull = False
                for nc, np in net_connections:
                    if nc == comp_ref:
                        continue
                    n_part = components.get(nc, {}).get('part', '').upper()
                    if n_part.startswith('R'):
                        # Check if other end goes to power or ground (pull)
                        other_net = get_other_pin_net(nc, np, comp_pins)
                        if other_net:
                            if is_power_net(other_net):
                                has_pull = True
                            elif 'GND' in other_net.upper():
                                has_pull = True
                    else:
                        has_active_drive = True
                
                if not has_active_drive and not has_pull:
                    findings.append({
                        'category': '5.1',
                        'check_name': 'Floating Control Pin',
                        'severity': 'HIGH',
                        'status': 'WARN',
                        'components': [comp_ref],
                        'nets': [net_name],
                        'description': f'{comp_ref}.{pin_name} has no active drive or pull (net: {net_name})',
                    })
    return findings
```

## Check 2: Floating Passive Component Pins

```python
def check_floating_passive_pins(nets, comp_pins, components):
    """Find resistors/capacitors with one unconnected terminal."""
    findings = []
    
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        if not any(part.startswith(kw) for kw in ['R', 'C', 'L', 'FB', 'BL']):
            continue
        
        pin_map = comp_pins.get(comp_ref, {})
        
        # 2-pin component with only 1 net connection
        if len(pin_map) < 2:
            connected_nets = list(pin_map.values())
            findings.append({
                'category': '5.3',
                'check_name': 'Floating Passive Pin',
                'severity': 'MEDIUM',
                'status': 'WARN',
                'components': [comp_ref],
                'nets': connected_nets,
                'description': f'{comp_ref} ({part}) has only {len(pin_map)} pin connected (expected 2)',
            })
    return findings
```

## Check 3: Strap Pins — Missing Pull

```python
def check_strap_missing_pull(nets, comp_pins, chips):
    """Find configuration/strap pins without pull-up or pull-down."""
    findings = []
    
    # Common strap pin patterns
    strap_patterns = [
        re.compile(r'^(BOOT|CFG|CONFIG|STRAP|MODE|SEL|SET|OPT)', re.IGNORECASE),
        re.compile(r'.*(_M0|_M1|_M2|_S0|_S1|_S2)$', re.IGNORECASE),
        re.compile(r'^(GPIO\d*_F|FUNC\d)', re.IGNORECASE),
        re.compile(r'^(SCL|SDA|TXD|RXD|CTS|RTS)_SEL', re.IGNORECASE),
    ]
    
    # SoC strap pins from common datasheets
    soc_strap_pins = {
        'RK3588': ['GPIO0_A0', 'GPIO0_A1', 'GPIO0_B3', 'GPIO0_B4', 'GPIO0_B5',
                    'GPIO0_B6', 'GPIO0_C0', 'GPIO0_C1', 'GPIO0_C2', 'GPIO0_C3',
                    'GPIO0_C4', 'GPIO0_C5', 'GPIO0_D0', 'GPIO0_D1'],
    }
    
    for comp_ref, pin_map in comp_pins.items():
        comp_part = components.get(comp_ref, {}).get('part', '').upper()
        
        for pin_name, net_name in pin_map.items():
            is_strap = False
            
            # Check patterns
            for pattern in strap_patterns:
                if pattern.match(pin_name):
                    is_strap = True
                    break
            
            # Check SoC-specific strap lists
            for soc, strap_list in soc_strap_pins.items():
                if soc in comp_part and pin_name in strap_list:
                    is_strap = True
                    break
            
            if not is_strap:
                continue
            
            # Check for pull resistor
            net_conns = nets.get(net_name, [])
            has_pull = False
            for nc, np in net_conns:
                n_part = components.get(nc, {}).get('part', '').upper()
                if n_part.startswith('R') and nc != comp_ref:
                    other_net = get_other_pin_net(nc, np, comp_pins)
                    if other_net and (is_power_net(other_net) or 'GND' in other_net.upper()):
                        has_pull = True
                        break
            
            if not has_pull and len(net_conns) <= 2:
                findings.append({
                    'category': '3.2',
                    'check_name': 'Strap Pin Missing Pull',
                    'severity': 'HIGH',
                    'status': 'WARN',
                    'components': [comp_ref],
                    'nets': [net_name],
                    'description': f'{comp_ref}.{pin_name} (strap) has no pull-up/pull-down',
                })
    return findings
```

## Check 4: Strap Pin — Both PU and PD

```python
def check_strap_both_pu_pd(nets, comp_pins, components):
    """Find strap pins with BOTH pull-up AND pull-down (creates voltage divider, indeterminate state)."""
    findings = []
    
    strap_keywords = ['BOOT', 'CFG', 'CONFIG', 'STRAP', 'MODE', 'SEL', 'SET']
    
    for comp_ref, pin_map in comp_pins.items():
        for pin_name, net_name in pin_map.items():
            is_strap = any(kw in pin_name.upper() for kw in strap_keywords)
            if not is_strap:
                continue
            
            net_conns = nets.get(net_name, [])
            pull_up_to_power = []
            pull_down_to_gnd = []
            
            for nc, np in net_conns:
                n_part = components.get(nc, {}).get('part', '').upper()
                if n_part.startswith('R') and nc != comp_ref:
                    other_net = get_other_pin_net(nc, np, comp_pins)
                    if other_net:
                        if is_power_net(other_net):
                            pull_up_to_power.append(nc)
                        elif 'GND' in other_net.upper():
                            pull_down_to_gnd.append(nc)
            
            if pull_up_to_power and pull_down_to_gnd:
                findings.append({
                    'category': '3.3',
                    'check_name': 'Strap Both PU and PD',
                    'severity': 'CRITICAL',
                    'status': 'FAIL',
                    'components': [comp_ref] + pull_up_to_power + pull_down_to_gnd,
                    'nets': [net_name],
                    'description': f'{comp_ref}.{pin_name} has both pull-up ({", ".join(pull_up_to_power)}) and pull-down ({", ".join(pull_down_to_gnd)}) — indeterminate state',
                })
    return findings
```

## Check 5: OE/EN Pulled Inactive

```python
def check_oe_en_inactive(nets, comp_pins, components):
    """Find OE/EN pins pulled to inactive state (GND for active-high EN, VCC for active-low)."""
    findings = []
    
    active_low_pins = ['OE_N', 'EN_N', '_N', 'RESET_N', 'RST_N', 'SHDN_N']
    active_high_pins = ['EN', 'OE', 'ENABLE', 'OUTPUT_ENABLE']
    
    for comp_ref, pin_map in comp_pins.items():
        for pin_name, net_name in pin_map.items():
            pin_upper = pin_name.upper()
            
            # Skip if actively driven (connected to another IC output)
            net_conns = nets.get(net_name, [])
            if len(net_conns) > 2:
                continue  # Likely actively driven
            
            is_active_low = any(pin_upper.endswith(kw) for kw in ['_N', '_B'])
            
            for nc, np in net_conns:
                if nc == comp_ref:
                    continue
                n_part = components.get(nc, {}).get('part', '').upper()
                if not n_part.startswith('R'):
                    continue
                
                other_net = get_other_pin_net(nc, np, comp_pins)
                if not other_net:
                    continue
                
                # Active-high EN pulled to GND = permanently disabled
                if not is_active_low and 'GND' in other_net.upper():
                    if any(pin_upper.startswith(kw) for kw in ['EN', 'OE', 'ENABLE']):
                        findings.append({
                            'category': '5.2',
                            'check_name': 'OE/EN Pulled Inactive',
                            'severity': 'HIGH',
                            'status': 'WARN',
                            'components': [comp_ref, nc],
                            'nets': [net_name],
                            'description': f'{comp_ref}.{pin_name} (active-high) pulled to GND via {nc} — permanently disabled',
                        })
                
                # Active-low EN pulled to VCC = permanently disabled
                elif is_active_low and is_power_net(other_net):
                    findings.append({
                        'category': '5.2',
                        'check_name': 'OE/EN Pulled Inactive',
                        'severity': 'HIGH',
                        'status': 'WARN',
                        'components': [comp_ref, nc],
                        'nets': [net_name],
                        'description': f'{comp_ref}.{pin_name} (active-low) pulled to VCC via {nc} — permanently disabled',
                    })
    return findings
```

## Helpers
See schematic-review-core for: get_other_pin_net, is_power_net, find_resistor_value

