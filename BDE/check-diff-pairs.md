---
name: check-diff-pairs
category: board-design
description: "Differential pair checks: DP/DN swap, P/N swap, USB DP/DM, double AC-coupling, TX/RX orientation, multi-pin connection."
triggers:
  - "Check differential pairs"
  - "DP DN swap"
  - "USB DP DM check"
  - "AC coupling check"
---

# Check Differential Pairs

## Checks Performed
1. DP/DN (P/N) swap detection across all diff pairs
2. USB DP/DM swap
3. Double AC-coupling on diff pairs
4. Diff pair TX/RX orientation
5. Diff pair connected to multiple pins on same component

## Check 1: DP/DN (P/N) Swap Detection

For every differential pair, verify P connects to P and N connects to N on both ends.

```python
def check_diff_pair_pn_swap(nets, comp_pins, chips):
    """Detect P/N swap in differential pairs."""
    findings = []
    
    # Find all differential pair nets
    diff_pairs = find_diff_pairs(nets)
    
    for pair_name, (p_net, n_net) in diff_pairs.items():
        p_conns = nets.get(p_net, [])
        n_conns = nets.get(n_net, [])
        
        # Get common components
        p_comps = {c for c, p in p_conns}
        n_comps = {c for c, p in n_conns}
        common_comps = p_comps & n_comps
        
        for comp in common_comps:
            p_pin = next((pin for c, pin in p_conns if c == comp), None)
            n_pin = next((pin for c, pin in n_conns if c == comp), None)
            
            if not p_pin or not n_pin:
                continue
            
            p_upper = p_pin.upper()
            n_upper = n_pin.upper()
            
            # Check: P net should go to P pin, N net to N pin
            p_on_p_net = any(kw in p_upper for kw in ['_P', 'P', 'DP', 'TX_P', 'RX_P'])
            n_on_n_net = any(kw in n_upper for kw in ['_N', 'N', 'DN', 'TX_N', 'RX_N'])
            p_on_n_net = any(kw in n_upper for kw in ['_P', 'P', 'DP', 'TX_P', 'RX_P'])
            n_on_p_net = any(kw in p_upper for kw in ['_N', 'N', 'DN', 'TX_N', 'RX_N'])
            
            if (p_on_n_net and n_on_p_net) and not (p_on_p_net and n_on_n_net):
                findings.append({
                    'category': '1.1',
                    'check_name': 'Diff Pair P/N Swap',
                    'severity': 'CRITICAL',
                    'status': 'FAIL',
                    'components': [comp],
                    'nets': [p_net, n_net],
                    'description': f'{comp}: P net {p_net} → pin {n_pin} (N), N net {n_net} → pin {p_pin} (P) — SWAPPED',
                })
    return findings

def find_diff_pairs(nets):
    """Identify differential pairs from net names."""
    pairs = {}
    net_names = list(nets.keys())
    
    # Patterns: *_P/*_N, *_DP/*_DN, *_TX_P/*_TX_N, *_RX_P/*_RX_N
    suffixes = [
        ('_P', '_N'), ('_DP', '_DN'), 
        ('_TX_P', '_TX_N'), ('_RX_P', '_RX_N'),
        ('_M', '_P'),  # Some naming conventions
    ]
    
    for net in net_names:
        for s_p, s_n in suffixes:
            if net.endswith(s_p):
                base = net[:-len(s_p)]
                n_counterpart = base + s_n
                if n_counterpart in nets:
                    pairs[base] = (net, n_counterpart)
            elif net.endswith(s_n):
                base = net[:-len(s_n)]
                p_counterpart = base + s_p
                if p_counterpart in nets and base not in pairs:
                    pairs[base] = (p_counterpart, net)
    return pairs
```

## Check 2: USB DP/DM Swap

