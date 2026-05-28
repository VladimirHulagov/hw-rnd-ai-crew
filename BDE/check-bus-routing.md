---
name: check-bus-routing
category: board-design
description: "Bus routing checks: SD/eMMC/DDR bus index swap, connector duplication, missing signals, USB3 TX/RX swap."
triggers:
  - "Check bus routing"
  - "SDIO bus check"
  - "eMMC bus check"
  - "DDR bus check"
  - "USB3 TX RX swap"
---

# Check Bus Routing

## Checks Performed
1. Bus index swap detection (SD/eMMC/DDR)
2. Connector signal duplication
3. Missing bus signals
4. USB3 TX/RX swap

## Check 1: Bus Index Swap (SD/eMMC/DDR)

### SD/eMMC bus signals
For SD/eMMC interfaces, the data lines D0-D7 and CMD must maintain correct indexing:
```
CLK  → CLK pin on both sides
CMD  → CMD pin on both sides
D0   → D0 pin on both sides
D1   → D1 pin on both sides
...
```

```python
def check_sd_emmc_bus_swap(nets, comp_pins, chips):
    """Check SD/eMMC bus for index swaps."""
    findings = []
    
    sd_signals = ['CLK', 'CMD', 'D0', 'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7']
    
    # Find SD/eMMC controller nets
    sd_nets = {n: pins for n, pins in nets.items() 
               if any(sig in n.upper() for sig in ['SDIO', 'SDMMC', 'SDCARD', 'EMMC'])}
    
    # For each data line, verify it connects corresponding pins on both ends
    for net_name, connections in sd_nets.items():
        net_upper = net_name.upper()
        for sig in sd_signals:
            if f'_{sig}' in net_upper or f'{sig}_' in net_upper:
                # Check: does this net connect to the matching pin on each IC?
                for comp, pin in connections:
                    chip_data = chips.get(comp, {})
                    pin_upper = pin.upper()
                    # Signal name should appear in pin name
                    if sig not in pin_upper and len(connections) > 1:
                        findings.append({
                            'category': '1.2',
                            'check_name': 'SD/eMMC Bus Index Swap',
                            'severity': 'CRITICAL',
                            'status': 'FAIL',
                            'components': [comp],
                            'nets': [net_name],
                            'description': f'{comp}.{pin} on {net_name} — expected {sig} signal on this pin',
                        })
    return findings
```

### DDR bus check
DDR signals have specific routing requirements:
- DQ[0:7] ↔ DQS_P/DQS_N (byte lanes)
- DM ↔ DQS (data mask)
- CA[0:n] command/address
- CLK_P/CLK_N differential clock

```python
def check_ddr_byte_lane_integrity(nets, comp_pins, chips):
    """Verify DDR byte lane consistency — DQ pins map to correct DQS pair."""
    findings = []
    
    # Find DDR-related nets
    ddr_dq_nets = {n: pins for n, pins in nets.items() 
                   if re.match(r'.*DQ\d+', n, re.IGNORECASE)}
    ddr_dqs_nets = {n: pins for n, pins in nets.items() 
                    if re.match(r'.*DQS[PTN]', n, re.IGNORECASE)}
    
    # Check: DQ[0:7] should go with DQS0, DQ[8:15] with DQS1, etc.
    dq_groups = defaultdict(list)
    for net_name in ddr_dq_nets:
        m = re.match(r'.*DQ(\d+)', net_name, re.IGNORECASE)
        if m:
            idx = int(m.group(1))
            byte_lane = idx // 8
            dq_groups[byte_lane].append(net_name)
    
    for lane, dq_list in dq_groups.items():
        if len(dq_list) != 8:
            findings.append({
                'category': '1.2',
                'check_name': 'DDR Byte Lane Incomplete',
                'severity': 'HIGH',
                'status': 'WARN',
                'components': [],
                'nets': dq_list,
                'description': f'DDR byte lane {lane} has {len(dq_list)} DQ lines (expected 8)',
            })
    return findings
```

## Check 2: Connector Signal Duplication

