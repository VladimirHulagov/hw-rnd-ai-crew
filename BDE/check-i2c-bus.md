---
name: check-i2c-bus
category: board-design
description: "I2C bus checks: SCL/SDA swap (direct + through R), address collision, pull-up values, open-drain detection."
triggers:
  - "Check I2C bus"
  - "I2C address collision"
  - "SCL SDA swap"
  - "I2C pull-up check"
---

# Check I2C Bus

## Checks Performed
1. SCL/SDA swap detection (direct connection and through series resistor)
2. I2C address collision on same bus
3. Pull-up presence and value verification
4. Open-drain output detection (missing pull-up)

## Bug Fix Applied
**I2S false positive**: Nets like `I2S2_SCLK_M0_BT` match 'SCL' substring. Use negative lookahead:
```python
# CORRECT — exclude I2S nets
i2c_scl = [n for n in nets if re.match(r'^(?!I2S).*SCL', n, re.IGNORECASE)]
i2c_sda = [n for n in nets if re.match(r'^(?!I2S).*SDA', n, re.IGNORECASE)]
```

## Check 1: SCL/SDA Swap Detection

### Direct swap
```python
def check_scl_sda_swap_direct(nets, chips):
    """Find I2C peripherals where SCL pin is connected to SDA net or vice versa."""
    findings = []
    for net_name, pins in nets.items():
        if not re.match(r'^(?!I2S).*SDA', net_name, re.IGNORECASE):
            continue
        for comp, pin in pins:
            chip_pins = chips.get(comp, {})
            # Check if pin named SCL is on an SDA net
            if 'SCL' in pin.upper() and 'SDA' in net_name.upper():
                findings.append({
                    'category': '1.1',
                    'check_name': 'SCL/SDA Direct Swap',
                    'severity': 'CRITICAL',
                    'status': 'FAIL',
                    'components': [comp],
                    'nets': [net_name],
                    'description': f'{comp} pin {pin} (SCL) connected to SDA net {net_name}',
                })
    return findings
```

### Swap through series resistor
```python
def check_scl_sda_swap_through_r(nets, comp_pins, components):
    """Detect SCL/SDA swap where signals pass through a series resistor."""
    findings = []
    for comp_ref, props in components.items():
        part = props.get('part', '').upper()
        if not part.startswith('R'):
            continue
        # Find which nets this resistor connects
        cp = comp_pins.get(comp_ref, {})
        if len(cp) < 2:
            continue
        pin_nets = list(cp.items())
        net1, net2 = pin_nets[0][1], pin_nets[1][1]
        
        # Check: SCL net on one side, SDA net on the other
        # AND resistor connects to a component with SCL/SDA pins
        is_scl1 = bool(re.match(r'^(?!I2S).*SCL', net1, re.IGNORECASE))
        is_sda1 = bool(re.match(r'^(?!I2S).*SDA', net1, re.IGNORECASE))
        is_scl2 = bool(re.match(r'^(?!I2S).*SCL', net2, re.IGNORECASE))
        is_sda2 = bool(re.match(r'^(?!I2S).*SDA', net2, re.IGNORECASE))
        
        if (is_scl1 and is_sda2) or (is_sda1 and is_scl2):
            findings.append({
                'category': '1.1',
                'check_name': 'SCL/SDA Swap Through R',
                'severity': 'CRITICAL',
                'status': 'FAIL',
                'components': [comp_ref],
                'nets': [net1, net2],
                'description': f'Cross-connection: {net1} ↔ {net2} via {comp_ref}',
            })
    return findings
```

## Check 2: I2C Address Collision

**Bug Fix Applied**: Group collisions — ONE finding per address group, not pairwise.

```python
def check_i2c_address_collision(nets, chips, components):
    """Find I2C devices with identical addresses on the same bus."""
    from collections import defaultdict
    
    # Build bus → devices map
    i2c_buses = defaultdict(list)
    i2c_scl = [n for n in nets if re.match(r'^(?!I2S).*SCL', n, re.IGNORECASE)]
    
    for scl_net in i2c_scl:
        bus_name = scl_net.replace('SCL', '').replace('_SCL', '')
        sda_net = scl_net.replace('SCL', 'SDA')
        if sda_net not in nets:
            continue
        
        # Find all components on both SCL and SDA
        scl_comps = {comp for comp, pin in nets[scl_net]}
        sda_comps = {comp for comp, pin in nets[sda_net]}
        bus_devices = scl_comps & sda_comps
        
        for comp in bus_devices:
            addr = calculate_i2c_address(comp, nets, components, chips)
            i2c_buses[bus_name].append({'ref': comp, 'addr': addr})
    
    # Group by address — ONE finding per group
    findings = []
    for bus, devices in i2c_buses.items():
        addr_groups = defaultdict(list)
        for dev in devices:
            if dev['addr'] is not None:
                addr_groups[dev['addr']].append(dev)
        
        for addr, devs in addr_groups.items():
            if len(devs) > 1:
                findings.append({
                    'category': '7.1',
                    'check_name': 'I2C Address Collision',
                    'severity': 'CRITICAL',
                    'status': 'FAIL',
                    'components': [d['ref'] for d in devs],
                    'nets': [bus + 'SCL', bus + 'SDA'],
                    'description': f'Address 0x{addr:02X} collision on {bus}: {", ".join(d["ref"] for d in devs)}',
                })
    return findings

def calculate_i2c_address(comp, nets, components, chips):
    """Calculate I2C address from component type and strap pins."""
    comp_data = components.get(comp, {})
    part = comp_data.get('part', '').upper()
    
    # Known I2C address map: part_prefix → (base_addr, addr_pins)
    addr_map = {
        'INA231': (0x40, ['A0', 'A1']),  # 0x40-0x43
        'PCA9501': (0x20, ['A0', 'A1', 'A2']),  # 0x20-0x27
        'TPS65988': (0x20, []),
        'BQ25792': (0x6B, ['PROG']),
        'CAT24C': (0x50, ['A0', 'A1', 'A2']),
    }
    
    for prefix, (base, addr_pins) in addr_map.items():
        if part.startswith(prefix):
            addr = base
            # TODO: read strap pin states to compute actual address
            return addr
    return None  # Unknown device — skip
```