```python
def check_usb_dpdm_swap(nets, comp_pins, chips):
    """Specific USB DP/DM swap check."""
    findings = []
    
    dp_nets = {n: pins for n, pins in nets.items() if re.search(r'USB.*DP', n, re.IGNORECASE)}
    dm_nets = {n: pins for n, pins in nets.items() if re.search(r'USB.*DM', n, re.IGNORECASE)}
    
    for dp_net, dp_conns in dp_nets.items():
        # Find corresponding DM net
        dm_net = dp_net.replace('DP', 'DM')
        if dm_net not in dm_nets:
            continue
        dm_conns = nets[dm_net]
        
        # Check common components
        dp_comps = {c for c, p in dp_conns}
        dm_comps = {c for c, p in dm_conns}
        
        for comp in dp_comps & dm_comps:
            dp_pin = next((p for c, p in dp_conns if c == comp), None)
            dm_pin = next((p for c, p in dm_conns if c == comp), None)
            
            if dp_pin and dm_pin:
                dp_upper = dp_pin.upper()
                dm_upper = dm_pin.upper()
                
                # DP net should go to DP pin, DM to DM pin
                dp_on_dm = 'DM' in dp_upper and 'DP' not in dp_upper
                dm_on_dp = 'DP' in dm_upper and 'DM' not in dm_upper
                
                if dp_on_dm or dm_on_dp:
                    findings.append({
                        'category': '1.1',
                        'check_name': 'USB DP/DM Swap',
                        'severity': 'CRITICAL',
                        'status': 'FAIL',
                        'components': [comp],
                        'nets': [dp_net, dm_net],
                        'description': f'{comp}: DP net→pin {dp_pin}, DM net→pin {dm_pin} — SWAPPED',
                    })
    return findings
```

## Check 3: Double AC-Coupling

AC coupling capacitors should appear only once in a diff pair path. Two caps in series block DC permanently.

```python
def check_double_ac_coupling(nets, comp_pins, components):
    """Find diff pairs with AC-coupling capacitors on both P and N lines."""
    findings = []
    
    diff_pairs = find_diff_pairs(nets)
    
    for pair_name, (p_net, n_net) in diff_pairs.items():
        for target_net in [p_net, n_net]:
            ac_caps = []
            for comp, pin in nets.get(target_net, []):
                part = components.get(comp, {}).get('part', '').upper()
                if part.startswith('C'):
                    val = parse_cap_value(comp, components)
                    if val and val < 1e-6:  # < 1µF suggests AC coupling
                        ac_caps.append(comp)
            
            if len(ac_caps) > 1:
                findings.append({
                    'category': '1.4',
                    'check_name': 'Double AC Coupling',
                    'severity': 'HIGH',
                    'status': 'FAIL',
                    'components': ac_caps,
                    'nets': [target_net],
                    'description': f'Net {target_net} has {len(ac_caps)} AC-coupling caps: {", ".join(ac_caps)}',
                })
    return findings
```

## Check 4: Diff Pair Multi-Pin Connection

A diff pair signal (P or N) should connect to exactly one pin per component.

```python
def check_diff_pair_multi_pin(nets):
    """Check that diff pair signals don't go to multiple pins on same component."""
    findings = []
    
    diff_pairs = find_diff_pairs(nets)
    
    for pair_name, (p_net, n_net) in diff_pairs.items():
        for net_name in [p_net, n_net]:
            comp_pins_count = defaultdict(list)
            for comp, pin in nets.get(net_name, []):
                comp_pins_count[comp].append(pin)
            
            for comp, pin_list in comp_pins_count.items():
                if len(pin_list) > 1:
                    findings.append({
                        'category': '1.5',
                        'check_name': 'Diff Pair Multi-Pin',
                        'severity': 'HIGH',
                        'status': 'FAIL',
                        'components': [comp],
                        'nets': [net_name],
                        'description': f'{net_name} connects to {len(pin_list)} pins on {comp}: {", ".join(pin_list)}',
                    })
    return findings
```

## Helper Functions
See schematic-review-core for: parse_cap_value, is_power_net, get_other_pin_net