```python
def check_connector_duplication(nets, comp_pins, components):
    """Find connectors that duplicate the same signals."""
    findings = []
    
    # Find connector components
    connectors = {c: p for c, p in components.items() 
                  if any(kw in p.get('part', '').upper() 
                         for kw in ['CONN', 'JACK', 'HEADER', 'RECEPTACLE'])}
    
    # Group connectors by signal set
    conn_signals = {}
    for conn_ref in connectors:
        signals = set()
        for pin_name, net_name in comp_pins.get(conn_ref, {}).items():
            if net_name and not is_power_net(net_name):
                signals.add(net_name)
        conn_signals[conn_ref] = signals
    
    # Compare pairs
    conn_list = list(conn_signals.keys())
    for i in range(len(conn_list)):
        for j in range(i+1, len(conn_list)):
            c1, c2 = conn_list[i], conn_list[j]
            common = conn_signals[c1] & conn_signals[c2]
            if len(common) > 3:  # More than power/GND overlap
                findings.append({
                    'category': '6.2',
                    'check_name': 'Connector Signal Duplication',
                    'severity': 'MEDIUM',
                    'status': 'WARN',
                    'components': [c1, c2],
                    'nets': list(common)[:5],
                    'description': f'{c1} and {c2} share {len(common)} signals: {", ".join(list(common)[:5])}',
                })
    return findings
```

## Check 3: Missing Bus Signals

```python
def check_missing_bus_signals(nets, comp_pins, chips):
    """Detect missing signals on standard interfaces."""
    findings = []
    
    # USB2: DP, DM
    # USB3: TX_P, TX_N, RX_P, RX_N, DP, DM
    # SD: CLK, CMD, D0-D3
    # eMMC: CLK, CMD, D0-D7, RST_N
    
    interface_requirements = {
        'USB2': {'required': ['DP', 'DM'], 'net_prefix': ['USB']},
        'USB3': {'required': ['TX_P', 'TX_N', 'RX_P', 'RX_N'], 'net_prefix': ['USB3']},
        'SD':   {'required': ['CLK', 'CMD', 'D0'], 'net_prefix': ['SD', 'SDIO', 'SDMMC']},
        'eMMC': {'required': ['CLK', 'CMD', 'D0', 'D1', 'D2', 'D3'], 'net_prefix': ['EMMC']},
    }
    
    for iface, reqs in interface_requirements.items():
        iface_nets = {n for n in nets 
                      if any(n.upper().startswith(p) for p in reqs['net_prefix'])}
        
        for sig in reqs['required']:
            found = any(sig in n.upper() for n in iface_nets)
            if not found and iface_nets:
                findings.append({
                    'category': '1.2',
                    'check_name': f'Missing {iface} Signal',
                    'severity': 'HIGH',
                    'status': 'WARN',
                    'components': [],
                    'nets': list(iface_nets)[:3],
                    'description': f'{iface} interface missing {sig} signal',
                })
    return findings
```

## Check 4: USB3 TX/RX Swap

```python
def check_usb3_txrx_swap(nets, comp_pins, chips):
    """Detect USB3 TX/RX cross-connection."""
    findings = []
    
    usb3_tx = {n: pins for n, pins in nets.items() if 'TX_P' in n.upper() or 'TX_N' in n.upper()}
    usb3_rx = {n: pins for n, pins in nets.items() if 'RX_P' in n.upper() or 'RX_N' in n.upper()}
    
    # TX of host must connect to RX of device and vice versa
    # Check: if both sides have TX pin name on a TX net, that's wrong
    for net_name, connections in usb3_tx.items():
        tx_count = 0
        for comp, pin in connections:
            if 'TX' in pin.upper():
                tx_count += 1
        if tx_count == len(connections) and len(connections) > 1:
            findings.append({
                'category': '1.2',
                'check_name': 'USB3 TX/RX Swap',
                'severity': 'CRITICAL',
                'status': 'FAIL',
                'components': [c for c, p in connections],
                'nets': [net_name],
                'description': f'USB3 TX net {net_name} connects to TX pins on both sides — TX/RX swap suspected',
            })
    return findings
```

