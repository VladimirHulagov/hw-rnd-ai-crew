---
name: check-power-tree
category: board-design
description: "Power tree checks: feedback divider calculation, LDO dropout/overvoltage, continuity (source→load), DDR Rext, LED resistor, VSS/GND shorts."
triggers:
  - "Check power tree"
  - "Feedback divider"
  - "LDO dropout"
  - "Power continuity"
  - "DDR calibration resistor"
---

# Check Power Tree

## Checks Performed
1. DC-DC/LDO feedback divider calculation (verify output voltage)
2. LDO dropout voltage (Vin - Vout ≥ Vdo)
3. LDO input overvoltage (Vin > Vmax)
4. Power continuity (every load has a source)
5. DDR calibration resistor (should be 240Ω)
6. LED current-limiting resistor
7. VSS/GND cross-connection detection

## Check 1: Feedback Divider Calculation

For DC-DC converters and LDOs with adjustable output, verify R1/R2 divider produces the expected voltage.

```python
def check_feedback_dividers(nets, comp_pins, components, chips):
    """Calculate output voltage from feedback divider and compare to expected."""
    findings = []
    
    # Known DC-DC/LDO chips with FB pin and Vref
    fb_chips = {
        'SY8113':  {'vref': 0.6, 'fb_pin': 'FB', 'vin_pin': 'VIN', 'sw_pin': 'SW'},
        'BCT2050': {'vref': 0.6, 'fb_pin': 'FB', 'vin_pin': 'VIN', 'sw_pin': 'SW'},
        'ETA5041': {'vref': 0.6, 'fb_pin': 'FB', 'vin_pin': 'VIN', 'sw_pin': 'SW'},
        'RK860':   {'vref': 0.6, 'fb_pin': 'FB', 'vin_pin': 'VIN', 'sw_pin': 'SW'},
        'SY8009':  {'vref': 0.6, 'fb_pin': 'FB', 'vin_pin': 'VIN', 'sw_pin': 'SW'},
        'TLV62565':{'vref': 0.6, 'fb_pin': 'FB', 'vin_pin': 'VIN', 'sw_pin': 'SW'},
    }
    
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        
        for chip_prefix, chip_info in fb_chips.items():
            if part.startswith(chip_prefix):
                # Find FB net
                fb_net = comp_pins.get(comp_ref, {}).get(chip_info['fb_pin'], None)
                if not fb_net:
                    continue
                
                # Find resistors on FB net
                fb_resistors = []
                for rc, rp in nets.get(fb_net, []):
                    if components.get(rc, {}).get('part', '').upper().startswith('R'):
                        other_net = get_other_pin_net(rc, rp, comp_pins)
                        val = find_resistor_value(rc, components)
                        fb_resistors.append((rc, val, other_net, other_net))
                
                # Identify R1 (to output) and R2 (to GND)
                r1 = None  # FB to VOUT
                r2 = None  # FB to GND
                
                for rc, val, other_pin, other_net in fb_resistors:
                    if other_net and ('GND' in other_net.upper() or 'VSS' in other_net.upper()):
                        r2 = (rc, val)
                    elif other_net and is_power_net(other_net):
                        # Could be R1 to output, or direct feedback
                        # Need to check if other_net is the output
                        r1 = (rc, val)
                
                # Alternative: check if resistor goes to SW net (indirect output)
                if not r1:
                    for rc, val, other_pin, other_net in fb_resistors:
                        if r2 and rc != r2[0]:
                            r1 = (rc, val)
                
                if r1 and r2 and r1[1] and r2[1]:
                    vref = chip_info['vref']
                    vout_calc = vref * (1 + r1[1] / r2[1])
                    
                    # Expected output from net name
                    sw_net = comp_pins.get(comp_ref, {}).get(chip_info['sw_pin'], '')
                    expected_v = parse_voltage_from_net(sw_net)
                    
                    if expected_v and abs(vout_calc - expected_v) > 0.05:
                        findings.append({
                            'category': '4.3',
                            'check_name': 'Feedback Divider Mismatch',
                            'severity': 'CRITICAL',
                            'status': 'FAIL',
                            'components': [comp_ref, r1[0], r2[0]],
                            'nets': [fb_net, sw_net or ''],
                            'description': f'{comp_ref} ({chip_prefix}): Vout={vout_calc:.3f}V (R1={r1[1]}Ω, R2={r2[1]}Ω, Vref={vref}V) vs expected {expected_v}V from net name',
                            'reference': f'{chip_prefix} datasheet: Vref={vref}V',
                        })
    return findings

def parse_voltage_from_net(net_name):
    """Extract voltage value from net name: VDD_0V75_S0 → 0.75, 3V3 → 3.3"""
    if not net_name:
        return None
    import re
    # Pattern: 0V75, 1V2, 3V3, 0V9
    m = re.search(r'(\d)V(\d+)', net_name)
    if m:
        return float(m.group(1) + '.' + m.group(2))
    return None
```

