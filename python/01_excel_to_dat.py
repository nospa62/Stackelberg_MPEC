import pandas as pd
import numpy as np
import os
import sys

def clean_str(val):
    if pd.isna(val):
        return "none"
    return str(val).strip().replace(" ", "_").replace("'", "").replace('"', '')

def is_true(val):
    if pd.isna(val):
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val == 1
    return str(val).strip().lower() in ['true', '1', 'yes', 'y']

def get_val(row, col, default=0.0):
    """Safely extracts values from Pandas rows, overriding NaNs with defaults."""
    val = row.get(col)
    return default if pd.isna(val) else float(val)

def process_excel_to_dat(excel_path, dat_path):
    print(f"Reading {excel_path}...")
    if not os.path.exists(excel_path):
        print(f"Error: {excel_path} not found.")
        return

    # Read GameParameters
    df_params = pd.read_excel(excel_path, sheet_name='GameParameters', header=3)
    df_params = df_params.dropna(subset=['Parameter'])
    params = {}
    for _, row in df_params.iterrows():
        if not pd.isna(row['Value']):
            params[str(row['Parameter']).strip()] = row['Value']

    s_base_mva = float(params.get('s_base_mva', 100.0))
    f_hz = float(params.get('f_hz', 50.0))
    
    # Read other sheets
    buses = pd.read_excel(excel_path, sheet_name='Buses', header=3)
    lines = pd.read_excel(excel_path, sheet_name='Lines', header=3)
    trafos = pd.read_excel(excel_path, sheet_name='Transformers', header=3)
    loads = pd.read_excel(excel_path, sheet_name='Loads', header=3)
    gens = pd.read_excel(excel_path, sheet_name='Generators', header=3)
    extgrids = pd.read_excel(excel_path, sheet_name='ExternalGrids', header=3)
    shunts = pd.read_excel(excel_path, sheet_name='Shunts', header=3)

    # Filter in_service
    buses = buses[buses['in_service'].apply(is_true)].copy()
    lines = lines[lines['in_service'].apply(is_true)].copy()
    trafos = trafos[trafos['in_service'].apply(is_true)].copy()
    loads = loads[loads['in_service'].apply(is_true)].copy()
    gens = gens[gens['in_service'].apply(is_true)].copy()
    extgrids = extgrids[extgrids['in_service'].apply(is_true)].copy()
    shunts = shunts[shunts['in_service'].apply(is_true)].copy()

    # Bus dictionary for quick lookup
    bus_vn = dict(zip([int(b) for b in buses['bus_id']], buses['vn_kv']))
    bus_ids = sorted(list(set([int(b) for b in buses['bus_id']])))
    n_buses = len(bus_ids)
    bus_idx = {b: i for i, b in enumerate(bus_ids)}

    # Y-bus construction
    Y_bus = np.zeros((n_buses, n_buses), dtype=complex)

    for _, row in lines.iterrows():
        f = int(row['from_bus'])
        t = int(row['to_bus'])
        if f not in bus_idx or t not in bus_idx:
            continue
        
        L = float(row['length_km'])
        r_km = float(row['r_ohm_per_km'])
        x_km = float(row['x_ohm_per_km'])
        c_km = get_val(row, 'c_nf_per_km', 0.0)
        parallel = get_val(row, 'parallel', 1.0)
        
        Z_base_f = (bus_vn[f]**2) / s_base_mva
        z_series = (r_km + 1j * x_km) * L
        if abs(z_series) < 1e-6:
            z_series = 1e-6 + 1j*1e-6 # avoid division by zero
        z_pu = z_series / Z_base_f
        y_series = 1.0 / z_pu
        
        b_shunt_half = (c_km * L * 1e-9) * (2 * np.pi * f_hz) * Z_base_f / 2.0
        
        i, j = bus_idx[f], bus_idx[t]
        
        Y_bus[i, i] += (y_series + 1j * b_shunt_half) * parallel
        Y_bus[j, j] += (y_series + 1j * b_shunt_half) * parallel
        Y_bus[i, j] -= y_series * parallel
        Y_bus[j, i] -= y_series * parallel

    for _, row in trafos.iterrows():
        h = int(row['hv_bus'])
        l = int(row['lv_bus'])
        if h not in bus_idx or l not in bus_idx:
            continue
            
        sn_mva = float(row['sn_mva'])
        vn_hv = float(row['vn_hv_kv'])
        vn_lv = float(row['vn_lv_kv'])
        vk_pct = float(row['vk_percent'])
        vkr_pct = float(row['vkr_percent'])
        pfe_kw = get_val(row, 'pfe_kw', 0.0)
        i0_pct = get_val(row, 'i0_percent', 0.0)
        shift_deg = get_val(row, 'shift_degree', 0.0)
        parallel = get_val(row, 'parallel', 1.0)
        
        # Corrected Transformer per-unit conversion
        Z_base_h = (bus_vn[h]**2) / s_base_mva
        
        # z_k
        r_k = vkr_pct / 100.0
        x_k = np.sqrt(max(0, (vk_pct/100.0)**2 - r_k**2))
        z_k = r_k + 1j * x_k
        
        voltage_correction = (vn_hv / bus_vn[h])**2
        z_pu = z_k * (s_base_mva / sn_mva) * voltage_correction
        if abs(z_pu) < 1e-6:
            z_pu = 1e-6 + 1j*1e-6
        y_t = 1.0 / z_pu
        
        # y_m
        if i0_pct == 0 and pfe_kw == 0:
            y_m = 0j
        else:
            g_m = pfe_kw / (1000.0 * sn_mva)
            y_m_mag = i0_pct / 100.0
            b_m = -np.sqrt(max(0, y_m_mag**2 - g_m**2))
            y_m = (g_m + 1j * b_m) * (sn_mva / s_base_mva) # pu on system base
            
        i, j = bus_idx[h], bus_idx[l]
        
        # 1. Extract Tap Parameters from Excel
        t_pos = get_val(row, 'tap_pos', 0.0)
        t_step = get_val(row, 'tap_step_percent', 0.0)
        shift_deg = get_val(row, 'shift_degree', 0.0)
        
        # 2. Calculate the Off-Nominal Tap Ratio (a)
        # Formula: a = 1 + (pos * step/100)
        # Neutral (pos=0) results in a = 1.0
        tap_ratio = 1.0 + (t_pos * (t_step / 100.0))
        
        # 3. Safety Check: Prevent Division by Zero
        if abs(tap_ratio) < 1e-4:
            tap_ratio = 1.0  # Fallback to neutral if data is invalid
            print(f"Warning: Invalid tap_ratio at Trafo {row.get('name')}. Resetting to 1.0.")

        # 4. Calculate Complex Shift (phi)
        shift_rad = np.radians(shift_deg)
        # k = a * exp(j * phi)
        k = tap_ratio * (np.cos(shift_rad) + 1j * np.sin(shift_rad))
        
        # 5. Apply Asymmetric Y-bus Logic
        g_t = np.real(y_t)
        b_t = np.imag(y_t)
        g_m = np.real(y_m)
        b_m = np.imag(y_m)
        a = tap_ratio
        
        # User formula:
        # G[f,f] += g_t/a^2, G[f,t] -= g_t/a, G[t,f] -= g_t/a, G[t,t] += g_t
        # B[f,f] += b_t/a^2 + b_c/2, B[f,t] -= b_t/a, B[t,f] -= b_t/a, B[t,t] += b_t + b_c/2
        
        Y_bus[i, i] += (g_t / (a**2) + 1j * (b_t / (a**2) + b_m / 2.0)) * parallel
        Y_bus[j, j] += (g_t + 1j * (b_t + b_m / 2.0)) * parallel
        Y_bus[i, j] -= (g_t / a + 1j * (b_t / a)) * parallel
        Y_bus[j, i] -= (g_t / a + 1j * (b_t / a)) * parallel

    G = np.real(Y_bus)
    B = np.imag(Y_bus)

    # Check row sums
    # FIX: Removed. In AC literature, row sums equal nodal shunts, not zero.
    # row_sums = np.sum(Y_bus, axis=1)
    # for i, rsum in enumerate(row_sums):
    #     if abs(np.real(rsum)) > 1e-4 or abs(np.imag(rsum)) > 1e-4:
    #         print(f"Warning: Y_bus row sum for bus {bus_ids[i]} is not zero: {rsum}")

    # Load aggregation
    P_load = {b: 0.0 for b in bus_ids}
    Q_load = {b: 0.0 for b in bus_ids}
    for _, row in loads.iterrows():
        b = int(row['bus_id'])
        if b in P_load:
            scaling = get_val(row, 'scaling', 1.0)
            P_load[b] += float(row['p_mw']) * scaling / s_base_mva
            Q_load[b] += float(row['q_mvar']) * scaling / s_base_mva

    # Shunt aggregation
    Q_shunt = {b: 0.0 for b in bus_ids}
    for _, row in shunts.iterrows():
        b = int(row['bus_id'])
        if b in Q_shunt:
            step = get_val(row, 'step', 1.0)
            Q_shunt[b] += float(row['q_mvar']) * step / s_base_mva

    # Generator set construction
    gen_list = []
    gen_id_counter = 0
    
    for _, row in gens.iterrows():
        gen_list.append({
            'gen_id': gen_id_counter,
            'bus_id': int(row['bus_id']),
            'p_mw': get_val(row, 'p_mw', 0.0),
            'vm_pu': float(row['vm_pu']),
            'min_q_mvar': get_val(row, 'min_q_mvar', -500.0),
            'max_q_mvar': get_val(row, 'max_q_mvar', 500.0),
            'cost_a_inj': get_val(row, 'cost_a_inj', 0.0),
            'cost_b_inj': get_val(row, 'cost_b_inj', 0.0),
            'cost_c_inj': get_val(row, 'cost_c_inj', 0.0),
            'cost_a_abs': get_val(row, 'cost_a_abs', 0.0),
            'cost_b_abs': get_val(row, 'cost_b_abs', 0.0),
            'cost_c_abs': get_val(row, 'cost_c_abs', 0.0),
            'q_init_mvar': get_val(row, 'q_init_mvar', 0.0),
            'is_ext': False
        })
        gen_id_counter += 1
        
    ref_bus_id = None
    ref_v_init = 1.0
    
    for _, row in extgrids.iterrows():
        if is_true(row.get('in_service', False)):
            ref_bus_id = int(row['bus_id'])
            ref_v_init = float(row['vm_pu'])
            break

    if ref_bus_id is None and len(bus_ids) > 0:
        ref_bus_id = bus_ids[0] # Fallback

    # Branch set
    branch_dict = {}
    for _, row in lines.iterrows():
        f = int(row['from_bus'])
        t = int(row['to_bus'])
        if f not in bus_idx or t not in bus_idx:
            continue
        s_max = row.get('s_max_mva')
        if pd.isna(s_max):
            s_max = np.sqrt(3) * float(row['max_i_ka']) * bus_vn[f]
        
        pair = tuple(sorted((f, t)))
        if pair in branch_dict:
            branch_dict[pair] += float(s_max) / s_base_mva
        else:
            branch_dict[pair] = float(s_max) / s_base_mva
        
    for _, row in trafos.iterrows():
        h = int(row['hv_bus'])
        l = int(row['lv_bus'])
        if h not in bus_idx or l not in bus_idx:
            continue
        s_max = float(row['sn_mva'])
        
        pair = tuple(sorted((h, l)))
        if pair in branch_dict:
            branch_dict[pair] += s_max / s_base_mva
        else:
            branch_dict[pair] = s_max / s_base_mva

    branches = [(k[0], k[1], v) for k, v in branch_dict.items()]

    # Write DAT file
    print(f"Writing {dat_path}...")
    os.makedirs(os.path.dirname(dat_path), exist_ok=True)
    
    with open(dat_path, 'w') as f:
        f.write("# ==========================================\n")
        f.write("# Auto-generated AMPL Data File\n")
        f.write("# ==========================================\n\n")
        
        # 1. Scalar params
        f.write("# --- SCALAR SYSTEM PARAMETERS ---\n")
        f.write(f"param s_base_mva := {s_base_mva};\n")
        f.write(f"param f_hz := {f_hz};\n")
        f.write(f"param price_cap := {params.get('price_cap', 1000.0)};\n")
        f.write(f"param price_floor := {params.get('price_floor', 0.0)};\n")
        f.write(f"param smoothing_eps_1 := {params.get('smoothing_eps_1', 1e-2)};\n")
        f.write(f"param smoothing_eps_2 := {params.get('smoothing_eps_2', 1e-4)};\n")
        f.write(f"param smoothing_eps_3 := {params.get('smoothing_eps_3', 1e-6)};\n")
        f.write(f"param ipopt_max_iter := {params.get('ipopt_max_iter', 3000)};\n")
        f.write(f"param ipopt_tol := {params.get('ipopt_tol', 1e-6)};\n\n")
        
        # 2. set BUSES
        f.write("# --- SETS ---\n")
        f.write(f"set BUSES := {' '.join(map(str, bus_ids))} ;\n")
        
        # 3. set GENERATORS
        gen_ids = [g['gen_id'] for g in gen_list]
        f.write(f"set GENERATORS := {' '.join(map(str, gen_ids))} ;\n")
        
        # 4. set REF_BUSES
        f.write(f"set REF_BUSES := {ref_bus_id} ;\n")
        
        # 5. set BRANCHES
        branch_str = " ".join([f"({br[0]},{br[1]})" for br in branches])
        f.write(f"set BRANCHES := {branch_str} ;\n\n")
        
        # 6. param vn_kv
        f.write("# --- BUS PARAMETERS ---\n")
        f.write("param vn_kv :=\n")
        for b in bus_ids:
            f.write(f"{b} {bus_vn[b]}\n")
        f.write(";\n\n")
        
        # 7. param V_min, V_max
        f.write("param V_min :=\n")
        for _, row in buses.iterrows():
            # Safely get value, default to 0.95 if missing
            v_min = get_val(row, 'v_min_pu', 0.95)
            # Enforce strict 0.95 limit even if Excel has 0.9
            if v_min < 0.95: 
                v_min = 0.95
            f.write(f"{row['bus_id']} {v_min}\n")
        f.write(";\n\n")
        
        f.write("param V_max :=\n")
        for _, row in buses.iterrows():
            # Safely get value, default to 1.05 if missing
            v_max = get_val(row, 'v_max_pu', 1.05)
            # Enforce strict 1.05 limit even if Excel has 1.1
            if v_max > 1.05: 
                v_max = 1.05
            f.write(f"{row['bus_id']} {v_max}\n")
        f.write(";\n\n")
        
        # 9. param G
        f.write("# --- Y-BUS MATRICES ---\n")
        f.write("param G :=\n")
        for i, b1 in enumerate(bus_ids):
            for j, b2 in enumerate(bus_ids):
                val = G[i, j]
                # FIX: Always explicitly write the diagonal structural elements
                if abs(val) > 1e-8 or i == j:
                    f.write(f"{b1} {b2} {val:.8f}\n")
        f.write(";\n\n")
        
        # 10. param B
        f.write("param B :=\n")
        for i, b1 in enumerate(bus_ids):
            for j, b2 in enumerate(bus_ids):
                val = B[i, j]
                # FIX: Always explicitly write the diagonal structural elements
                if abs(val) > 1e-8 or i == j:
                    f.write(f"{b1} {b2} {val:.8f}\n")
        f.write(";\n\n")
        
        # 11. param P_load
        f.write("# --- LOAD AND SHUNT PARAMETERS ---\n")
        f.write("param P_load :=\n")
        for b in bus_ids:
            if abs(P_load[b]) > 1e-8:
                f.write(f"{b} {P_load[b]:.6f}\n")
        f.write(";\n\n")
        
        # 12. param Q_load
        f.write("param Q_load :=\n")
        for b in bus_ids:
            if abs(Q_load[b]) > 1e-8:
                f.write(f"{b} {Q_load[b]:.6f}\n")
        f.write(";\n\n")
        
        # 13. param Q_shunt
        f.write("param Q_shunt :=\n")
        for b in bus_ids:
            if abs(Q_shunt[b]) > 1e-8:
                f.write(f"{b} {Q_shunt[b]:.6f}\n")
        f.write(";\n\n")
        
        # 14. param S_max
        f.write("# --- BRANCH PARAMETERS ---\n")
        f.write("param S_max :=\n")
        for br in branches:
            f.write(f"{br[0]} {br[1]} {br[2]:.6f}\n")
        f.write(";\n\n")
        
        # 15. param gen_bus
        f.write("# --- GENERATOR PARAMETERS ---\n")
        f.write("param gen_bus :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['bus_id']}\n")
        f.write(";\n\n")
        
        # 16. param P_gen_fixed
        f.write("param P_gen_fixed :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['p_mw']/s_base_mva:.6f}\n")
        f.write(";\n\n")
        
        # 17. param Q_min_gen
        f.write("param Q_min_gen :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['min_q_mvar']/s_base_mva:.6f}\n")
        f.write(";\n\n")
        
        # 18. param Q_max_gen
        f.write("param Q_max_gen :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['max_q_mvar']/s_base_mva:.6f}\n")
        f.write(";\n\n")
        
        # 19. param q_inj_max
        f.write("param q_inj_max :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {max(0, g['max_q_mvar']/s_base_mva):.6f}\n")
        f.write(";\n\n")
        
        # 20. param q_abs_max
        f.write("param q_abs_max :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {max(0, -g['min_q_mvar']/s_base_mva):.6f}\n")
        f.write(";\n\n")
        
        # 21. param cost_a_inj, cost_b_inj, cost_c_inj
        f.write("param cost_a_inj :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['cost_a_inj']}\n")
        f.write(";\n\n")
        
        f.write("param cost_b_inj :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['cost_b_inj']}\n")
        f.write(";\n\n")
        
        f.write("param cost_c_inj :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['cost_c_inj']}\n")
        f.write(";\n\n")
        
        # 22. param cost_a_abs, cost_b_abs, cost_c_abs
        f.write("param cost_a_abs :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['cost_a_abs']}\n")
        f.write(";\n\n")
        
        f.write("param cost_b_abs :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['cost_b_abs']}\n")
        f.write(";\n\n")
        
        f.write("param cost_c_abs :=\n")
        for g in gen_list:
            f.write(f"{g['gen_id']} {g['cost_c_abs']}\n")
        f.write(";\n\n")
        
        # 23. param q_init_inj
        f.write("# --- INITIALIZATION PARAMETERS ---\n")
        f.write("param q_init_inj :=\n")
        for g in gen_list:
            q_init = g['q_init_mvar'] / s_base_mva
            f.write(f"{g['gen_id']} {max(0, q_init):.6f}\n")
        f.write(";\n\n")
        
        # 24. param q_init_abs
        f.write("param q_init_abs :=\n")
        for g in gen_list:
            q_init = g['q_init_mvar'] / s_base_mva
            f.write(f"{g['gen_id']} {max(0, -q_init):.6f}\n")
        f.write(";\n\n")
        
        # 25. param V_init
        f.write("param V_init :=\n")
        for b in bus_ids:
            # find if there's a generator here
            v_init = 1.0
            for g in gen_list:
                if g['bus_id'] == b:
                    v_init = g['vm_pu']
                    break
            if b == ref_bus_id:
                v_init = ref_v_init
            f.write(f"{b} {v_init:.6f}\n")
        f.write(";\n\n")
        
        # 26. param theta_init
        f.write("param theta_init :=\n")
        for b in bus_ids:
            f.write(f"{b} 0.0\n")
        f.write(";\n\n")

    # Validation and Summary
    print("\n--- VALIDATION AND SUMMARY ---")
    print(f"Number of buses: {n_buses}")
    print(f"Number of generators: {len(gen_list)}")
    print(f"Number of branches: {len(branches)}")
    print(f"Number of loads: {len(loads)}")
    print(f"Number of shunts: {len(shunts)}")
    print(f"Reference bus ID: {ref_bus_id}, Init Voltage: {ref_v_init} pu")
    
    try:
        cond_num = np.linalg.cond(Y_bus)
        print(f"Y_bus condition number: {cond_num:.2e}")
        if cond_num > 1e6:
            print("WARNING: Y_bus is singular or near-singular.")
    except Exception as e:
        print(f"WARNING: Could not compute Y_bus condition number ({e}). Y_bus might be singular.")
        
    total_p_load = sum(P_load.values()) * s_base_mva
    total_q_load = sum(Q_load.values()) * s_base_mva
    print(f"Total Load: {total_p_load:.2f} MW, {total_q_load:.2f} MVAr")
    
    total_q_shunt = sum(Q_shunt.values()) * s_base_mva
    print(f"Total Shunt: {total_q_shunt:.2f} MVAr")
    
    print("\nGenerator Q Ranges (MVAr):")
    for g in gen_list:
        print(f"  Gen {g['gen_id']} (Bus {g['bus_id']}): [{g['min_q_mvar']:.2f}, {g['max_q_mvar']:.2f}]")
        if g['min_q_mvar'] >= 0:
            print(f"  WARNING: Gen {g['gen_id']} has Q_min >= 0 (cannot absorb). Check data.")

    print(f"\nSuccessfully wrote {dat_path}")

if __name__ == '__main__':
    excel_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'CIGRE_HV_Network_Input.xlsx')
    dat_file = os.path.join(os.path.dirname(__file__), '..', 'ampl', 'network.dat')
    process_excel_to_dat(excel_file, dat_file)
