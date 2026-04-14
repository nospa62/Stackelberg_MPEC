import pandas as pd
import io
import re
import os

def parse_solution_summary(path: str) -> dict:
    """
    Parse solution_summary.txt and return a dictionary of DataFrames and solve info.
    """
    with open(path, 'r') as f:
        content = f.read()
        
    # Split by section headers
    sections = re.split(r'\n(?=\[)', '\n' + content)
    
    results = {}
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
            
        lines = sec.split('\n')
        header = lines[0].strip('[]')
        csv_data = '\n'.join(lines[1:])
        
        if not csv_data.strip():
            continue
            
        df = pd.read_csv(io.StringIO(csv_data))
        
        if header == 'SOLVE INFO':
            results['solve_info'] = {
                'objective': float(df['objective'].iloc[0]),
                'solve_result': str(df['solve_result'].iloc[0]),
                'eps_final': float(df['eps_smooth_final'].iloc[0]),
                's_base': float(df['s_base_mva'].iloc[0]),
                'procurement_cost_eur': float(df['procurement_cost_eur'].iloc[0]) if 'procurement_cost_eur' in df.columns else float(df['objective'].iloc[0]),
                'tikhonov_eur': float(df['tikhonov_eur'].iloc[0]) if 'tikhonov_eur' in df.columns else 0.0
            }
        elif header == 'PRICES':
            results['prices'] = df
        elif header == 'DISPATCH':
            results['dispatch'] = df
        elif header == 'VOLTAGES':
            results['voltages'] = df
        elif header == 'BRANCH_FLOWS':
            results['branch_flows'] = df
        elif header == 'KKT_MULTIPLIERS':
            results['kkt_multipliers'] = df
            
    return results

def parse_raw_solution(path: str) -> dict:
    """
    Parse solution_raw.txt (AMPL display output format) and return a flat dict.
    """
    raw_data = {}
    with open(path, 'r') as f:
        lines = f.readlines()
        
    current_var = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Scalar variable: TotalPayment = 123.45
        if '=' in line and ':=' not in line:
            parts = line.split('=')
            if len(parts) == 2:
                var_name = parts[0].strip()
                try:
                    raw_data[var_name] = float(parts[1].strip())
                except ValueError:
                    raw_data[var_name] = parts[1].strip()
            continue
            
        # Array variable start: lam_inj [*] :=
        if ':=' in line:
            parts = line.split(':=')
            var_decl = parts[0].strip()
            # Extract variable name before '['
            if '[' in var_decl:
                current_var = var_decl.split('[')[0].strip()
            else:
                current_var = var_decl
            continue
            
        # End of array block
        if line == ';':
            current_var = None
            continue
            
        # Inside array block
        if current_var:
            tokens = line.split()
            if len(tokens) >= 2:
                val = tokens[-1]
                keys = tokens[:-1]
                key_str = ','.join(keys)
                try:
                    raw_data[f"{current_var}[{key_str}]"] = float(val)
                except ValueError:
                    raw_data[f"{current_var}[{key_str}]"] = val
                    
    return raw_data