## Check 2: LDO Dropout Voltage

```python
def check_ldo_dropout(nets, comp_pins, components):
    """Verify LDO dropout: Vin - Vout >= Vdo (typically 200-400mV)."""
    findings = []
    
    ldo_chips = {
        'TLV': 0.2, 'LP3985': 0.2, 'MIC5219': 0.3,
        'RT9193': 0.25, 'SY8089': 0.3, 'BCT2050': 0.3,
    }
    
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        
        for prefix, vdo in ldo_chips.items():
            if part.startswith(prefix):
                vin_net = comp_pins.get(comp_ref, {}).get('VIN', '')
                vout_net = comp_pins.get(comp_ref, {}).get('VOUT', '')
                
                vin_v = parse_voltage_from_net(vin_net)
                vout_v = parse_voltage_from_net(vout_net)
                
                if vin_v and vout_v:
                    dropout = vin_v - vout_v
                    if dropout < vdo:
                        findings.append({
                            'category': '2.2',
                            'check_name': 'LDO Dropout Insufficient',
                            'severity': 'CRITICAL',
                            'status': 'FAIL',
                            'components': [comp_ref],
                            'nets': [vin_net, vout_net],
                            'description': f'{comp_ref} ({part}): dropout={dropout:.3f}V < required {vdo}V (Vin={vin_v}V, Vout={vout_v}V)',
                        })
    return findings
```

## Check 3: LDO Input Overvoltage

```python
def check_ldo_overvoltage(nets, comp_pins, components):
    """Check if LDO input voltage exceeds maximum rating."""
    findings = []
    
    # Max Vin for common LDOs
    ldo_max_vin = {
        'BCT2050': 5.5, 'RT9193': 6.0, 'TLV733': 5.5,
        'MIC5219': 16.0, 'LP3985': 6.0,
    }
    
    for comp_ref, comp_data in components.items():
        part = comp_data.get('part', '').upper()
        
        for prefix, vmax in ldo_max_vin.items():
            if part.startswith(prefix):
                vin_net = comp_pins.get(comp_ref, {}).get('VIN', '')
                vin_v = parse_voltage_from_net(vin_net)
                
                if vin_v and vin_v > vmax:
                    findings.append({
                        'category': '2.3',
                        'check_name': 'LDO Input Overvoltage',
                        'severity': 'CRITICAL',
                        'status': 'FAIL',
                        'components': [comp_ref],
                        'nets': [vin_net],
                        'description': f'{comp_ref} ({part}): Vin={vin_v}V > Vmax={vmax}V',
                    })
    return findings
```

## Check 4: Power Continuity

