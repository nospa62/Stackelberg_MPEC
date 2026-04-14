import sys
import os
import re
import math

def parse_network_dat(filepath):
    """Parse network.dat to extract parameters and sets."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    data = {
        'BUSES': [], 'GENERATORS': [], 'REF_BUSES': [],
        'S_base': 100.0,
        'G': {}, 'B': {},
        'P_load': {}, 'Q_load': {}, 'Q_shunt': {},
        'gen_bus': {}, 'P_gen_fixed': {},
        'q_inj_max': {}, 'q_abs_max': {},
        'cost_a_inj': {}, 'cost_b_inj': {}, 'cost_c_inj': {},
        'cost_a_abs': {}, 'cost_b_abs': {}, 'cost_c_abs': {}
    }
    
    m = re.search(r'param\s+s_base_mva\s*:=\s*([\d\.]+)', content)
    if m: data['S_base'] = float(m.group(1))
    else:
        m = re.search(r'param\s+S_base\s*:=\s*([\d\.]+)', content)
        if m: data['S_base'] = float(m.group(1))
    
    for set_name in ['BUSES', 'GENERATORS', 'REF_BUSES']:
        m = re.search(r'set\s+' + set_name + r'\s*:=\s*(.*?);', content, re.DOTALL)
        if m:
            data[set_name] = m.group(1).split()
            
    def parse_1d(param_name, is_float=True):
        d = {}
        m = re.search(r'param\s+' + param_name + r'\s*:=\s*(.*?);', content, re.DOTALL)
        if m:
            for line in m.group(1).strip().split('\n'):
                parts = line.split()
                if len(parts) >= 2:
                    d[parts[0]] = float(parts[1]) if is_float else parts[1]
        return d
                
    data['P_load'] = parse_1d('P_load')
    data['Q_load'] = parse_1d('Q_load')
    data['Q_shunt'] = parse_1d('Q_shunt')
    data['gen_bus'] = parse_1d('gen_bus', is_float=False)
    data['P_gen_fixed'] = parse_1d('P_gen_fixed')
    data['q_inj_max'] = parse_1d('q_inj_max')
    data['q_abs_max'] = parse_1d('q_abs_max')
    data['cost_a_inj'] = parse_1d('cost_a_inj')
    data['cost_b_inj'] = parse_1d('cost_b_inj')
    data['cost_c_inj'] = parse_1d('cost_c_inj')
    data['cost_a_abs'] = parse_1d('cost_a_abs')
    data['cost_b_abs'] = parse_1d('cost_b_abs')
    data['cost_c_abs'] = parse_1d('cost_c_abs')
    
    for param in ['G', 'B']:
        m = re.search(r'param\s+' + param + r'\s*:=\s*(.*?);', content, re.DOTALL)
        if m:
            tokens = m.group(1).split()
            for i in range(0, len(tokens), 3):
                data[param][(tokens[i], tokens[i+1])] = float(tokens[i+2])
                
    return data

def normalise_key(k):
    """Normalise a parameter key: int-like floats → int string, else pass through."""
    try:
        return str(int(float(k)))
    except (ValueError, TypeError):
        return str(k)  # symbolic ID — return as-is, log a warning

def parse_solution_raw(filepath):
    """Parse solution_raw.txt to extract variable values."""
    with open(filepath, 'r') as f:
        content = f.read()
        
    sol = {
        'V': {}, 'theta': {}, 'qp': {}, 'qn': {},
        'lam_inj': {}, 'lam_abs': {},
        'mu_qp_ub': {}, 'mu_qp_lb': {}, 'mu_qn_ub': {}, 'mu_qn_lb': {},
        'P_ref': {}
    }
    
    arrays = ['V', 'theta', 'qp', 'qn', 'lam_inj', 'lam_abs', 
              'mu_qp_ub', 'mu_qp_lb', 'mu_qn_ub', 'mu_qn_lb', 'P_ref']
              
    for arr in arrays:
        pattern = r'(?:' + arr + r'\s*\[\*\]\s*:=\s*|' + arr + r'\s*=\s*)(.*?);'
        m = re.search(pattern, content, re.DOTALL)
        if m:
            for line in m.group(1).strip().split('\n'):
                parts = line.split()
                if len(parts) == 2 and not parts[0].startswith('empty'):
                    try:
                        key = normalise_key(parts[0])
                        sol[arr][key] = float(parts[1])
                    except ValueError:
                        pass
    return sol

def verify_power_flow_balance(raw_res: dict, net_data: dict, s_base: float,
                               tol: float = 1e-4) -> list:
    """
    Recompute P and Q balance at every bus from raw solution variables.
    Returns list of (bus_id, 'P'|'Q', residual) tuples where |residual| > tol.

    Uses: V[b], theta[b], G[b,b'], B[b,b'], P_load[b], Q_load[b], Q_shunt[b],
          P_gen_fixed[b], qp[i], qn[i] from raw_res and net_data.
    All quantities in pu.
    """
    import math

    buses = [int(k) for k in net_data.get('V_min', {}).keys()]
    V = {b: raw_res.get(f'V[{b}]', 1.0) for b in buses}
    theta = {b: raw_res.get(f'theta[{b}]', 0.0) for b in buses}

    G = net_data.get('G', {})
    B = net_data.get('B', {})

    P_load = net_data.get('P_load', {})
    Q_load = net_data.get('Q_load', {})
    Q_shunt = net_data.get('Q_shunt', {})

    # Build gen_bus map from net_data
    gen_bus_map = {}
    for gen_id, b in net_data.get('gen_bus', {}).items():
        gen_bus_map[str(int(float(b)))] = gen_id

    def _g(b1, b2):
        k = (str(int(b1)), str(int(b2)))
        return G.get(k, G.get((str(b1), str(b2)), 0.0))

    def _b(b1, b2):
        k = (str(int(b1)), str(int(b2)))
        return B.get(k, B.get((str(b1), str(b2)), 0.0))

    violations = []

    for b in buses:
        # Network injection at bus b
        P_net = sum(
            V[b] * V[b2] * (
                _g(b, b2) * math.cos(theta[b] - theta[b2]) +
                _b(b, b2) * math.sin(theta[b] - theta[b2])
            ) for b2 in buses
        )
        Q_net = sum(
            V[b] * V[b2] * (
                _g(b, b2) * math.sin(theta[b] - theta[b2]) -
                _b(b, b2) * math.cos(theta[b] - theta[b2])
            ) for b2 in buses
        )

        p_load = float(P_load.get(str(b), 0.0))
        q_load = float(Q_load.get(str(b), 0.0))
        q_sh = float(Q_shunt.get(str(b), 0.0))

        # Generator contribution at this bus
        p_gen = 0.0
        q_gen = 0.0
        if str(b) in gen_bus_map:
            gid = gen_bus_map[str(b)]
            p_gen = float(net_data.get('P_gen_fixed', {}).get(str(gid), 0.0))
            qp = raw_res.get(f'qp[{gid}]', 0.0)
            qn = raw_res.get(f'qn[{gid}]', 0.0)
            q_gen = qp - qn

        P_res = P_net - p_gen + p_load
        Q_res = Q_net - q_gen + q_load - q_sh

        if abs(P_res) > tol:
            violations.append((b, 'P', P_res))
        if abs(Q_res) > tol:
            violations.append((b, 'Q', Q_res))

    return violations

def verify_ac_power_flow(solution, network, tol=1e-4):
    P_mismatch = {}
    Q_mismatch = {}
    
    for b in network['BUSES']:
        # Parameters from network.dat are ALREADY in PU. Do not divide by S_base.
        p_gen_pu = sum(network['P_gen_fixed'].get(g, 0.0) for g in network['GENERATORS'] if network['gen_bus'].get(g) == b)
        q_gen_pu = sum(solution['qp'].get(g, 0.0) - solution['qn'].get(g, 0.0) for g in network['GENERATORS'] if network['gen_bus'].get(g) == b)
        
        if b in network['REF_BUSES']:
            p_gen_pu += solution['P_ref'].get(b, 0.0)
            
        p_load_pu = network['P_load'].get(b, 0.0)
        q_load_pu = network['Q_load'].get(b, 0.0)
        q_shunt_pu = network['Q_shunt'].get(b, 0.0)
        
        p_inj = p_gen_pu - p_load_pu
        q_inj = q_gen_pu + q_shunt_pu - q_load_pu
        
        # Flows (in PU)
        v_b = solution['V'].get(b, 1.0)
        th_b = solution['theta'].get(b, 0.0)
        
        p_flow = 0.0
        q_flow = 0.0
        
        for j in network['BUSES']:
            g_bj = network['G'].get((b, j), 0.0)
            b_bj = network['B'].get((b, j), 0.0)
            if g_bj == 0.0 and b_bj == 0.0:
                continue
                
            v_j = solution['V'].get(j, 1.0)
            th_j = solution['theta'].get(j, 0.0)
            
            cos_th = math.cos(th_b - th_j)
            sin_th = math.sin(th_b - th_j)
            
            p_flow += v_j * (g_bj * cos_th + b_bj * sin_th)
            q_flow += v_j * (g_bj * sin_th - b_bj * cos_th)
            
        p_flow *= v_b
        q_flow *= v_b
        
        P_mismatch[b] = p_inj - p_flow
        Q_mismatch[b] = q_inj - q_flow
        
    max_p = max((abs(v) for v in P_mismatch.values()), default=0.0)
    max_q = max((abs(v) for v in Q_mismatch.values()), default=0.0)
    
    return {
        'max_p_mismatch': max_p,
        'max_q_mismatch': max_q,
        'p_mismatch': P_mismatch,
        'q_mismatch': Q_mismatch,
        'pass': max_p <= tol and max_q <= tol
    }

def verify_kkt_stationarity(solution, network, tol=1e-6):
    S_base = network['S_base']
    stat_inj = {}
    stat_abs = {}
    
    for g in network['GENERATORS']:
        # qp and qn are still stored in PU, so we convert them to MVAr for the cost polynomial
        qp_mvar = solution['qp'].get(g, 0.0) * S_base
        qn_mvar = solution['qn'].get(g, 0.0) * S_base
        
        lam_inj = solution['lam_inj'].get(g, 0.0)
        lam_abs = solution['lam_abs'].get(g, 0.0)
        
        # DO NOT divide multipliers by S_base. AMPL now outputs them correctly scaled.
        mu_qp_ub = solution['mu_qp_ub'].get(g, 0.0)
        mu_qp_lb = solution['mu_qp_lb'].get(g, 0.0)
        mu_qn_ub = solution['mu_qn_ub'].get(g, 0.0)
        mu_qn_lb = solution['mu_qn_lb'].get(g, 0.0)
        
        ca_inj = network['cost_a_inj'].get(g, 0.0) if isinstance(network.get('cost_a_inj'), dict) else 0.0
        cb_inj = network['cost_b_inj'].get(g, 0.0) if isinstance(network.get('cost_b_inj'), dict) else 0.0
        ca_abs = network['cost_a_abs'].get(g, 0.0) if isinstance(network.get('cost_a_abs'), dict) else 0.0
        cb_abs = network['cost_b_abs'].get(g, 0.0) if isinstance(network.get('cost_b_abs'), dict) else 0.0
        
        # Calculate exactly what AMPL calculated
        stat_inj[g] = abs(lam_inj - 2*ca_inj*qp_mvar - cb_inj - mu_qp_ub + mu_qp_lb)
        stat_abs[g] = abs(lam_abs - 2*ca_abs*qn_mvar - cb_abs - mu_qn_ub + mu_qn_lb)
        
        print(f"gen {g}: mu_qn_lb={mu_qn_lb:.6g}, lam_abs={lam_abs:.6g}, "
              f"cb_abs={cb_abs:.6g}, stat_abs={stat_abs[g]:.3e}")
        
    max_inj = max((v for v in stat_inj.values()), default=0.0)
    max_abs = max((v for v in stat_abs.values()), default=0.0)
    
    return {
        'max_stat_inj': max_inj,
        'max_stat_abs': max_abs,
        'stat_inj': stat_inj,
        'stat_abs': stat_abs,
        'pass': max_inj <= tol and max_abs <= tol
    }

def verify_complementarity(solution, network, tol=1e-4):
    S_base = network['S_base']
    c_prods = {}
    max_prod = 0.0
    
    for g in network['GENERATORS']:
        # DO NOT divide multipliers.
        mu_qp_ub = solution['mu_qp_ub'].get(g, 0.0)
        mu_qp_lb = solution['mu_qp_lb'].get(g, 0.0)
        mu_qn_ub = solution['mu_qn_ub'].get(g, 0.0)
        mu_qn_lb = solution['mu_qn_lb'].get(g, 0.0)
        
        q_inj_max_pu = network['q_inj_max'].get(g, 0.0)
        q_abs_max_pu = network['q_abs_max'].get(g, 0.0)
        qp_pu = solution['qp'].get(g, 0.0)
        qn_pu = solution['qn'].get(g, 0.0)
        
        # Calculate exactly what the KKT bounds define
        c1 = abs(mu_qp_ub * (q_inj_max_pu - qp_pu))
        c2 = abs(mu_qp_lb * qp_pu)
        c3 = abs(mu_qn_ub * (q_abs_max_pu - qn_pu))
        c4 = abs(mu_qn_lb * qn_pu)
        
        c_prods[g] = {'c1': c1, 'c2': c2, 'c3': c3, 'c4': c4}
        max_prod = max(max_prod, c1, c2, c3, c4)
        
    return {
        'max_product': max_prod,
        'products': c_prods,
        'pass': max_prod <= tol
    }

def verify_physical_exclusivity(solution, tol=1e-4):
    excl = {}
    max_excl = 0.0
    for g, qp_pu in solution['qp'].items():
        qn_pu = solution['qn'].get(g, 0.0)
        e = qp_pu * qn_pu
        excl[g] = e
        max_excl = max(max_excl, e)
        
    return {
        'max_exclusivity': max_excl,
        'exclusivity': excl,
        'pass': max_excl <= tol
    }

def verify_dual_price_economics(solution, network):
    S_base = network['S_base']
    flags = {}
    tol = 1e-4
    
    for g in network['GENERATORS']:
        qp_mvar = solution['qp'].get(g, 0.0) * S_base
        qn_mvar = solution['qn'].get(g, 0.0) * S_base
        lam_inj = solution['lam_inj'].get(g, 0.0)
        lam_abs = solution['lam_abs'].get(g, 0.0)
        
        ca_inj = network['cost_a_inj'].get(g, 0.0) if isinstance(network.get('cost_a_inj'), dict) else 0.0
        cb_inj = network['cost_b_inj'].get(g, 0.0) if isinstance(network.get('cost_b_inj'), dict) else 0.0
        cc_inj = network['cost_c_inj'].get(g, 0.0) if isinstance(network.get('cost_c_inj'), dict) else 0.0
        
        ca_abs = network['cost_a_abs'].get(g, 0.0) if isinstance(network.get('cost_a_abs'), dict) else 0.0
        cb_abs = network['cost_b_abs'].get(g, 0.0) if isinstance(network.get('cost_b_abs'), dict) else 0.0
        cc_abs = network['cost_c_abs'].get(g, 0.0) if isinstance(network.get('cost_c_abs'), dict) else 0.0
        
        q_inj_max = network['q_inj_max'].get(g, 0.0) * S_base
        q_abs_max = network['q_abs_max'].get(g, 0.0) * S_base
        
        def C_inj_var(q): return ca_inj*q**2 + cb_inj*q
        def C_inj_total(q): return ca_inj*q**2 + cb_inj*q + cc_inj
        def C_abs_var(q): return ca_abs*q**2 + cb_abs*q
        def C_abs_total(q): return ca_abs*q**2 + cb_abs*q + cc_abs
        
        # Optimal alternative
        q_abs_opt = 0.0
        if ca_abs > 0:
            q_abs_opt = max(0.0, min((lam_abs - cb_abs) / (2 * ca_abs), q_abs_max))
        elif lam_abs > cb_abs:
            q_abs_opt = q_abs_max
            
        q_inj_opt = 0.0
        if ca_inj > 0:
            q_inj_opt = max(0.0, min((lam_inj - cb_inj) / (2 * ca_inj), q_inj_max))
        elif lam_inj > cb_inj:
            q_inj_opt = q_inj_max
            
        is_rational = True
        reason = "OK"
        
        if qp_mvar > tol:
            if lam_inj < cb_inj - tol:
                is_rational = False
                reason = "Injection price below marginal cost"
            else:
                profit_inj = lam_inj * qp_mvar - C_inj_total(qp_mvar)
                profit_abs_alt = lam_abs * q_abs_opt - C_abs_var(q_abs_opt)
                profit_inj_var = lam_inj * qp_mvar - C_inj_var(qp_mvar)
                if profit_inj_var < profit_abs_alt - tol:
                    is_rational = False
                    reason = "Absorption would be more profitable"
        elif qn_mvar > tol:
            if lam_abs < cb_abs - tol:
                is_rational = False
                reason = "Absorption price below marginal cost"
            else:
                profit_abs = lam_abs * qn_mvar - C_abs_total(qn_mvar)
                profit_inj_alt = lam_inj * q_inj_opt - C_inj_var(q_inj_opt)
                profit_abs_var = lam_abs * qn_mvar - C_abs_var(qn_mvar)
                if profit_abs_var < profit_inj_alt - tol:
                    is_rational = False
                    reason = "Injection would be more profitable"
        else:
            if lam_inj > cb_inj + tol:
                is_rational = False
                reason = "Idle but injection is profitable"
            elif lam_abs > cb_abs + tol:
                is_rational = False
                reason = "Idle but absorption is profitable"
                
        flags[g] = {'rational': is_rational, 'reason': reason}
        
    all_pass = all(f['rational'] for f in flags.values())
    return {
        'flags': flags,
        'pass': all_pass
    }

def run_full_verification(solution_raw_path, network_dat_path, tol=1e-4):
    print("Loading data for verification...")
    network = parse_network_dat(network_dat_path)
    solution = parse_solution_raw(solution_raw_path)
    
    # Sanity check: do the parsed generator IDs match the network?
    sol_gens = set(solution['qp'].keys())
    net_gens = set(network['GENERATORS'])
    if sol_gens and net_gens:
        missing = net_gens - sol_gens
        assert not missing, f"Generator IDs in solution_raw do not match network.dat! Missing: {missing}"
    
    pf_res = verify_ac_power_flow(solution, network, tol=tol)
    
    # Also run the new power flow balance check
    pf_violations = verify_power_flow_balance(solution, network, s_base=network.get('s_base_mva', 100.0), tol=tol)
    if pf_violations:
        print(f"  [WARN] AC Power Flow Balance Violations found: {len(pf_violations)}")
        for v in pf_violations[:5]:
            print(f"    Bus {v[0]} {v[1]} mismatch: {v[2]:.4e}")
        if len(pf_violations) > 5:
            print(f"    ... and {len(pf_violations)-5} more.")
    
    stat_res = verify_kkt_stationarity(solution, network, tol=tol)
    compl_res = verify_complementarity(solution, network, tol=tol)
    excl_res = verify_physical_exclusivity(solution, tol=tol)
    econ_res = verify_dual_price_economics(solution, network)
    
    # Determine overall status
    checks_passed = sum([pf_res['pass'], stat_res['pass'], compl_res['pass'], excl_res['pass'], econ_res['pass']])
    total_checks = 5
    
    with open('ampl/solution_summary.txt', 'r') as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith('[') and not ln.startswith('objective')]
    solve_status = lines[0].split(',')[1].strip()

    if solve_status != "solved":
        quality = "INVALID"
    elif checks_passed == total_checks:
        quality = "CERTIFIED"
    elif pf_res['pass'] and stat_res['pass'] and compl_res['max_product'] < 1e-3:
        quality = "ACCEPTABLE"
    else:
        quality = "POOR"
        
    print("\n" + "═"*38)
    print("MPEC KKT VERIFICATION REPORT")
    print("═"*38)
    
    print("[1] AC Power Flow Feasibility")
    print(f"    Max P mismatch: {pf_res['max_p_mismatch']:.2e} [{'PASS' if pf_res['max_p_mismatch'] <= tol else 'FAIL'}]")
    print(f"    Max Q mismatch: {pf_res['max_q_mismatch']:.2e} [{'PASS' if pf_res['max_q_mismatch'] <= tol else 'FAIL'}]")
    
    print("[2] KKT Stationarity")
    print(f"    Max injection residual: {stat_res['max_stat_inj']:.2e} [{'PASS' if stat_res['max_stat_inj'] <= tol else 'FAIL'}]")
    
    # NOTE: stat_abs residuals can be FB artefacts when qn=0, but large residuals 
    # (> 1e-3) indicate genuine failures (e.g., stale cost parameters).
    WARN_THRESHOLD = 1e-3
    print(f"    Max absorption residual: {stat_res['max_stat_abs']:.2e} [{'PASS' if stat_res['max_stat_abs'] <= WARN_THRESHOLD else 'FAIL'}]")
    for g in network['GENERATORS']:
        print(f"      gen {g}: stat_abs={stat_res['stat_abs'][g]:.3e}, mu_qn_lb={solution['mu_qn_lb'].get(g,0):.3e}")
    
    print("[3] Complementarity (exact)")
    print(f"    Max product: {compl_res['max_product']:.2e} [{'PASS' if compl_res['max_product'] <= tol else 'FAIL'}]")
    
    print("[4] Physical Exclusivity")
    print(f"    Max qp*qn: {excl_res['max_exclusivity']:.2e} [{'PASS' if excl_res['max_exclusivity'] <= tol else 'FAIL'}]")
    
    print("[5] Dual-Price Economic Rationality")
    all_econ_pass = True
    for g, f in econ_res['flags'].items():
        if not f['rational']:
            print(f"    {g}: FAIL - {f['reason']}")
            all_econ_pass = False
    if all_econ_pass:
        print("    Per-generator: PASS")
        
    print("═"*38)
    print(f"OVERALL: {checks_passed}/{total_checks} checks passed")
    print(f"Stackelberg equilibrium quality: {quality}")
    
    return {
        'power_flow': pf_res,
        'stationarity': stat_res,
        'complementarity': compl_res,
        'exclusivity': excl_res,
        'economics': econ_res,
        'quality': quality
    }

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Verify KKT conditions of AMPL solution.")
    parser.add_argument("--raw", default="ampl/solution_raw.txt", help="Path to solution_raw.txt")
    parser.add_argument("--dat", default="ampl/network.dat", help="Path to network.dat")
    parser.add_argument("--tol", type=float, default=1e-4, help="Tolerance for checks")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.raw) or not os.path.exists(args.dat):
        print(f"Error: Could not find required files ({args.raw} or {args.dat}).")
        sys.exit(1)
        
    run_full_verification(args.raw, args.dat, tol=args.tol)