def _parse_network_dat(path: str) -> dict:
    """
    Parse network.dat returning a dict of {param_name: {key: value}}.
    Scalar params are stored as plain float values.
    1D params: {'1': 0.7, '2': 1.8, ...}
    2D params: {('1','2'): 4.344, ...}
    Uses same logic as 06_results_to_excel.py parse_dat_param for consistency.
    """
    with open(path, 'r') as f:
        content = f.read()

    data = {}

    # --- Scalar parameters ---
    scalar_pat = re.compile(r'param\s+(\w+)\s*:=\s*([^\s;]+)\s*;')
    for m in scalar_pat.finditer(content):
        name, val = m.group(1), m.group(2)
        try:
            data[name] = float(val)
        except ValueError:
            data[name] = val

    # --- Array parameters (1D and 2D) ---
    def normalise(k):
        try:
            return str(int(float(k)))
        except (ValueError, TypeError):
            return str(k)

    array_pat = re.compile(
        r'param\s+(\w+)(?:\s+default\s+[^:=]+)?\s*:=\s*(.*?);', re.DOTALL
    )
    for m in array_pat.finditer(content):
        name = m.group(1)
        if name in data and not isinstance(data.get(name), dict):
            # Scalar already parsed — skip the array match ghost
            continue
        tokens = m.group(2).split()
        d = {}
        i = 0
        while i < len(tokens):
            # Try 2D key first (three tokens: k1, k2, val)
            if i + 2 < len(tokens):
                try:
                    float(tokens[i+2])
                    # tokens[i] and [i+1] are keys, [i+2] is value
                    try:
                        float(tokens[i])    # key1 is numeric
                        float(tokens[i+1])  # key2 is numeric
                        d[(normalise(tokens[i]), normalise(tokens[i+1]))] = float(tokens[i+2])
                        i += 3
                        continue
                    except ValueError:
                        pass
                except (ValueError, IndexError):
                    pass
            # 1D key (two tokens: k, val)
            if i + 1 < len(tokens):
                try:
                    d[normalise(tokens[i])] = float(tokens[i+1])
                    i += 2
                    continue
                except ValueError:
                    pass
            i += 1
        if d:
            data[name] = d

    return data

def compute_market_statistics(results: dict, network_dat_path: str) -> dict:
    """
    Compute aggregate market statistics from the parsed results and network.dat.
    """
    net_data = _parse_network_dat(network_dat_path)
    s_base = results['solve_info']['s_base']
    
    disp = results['dispatch'].copy()
    prices = results['prices'].copy()
    
    # Define an economic threshold (e.g., 1e-5 MVAr)
    DISPATCH_THRESHOLD = 1e-5 
    
    # Filter out economic artifacts for idle generators
    for idx in disp.index:
        if abs(disp.at[idx, 'qp_mvar']) < DISPATCH_THRESHOLD:
            disp.at[idx, 'qp_mvar'] = 0.0
            if idx in prices.index:
                prices.at[idx, 'lam_inj'] = 0.0
                
        if abs(disp.at[idx, 'qn_mvar']) < DISPATCH_THRESHOLD:
            disp.at[idx, 'qn_mvar'] = 0.0
            if idx in prices.index:
                prices.at[idx, 'lam_abs'] = 0.0
                
    tot_q_inj = disp['qp_mvar'].sum()
    tot_q_abs = disp['qn_mvar'].sum()
    
    # Q_shunt and Q_load from net_data (they are in pu in network.dat)
    q_shunt_dict = net_data.get('Q_shunt', {})
    if isinstance(q_shunt_dict, dict):
        q_shunt_pu = sum(float(v) for v in q_shunt_dict.values())
    else:
        q_shunt_pu = 0.0
        
    q_load_dict = net_data.get('Q_load', {})
    if isinstance(q_load_dict, dict):
        q_load_pu = sum(float(v) for v in q_load_dict.values())
    else:
        q_load_pu = 0.0
    
    tot_q_shunt = q_shunt_pu * s_base
    tot_q_load = q_load_pu * s_base
    
    # Reactive power balance: Injected + Shunts = Load + Absorbed_by_lines + Line_losses
    # → Line reactive losses = (Injected + Shunts) - (Load + Absorbed_by_generators)
    bf = results['branch_flows']
    # Reactive losses = sum of (Q_from + Q_to) for each branch
    # Each row has Q_flow_mva measured FROM side. For the receiving side, use -Q_flow_back.
    # Simpler: sum all |Q flows| times sign correction
    q_transformer_absorption = 0.0
    if not bf.empty:
        # For each branch, reactive loss = Q_injected_at_from - Q_received_at_to
        # We only have one-sided Q_flow here. Use:
        # net_reactive_loss = ΣQ_gen + ΣQ_shunt - ΣQ_load (exact balance)
        q_line_losses = (tot_q_inj + tot_q_shunt) - (tot_q_load + tot_q_abs)
        # Flag if suspiciously large (>50% of total Q generation)
        if abs(q_line_losses) > 0.5 * max(tot_q_inj, 1.0):
            print(f"  WARNING: Q balance = {q_line_losses:.1f} MVAr. "
                  f"Network has large transformer reactive absorption — check Y-bus base MVA.")
    else:
        q_line_losses = (tot_q_inj + tot_q_shunt) - (tot_q_load + tot_q_abs)
    
    tot_payment = results['solve_info']['objective']
    procurement_cost = results['solve_info'].get('procurement_cost_eur', tot_payment)
    tikhonov_cost = results['solve_info'].get('tikhonov_eur', 0.0)
    
    avg_inj_price = (results['prices']['lam_inj'] * disp['qp_mvar']).sum() / tot_q_inj if tot_q_inj > 0 else 0.0
    avg_abs_price = (results['prices']['lam_abs'] * disp['qn_mvar']).sum() / tot_q_abs if tot_q_abs > 0 else 0.0
    
    num_inj = (disp['qp_mvar'] > 1e-3).sum()
    num_abs = (disp['qn_mvar'] > 1e-3).sum()
    num_idle = ((disp['qp_mvar'] <= 1e-3) & (disp['qn_mvar'] <= 1e-3)).sum()
    
    max_v_dev = (results['voltages']['V_pu'] - 1.0).abs().max()
    
    bf = results['branch_flows']
    if not bf.empty:
        idx_max_load = bf['loading_pct'].idxmax()
        most_loaded_row = bf.loc[idx_max_load]
        most_loaded_branch = f"{most_loaded_row['from_bus']}->{most_loaded_row['to_bus']} ({most_loaded_row['loading_pct']:.1f}%)"
    else:
        most_loaded_branch = "N/A"
    
    return {
        'total_q_injected_mvar': tot_q_inj,
        'total_q_absorbed_mvar': tot_q_abs,
        'total_q_shunt_mvar': tot_q_shunt,
        'total_q_load_mvar': tot_q_load,
        'net_reactive_line_losses_mvar': q_line_losses,
        'total_payment_eur': tot_payment,
        'procurement_cost_eur': procurement_cost,
        'tikhonov_eur': tikhonov_cost,
        'average_injection_price_eur_mvar': avg_inj_price,
        'average_absorption_price_eur_mvar': avg_abs_price,
        'num_generators_injecting': int(num_inj),
        'num_generators_absorbing': int(num_abs),
        'num_generators_idle': int(num_idle),
        'max_voltage_deviation_pu': max_v_dev,
        'most_loaded_branch': most_loaded_branch
    }