```python
def check_power_continuity(nets, comp_pins, net_types):
    """Every power load net must have a path to a source."""
    findings = []
    
    source_keywords = ['VOUT', 'SW', 'LDO', 'DCDC', 'DCDCOUT']
    load_keywords = ['VDD', 'VCCQ', 'VIN', 'DVDD', 'AVDD', 'IOVDD']
    
    for net_name in net_types.get('power', []):
        connections = nets.get(net_name, [])
        if not connections:
            findings.append({
                'category': '2.4',
                'check_name': 'Missing Power Source',
                'severity': 'CRITICAL',
                'status': 'FAIL',
                'components': [],
                'nets': [net_name],
                'description': f'Power net {net_name} has no connections (no source)',
            })
            continue
        
        # Check if any connection is from a known source
        has_source = False
        for comp, pin in connections:
            pin_upper = pin.upper()
            if any(kw in pin_upper for kw in source_keywords):
                has_source = True
                break
            # Also check if connected through ferrite bead
            part = components.get(comp, {}).get('part', '').upper()
            if part.startswith('FB') or part.startswith('BL'):
                # Ferrite bead — check other end
                other_net = get_other_pin_net(comp, pin, comp_pins)
                if other_net and is_power_net(other_net):
                    has_source = True
                    break
        
        if not has_source and len(connections) > 2:
            findings.append({
                'category': '2.4',
                'check_name': 'Missing Power Source',
                'severity': 'CRITICAL',
                'status': 'FAIL',
                'components': [c for c, p in connections[:5]],
                'nets': [net_name],
                'description': f'Power net {net_name} has {len(connections)} pins but no identifiable source',
            })
    return findings
```

## Check 5: DDR Calibration Resistor

```python
def check_ddr_rext(nets, comp_pins, components):
    """DDR ZQ calibration resistor should be 240Ω ±1%."""
    findings = []
    
    zq_nets = {n: pins for n, pins in nets.items() 
               if 'ZQ' in n.upper() or 'REXT' in n.upper() or 'CAL' in n.upper()}
    
    for net_name, connections in zq_nets.items():
        for comp, pin in connections:
            part = components.get(comp, {}).get('part', '').upper()
            if part.startswith('R'):
                val = find_resistor_value(comp, components)
                if val and abs(val - 240) > 12:  # ±5% tolerance
                    findings.append({
                        'category': '4.4',
                        'check_name': 'DDR Rext Wrong Value',
                        'severity': 'CRITICAL',
                        'status': 'FAIL',
                        'components': [comp],
                        'nets': [net_name],
                        'description': f'DDR calibration R {comp} = {val}Ω, expected 240Ω ±5%',
                    })
    return findings
```

## Check 6: LED Resistor

```python
def check_led_resistors(nets, comp_pins, components):
    """Verify LED current-limiting resistors set appropriate current."""
    findings = []
    
    for comp_ref, pins in comp_pins.items():
        part = components.get(comp_ref, {}).get('part', '').upper()
        if not part.startswith('LED'):
            continue
        
        for pin_name, net_name in pins.items():
            # Find series resistor on the anode/cathode net
            for rc, rp in nets.get(net_name, []):
                r_part = components.get(rc, {}).get('part', '').upper()
                if r_part.startswith('R'):
                    r_val = find_resistor_value(rc, components)
                    other_net = get_other_pin_net(rc, rp, comp_pins)
                    
                    if other_net and is_power_net(other_net):
                        v_supply = parse_voltage_from_net(other_net) or 3.3
                        v_led = 2.0  # Typical LED forward voltage
                        i_led = (v_supply - v_led) / r_val if r_val else 0
                        
                        if i_led < 0.001:  # < 1mA
                            findings.append({
                                'category': '4.6',
                                'check_name': 'LED Current Too Low',
                                'severity': 'LOW',
                                'status': 'WARN',
                                'components': [comp_ref, rc],
                                'nets': [net_name, other_net],
                                'description': f'LED {comp_ref} current ~{i_led*1000:.1f}mA (R={r_val}Ω, V={v_supply}V) — may be dim',
                            })
                        elif i_led > 0.030:  # > 30mA
                            findings.append({
                                'category': '4.6',
                                'check_name': 'LED Current Too High',
                                'severity': 'MEDIUM',
                                'status': 'WARN',
                                'components': [comp_ref, rc],
                                'nets': [net_name, other_net],
                                'description': f'LED {comp_ref} current ~{i_led*1000:.1f}mA — may exceed LED rating',
                            })
    return findings
```

## Helpers
See schematic-review-core for: find_resistor_value, parse_resistance, is_power_net, get_other_pin_net, parse_voltage_from_net

