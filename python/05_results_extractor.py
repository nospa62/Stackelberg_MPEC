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
                's_base': float(df['s_base_mva'].iloc[0])
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
    Helper function to parse network.dat for parameters needed in statistics and verification.
    """
    data = {}
    with open(path, 'r') as f:
        content = f.read()

    # Parse 1D array parameters
    param_pattern = re.compile(r'param\s+(\w+)(?:\s+default\s+[^:=]+)?\s*:=\s*(.*?);', re.DOTALL)
    for match in param_pattern.finditer(content):
        param_name = match.group(1)
        param_body = match.group(2)
        
        lines = param_body.strip().split('\n')
        param_dict = {}
        for line in lines:
            tokens = line.strip().split()
            if len(tokens) == 2:
                try:
                    param_dict[tokens[0]] = float(tokens[1])
                except ValueError:
                    param_dict[tokens[0]] = tokens[1]
        data[param_name] = param_dict
        
    # Parse scalar parameters
    scalar_pattern = re.compile(r'param\s+(\w+)\s*:=\s*([^\s;]+)\s*;')
    for match in scalar_pattern.finditer(content):
        param_name = match.group(1)
        val_str = match.group(2).strip()
        try:
            if param_name not in data or not isinstance(data[param_name], dict):
                data[param_name] = float(val_str)
        except ValueError:
            if param_name not in data or not isinstance(data[param_name], dict):
                data[param_name] = val_str
            
    return data

def compute_market_statistics(results: dict, network_dat_path: str) -> dict:
    """
    Compute aggregate market statistics from the parsed results and network.dat.
    """
    net_data = _parse_network_dat(network_dat_path)
    s_base = results['solve_info']['s_base']
    
    disp = results['dispatch']
    prices = results['prices']
    
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
    
    # Net grid Q losses (Net Injected + Shunts - Load - Absorbed)
    q_losses = tot_q_inj + tot_q_shunt - tot_q_load - tot_q_abs
    
    tot_payment = results['solve_info']['objective']
    
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
        'net_grid_q_losses_mvar': -q_losses,
        'total_payment_eur': tot_payment,
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
    Check that the dual-price solution is economically consistent.
    Returns a list of violation strings (empty if consistent).
    """
    net_data = _parse_network_dat(network_dat_path)
    s_base = results['solve_info']['s_base']
    
    violations = []
    
    # Merge dataframes for easy iteration
    df = results['dispatch'].merge(results['prices'], on='gen_id')
    df = df.merge(results['kkt_multipliers'], on='gen_id')
    
    cost_a_inj = net_data.get('cost_a_inj', {})
    if not isinstance(cost_a_inj, dict): cost_a_inj = {}
    cost_b_inj = net_data.get('cost_b_inj', {})
    if not isinstance(cost_b_inj, dict): cost_b_inj = {}
    cost_a_abs = net_data.get('cost_a_abs', {})
    if not isinstance(cost_a_abs, dict): cost_a_abs = {}
    cost_b_abs = net_data.get('cost_b_abs', {})
    if not isinstance(cost_b_abs, dict): cost_b_abs = {}
    
    for _, row in df.iterrows():
        gen_id_val = row['gen_id']
        try:
            gen = str(int(float(gen_id_val)))
        except ValueError:
            gen = str(gen_id_val)
        qp_pu = row['qp_mvar'] / s_base
        qn_pu = row['qn_mvar'] / s_base
        lam_inj = row['lam_inj']
        lam_abs = row['lam_abs']
        
        ca_inj = float(cost_a_inj.get(gen, 0.0))
        cb_inj = float(cost_b_inj.get(gen, 0.0))
        ca_abs = float(cost_a_abs.get(gen, 0.0))
        cb_abs = float(cost_b_abs.get(gen, 0.0))
        
        mu_qp_ub = row['mu_qp_ub'] / s_base
        mu_qp_lb = row['mu_qp_lb'] / s_base
        mu_qn_ub = row['mu_qn_ub'] / s_base
        mu_qn_lb = row['mu_qn_lb'] / s_base
        
        # a) Injecting
        if qp_pu * s_base > 1e-3:
            if lam_inj < cb_inj - 1e-4:
                violations.append(f"{gen} injecting but lam_inj ({lam_inj:.4f}) < cost_b_inj ({cb_inj:.4f})")
            
            expected_lam = 2 * ca_inj * (qp_pu * s_base) + cb_inj + mu_qp_ub - mu_qp_lb
            if abs(lam_inj - expected_lam) > 1e-3:
                violations.append(f"{gen} injecting: stationarity violated. lam_inj={lam_inj:.4f}, expected={expected_lam:.4f}")
                
        # b) Absorbing
        elif qn_pu * s_base > 1e-3:
            if lam_abs < cb_abs - 1e-4:
                violations.append(f"{gen} absorbing but lam_abs ({lam_abs:.4f}) < cost_b_abs ({cb_abs:.4f})")
            
            expected_lam = 2 * ca_abs * (qn_pu * s_base) + cb_abs + mu_qn_ub - mu_qn_lb
            if abs(lam_abs - expected_lam) > 1e-3:
                violations.append(f"{gen} absorbing: stationarity violated. lam_abs={lam_abs:.4f}, expected={expected_lam:.4f}")
                
        # c) Idle
        else:
            if lam_inj > cb_inj + 1e-4:
                violations.append(f"{gen} idle but lam_inj ({lam_inj:.4f}) > cost_b_inj ({cb_inj:.4f})")
            if lam_abs > cb_abs + 1e-4:
                violations.append(f"{gen} idle but lam_abs ({lam_abs:.4f}) > cost_b_abs ({cb_abs:.4f})")
                
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