def verify_dual_price_consistency(results: dict, network_dat_path: str = 'ampl/network.dat') -> list:
    """
    Check KKT stationarity of the dual-price solution.
    All quantities in physical units: Q in MVAr, λ/μ in €/MVAr.
    Multipliers in solution_summary.txt are already in €/MVAr.
    cost_a_inj is in €/MVAr² (network.dat convention).
    """
    net_data = _parse_network_dat(network_dat_path)
    s_base = results['solve_info']['s_base']
    
    # Sanity: multipliers should be O(price_cap) not O(price_cap * s_base)
    kkt_df = results['kkt_multipliers']
    assert all(abs(row['mu_qp_ub']) < 1e4 for _, row in kkt_df.iterrows()), \
        "mu_qp_ub values look pu-scaled (>>price_cap). Check C1 fix in .mod."

    # ── FIX: normalise gen_id to string in ALL DataFrames before merging ──
    for key in ('dispatch', 'prices', 'kkt_multipliers'):
        results[key]['gen_id'] = results[key]['gen_id'].astype(str)

    df = (results['dispatch']
          .merge(results['prices'],         on='gen_id')
          .merge(results['kkt_multipliers'], on='gen_id'))

    # ── FIX: drop rows where any quantity is NaN (merge artefacts) ──
    df = df.dropna(subset=['qp_mvar', 'qn_mvar', 'lam_inj', 'lam_abs',
                            'mu_qp_ub', 'mu_qp_lb', 'mu_qn_ub', 'mu_qn_lb'])

    violations = []

    cost_a_inj = {str(k): v for k, v in net_data.get('cost_a_inj', {}).items()}
    cost_b_inj = {str(k): v for k, v in net_data.get('cost_b_inj', {}).items()}
    cost_a_abs = {str(k): v for k, v in net_data.get('cost_a_abs', {}).items()}
    cost_b_abs = {str(k): v for k, v in net_data.get('cost_b_abs', {}).items()}

    # Tikhonov delta_reg read from .dat for idle-generator threshold
    delta_reg = float(net_data.get('delta_reg', 1e-6))
    # Tolerance: allow stationarity residual up to 1e-3 + Tikhonov artefact
    stat_tol = 1e-3 + delta_reg * s_base

    for _, row in df.iterrows():
        gen = str(row['gen_id'])

        # All quantities in MVAr / €/MVAr — NO /s_base divisions
        qp_mvar = float(row['qp_mvar'])
        qn_mvar = float(row['qn_mvar'])
        lam_inj = float(row['lam_inj'])
        lam_abs = float(row['lam_abs'])

        ca_inj = float(cost_a_inj.get(gen, 0.0))
        cb_inj = float(cost_b_inj.get(gen, 0.0))
        ca_abs = float(cost_a_abs.get(gen, 0.0))
        cb_abs = float(cost_b_abs.get(gen, 0.0))

        # Multipliers already in €/MVAr — do NOT divide by s_base
        mu_qp_ub = float(row['mu_qp_ub'])
        mu_qp_lb = float(row['mu_qp_lb'])
        mu_qn_ub = float(row['mu_qn_ub'])
        mu_qn_lb = float(row['mu_qn_lb'])

        if qp_mvar > 1e-3:  # Injecting
            expected_inj = 2.0 * ca_inj * qp_mvar + cb_inj + mu_qp_ub - mu_qp_lb
            residual_inj = abs(lam_inj - expected_inj)
            if residual_inj > stat_tol:
                violations.append(
                    f"Gen {gen} INJ stationarity FAIL: lam={lam_inj:.4f} €/MVAr, "
                    f"expected={expected_inj:.4f} €/MVAr, residual={residual_inj:.4e} €/MVAr"
                )

        elif qn_mvar > 1e-3:  # Absorbing
            expected_abs = 2.0 * ca_abs * qn_mvar + cb_abs + mu_qn_ub - mu_qn_lb
            residual_abs = abs(lam_abs - expected_abs)
            if residual_abs > stat_tol:
                violations.append(
                    f"Gen {gen} ABS stationarity FAIL: lam={lam_abs:.4f} €/MVAr, "
                    f"expected={expected_abs:.4f} €/MVAr, residual={residual_abs:.4e} €/MVAr"
                )

        else:  # Idle generator
            # FIX: Use stat_tol (includes Tikhonov artefact) instead of bare 1e-4
            # to prevent false positives caused by delta_reg pushing lam above cb
            if lam_inj > cb_inj + stat_tol:
                violations.append(
                    f"Gen {gen} IDLE but lam_inj ({lam_inj:.4f}) >> cb_inj ({cb_inj:.4f})"
                )
            if lam_abs > cb_abs + stat_tol:
                violations.append(
                    f"Gen {gen} IDLE but lam_abs ({lam_abs:.4f}) >> cb_abs ({cb_abs:.4f})"
                )

    return violations

if __name__ == "__main__":
    # Simple test block (requires actual output files to run successfully)
    if os.path.exists("ampl/solution_summary.txt") and os.path.exists("ampl/solution_raw.txt"):
        print("Parsing solution files...")
        res = parse_solution_summary("ampl/solution_summary.txt")
        raw_res = parse_raw_solution("ampl/solution_raw.txt")
        
        print("\n--- Solve Info ---")
        print(res['solve_info'])
        
        if os.path.exists("ampl/network.dat"):
            print("\n--- Market Statistics ---")
            stats = compute_market_statistics(res, "ampl/network.dat")
            for k, v in stats.items():
                print(f"{k}: {v}")
                
            print("\n--- Dual Price Consistency ---")
            violations = verify_dual_price_consistency(res, "ampl/network.dat")
            if not violations:
                print("All dual prices are economically consistent! [PASS]")
            else:
                for v in violations:
                    print(f"[FAIL] {v}")
    else:
        print("Run AMPL first to generate solution_summary.txt and solution_raw.txt.")