## Check 3: I2C Pull-up Presence

```python
def check_i2c_pullups(nets, comp_pins, components):
    """Verify each I2C bus has pull-ups with correct values."""
    findings = []
    i2c_scl = [n for n in nets if re.match(r'^(?!I2S).*SCL', n, re.IGNORECASE)]
    
    for scl_net in i2c_scl:
        bus_base = scl_net.replace('SCL', '').replace('_SCL', '')
        sda_net = scl_net.replace('SCL', 'SDA')
        
        for target_net, sig_name in [(scl_net, 'SCL'), (sda_net, 'SDA')]:
            if target_net not in nets:
                continue
            
            pull_ups = []
            for comp, pin in nets[target_net]:
                part = components.get(comp, {}).get('part', '').upper()
                if part.startswith('R'):
                    val = find_resistor_value(comp, components)
                    # Check if other pin goes to power
                    other_pin_net = get_other_pin_net(comp, pin, comp_pins)
                    if other_pin_net and is_power_net(other_pin_net, nets):
                        pull_ups.append((comp, val))
            
            if not pull_ups:
                findings.append({
                    'category': '3.4',
                    'check_name': 'I2C Missing Pull-up',
                    'severity': 'MEDIUM',
                    'status': 'WARN',
                    'components': [],
                    'nets': [target_net],
                    'description': f'No pull-up on {sig_name} net {target_net}',
                })
            else:
                for pu_comp, pu_val in pull_ups:
                    # Check value appropriateness (1.8V bus needs 1-4.7K, 3.3V ok with 4.7-10K)
                    if pu_val and pu_val > 10000:
                        findings.append({
                            'category': '4.2',
                            'check_name': 'I2C Pull-up Value',
                            'severity': 'MEDIUM',
                            'status': 'WARN',
                            'components': [pu_comp],
                            'nets': [target_net],
                            'description': f'Pull-up {pu_comp} = {pu_val}Ω may be too high for I2C',
                        })
    return findings
```

## Check 4: Open-drain Missing Pull-up

```python
def check_open_drain_pullups(nets, comp_pins, chips):
    """Find open-drain outputs without pull-ups (INT_N, ALERT, GPIO)."""
    findings = []
    od_pins = ['INT_N', 'INT', 'ALERT_N', 'ALERT', 'IRQ_N', 'IRQ', 
               'FAULT_N', 'FAULT', 'PG_N', 'PG', 'PGOOD', 'RST_N']
    
    for comp, pin_map in comp_pins.items():
        for pin_name, net_name in pin_map.items():
            pin_upper = pin_name.upper()
            is_od = any(od in pin_upper for od in od_pins)
            if not is_od:
                continue
            
            # Check if net has a pull-up
            net_pins = nets.get(net_name, [])
            has_pullup = False
            for ncomp, npin in net_pins:
                if ncomp in comp_pins and ncomp != comp:
                    other_net = comp_pins[ncomp].get(get_other_pin(npin), '')
                    if is_power_net(other_net, nets):
                        has_pullup = True
                        break
            
            if not has_pullup:
                findings.append({
                    'category': '3.4',
                    'check_name': 'Open-drain Missing Pull-up',
                    'severity': 'MEDIUM',
                    'status': 'WARN',
                    'components': [comp],
                    'nets': [net_name],
                    'description': f'{comp}.{pin_name} open-drain output has no pull-up on net {net_name}',
                })
    return findings
```

## Helper Functions
```python
def is_power_net(net_name, nets=None):
    kw = ['VCC','VDD','VIN','VOUT','3V3','5V','1V8','1V2']
    return any(kw in net_name.upper() for kw in kw)

def get_other_pin_net(comp, known_pin, comp_pins):
    """Get the net of the other pin for a 2-pin component."""
    pins = comp_pins.get(comp, {})
    for p, n in pins.items():
        if p != known_pin:
            return n
    return None

def get_other_pin(pin_name):
    """For a 2-pin component, return the other pin number/name."""
    return '2' if pin_name == '1' else '1'
```

