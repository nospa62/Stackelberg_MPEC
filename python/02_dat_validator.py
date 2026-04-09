import os
import sys
import re
from collections import defaultdict

class Validator:
    def __init__(self):
        self.passes = 0
        self.warns = 0
        self.fails = 0
        self.results = []
        
    def check(self, condition, msg_pass, msg_fail, is_warn=False):
        if condition:
            self.passes += 1
            self.results.append(f"[PASS] {msg_pass}")
        else:
            if is_warn:
                self.warns += 1
                self.results.append(f"[WARN] {msg_fail}")
            else:
                self.fails += 1
                self.results.append(f"[FAIL] {msg_fail}")

def parse_dat_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Remove comments
    content = re.sub(r'#.*', '', content)
    
    statements = content.split(';')
    
    scalars = {}
    sets = {}
    params_1d = defaultdict(dict)
    params_2d = defaultdict(dict)
    
    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue
            
        if stmt.startswith('set '):
            parts = stmt.split(':=')
            name = parts[0].replace('set ', '').strip()
            vals_str = parts[1].strip() if len(parts) > 1 else ""
            if name == 'BRANCHES':
                tuples = re.findall(r'\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)\)', vals_str)
                sets[name] = [(int(float(f)), int(float(t))) for f, t in tuples]
            else:
                sets[name] = [int(float(x)) for x in vals_str.split()]
                
        elif stmt.startswith('param '):
            parts = stmt.split(':=')
            header = parts[0].replace('param ', '').strip()
            if len(parts) == 1:
                continue
            vals_str = parts[1].strip()
            
            if len(vals_str.split()) == 1 and '\n' not in vals_str:
                scalars[header] = float(vals_str)
            else:
                lines = vals_str.split('\n')
                for line in lines:
                    tokens = line.split()
                    if not tokens:
                        continue
                    if len(tokens) == 2:
                        params_1d[header][int(float(tokens[0]))] = float(tokens[1])
                    elif len(tokens) == 3:
                        params_2d[header][(int(float(tokens[0])), int(float(tokens[1])))] = float(tokens[2])
                        
    return scalars, sets, params_1d, params_2d

def validate_network_dat(dat_path):
    print(f"Validating {dat_path}...\n")
    if not os.path.exists(dat_path):
        print(f"VALIDATION FAILED — {dat_path} not found.")
        sys.exit(1)
        
    scalars, sets, p1d, p2d = parse_dat_file(dat_path)
    v = Validator()
    
    buses = set(sets.get('BUSES', []))
    gens = set(sets.get('GENERATORS', []))
    ref_buses = sets.get('REF_BUSES', [])
    branches = sets.get('BRANCHES', [])
    
    # STRUCTURAL CHECKS
    # 1. All gen_bus values point to bus IDs that exist in set BUSES.
    gen_bus = p1d.get('gen_bus', {})
    invalid_gen_buses = [b for b in gen_bus.values() if b not in buses]
    v.check(len(invalid_gen_buses) == 0, 
            "All gen_bus values exist in BUSES.", 
            f"Found gen_bus values not in BUSES: {invalid_gen_buses}")
            
    # 2. All BRANCHES reference bus IDs that exist in set BUSES.
    invalid_branches = [(f, t) for f, t in branches if f not in buses or t not in buses]
    v.check(len(invalid_branches) == 0,
            "All BRANCHES reference valid BUSES.",
            f"Invalid branches found: {invalid_branches}")
            
    # 3. REF_BUSES is a subset of BUSES and has exactly one element.
    v.check(len(ref_buses) == 1 and ref_buses[0] in buses,
            "REF_BUSES has exactly one valid bus.",
            f"REF_BUSES invalid: {ref_buses}")
            
    # 4. Every generator in GENERATORS has exactly one entry in gen_bus.
    missing_gens = [g for g in gens if g not in gen_bus]
    v.check(len(missing_gens) == 0,
            "Every generator has a gen_bus entry.",
            f"Generators missing gen_bus: {missing_gens}")
            
    # 5. No duplicate entries in BUSES or GENERATORS.
    buses_list = sets.get('BUSES', [])
    gens_list = sets.get('GENERATORS', [])
    v.check(len(buses_list) == len(set(buses_list)) and len(gens_list) == len(set(gens_list)),
            "No duplicate entries in BUSES or GENERATORS.",
            "Duplicates found in BUSES or GENERATORS.")
            
    # PARAMETER CHECKS
    # 6. V_min[b] < V_max[b] for all buses.
    v_min = p1d.get('V_min', {})
    v_max = p1d.get('V_max', {})
    invalid_v_limits = [b for b in buses if v_min.get(b, 0) >= v_max.get(b, 0)]
    v.check(len(invalid_v_limits) == 0,
            "V_min < V_max for all buses.",
            f"Invalid voltage limits for buses: {invalid_v_limits}")
            
    # 8. q_inj_max[i] > 0 for all generators (can inject).
    q_inj_max = p1d.get('q_inj_max', {})
    invalid_q_inj = [g for g in gens if q_inj_max.get(g, 0) <= 0]
    v.check(len(invalid_q_inj) == 0,
            "q_inj_max > 0 for all generators.",
            f"Generators with q_inj_max <= 0: {invalid_q_inj}", is_warn=True)
            
    # 9. q_abs_max[i] >= 0 for all generators.
    q_abs_max = p1d.get('q_abs_max', {})
    invalid_q_abs = [g for g in gens if q_abs_max.get(g, 0) < 0]
    v.check(len(invalid_q_abs) == 0,
            "q_abs_max >= 0 for all generators.",
            f"Generators with q_abs_max < 0: {invalid_q_abs}")
            
    # 10. cost_a_inj[i] > 0 and cost_a_abs[i] > 0 for all generators (strict convexity).
    cost_a_inj = p1d.get('cost_a_inj', {})
    cost_a_abs = p1d.get('cost_a_abs', {})
    invalid_cost_a = [g for g in gens if cost_a_inj.get(g, 0) <= 0 or cost_a_abs.get(g, 0) <= 0]
    v.check(len(invalid_cost_a) == 0,
            "cost_a_inj > 0 and cost_a_abs > 0 for all generators.",
            f"Generators lacking strict convexity: {invalid_cost_a}")
            
    # 11. P_gen_fixed[i] >= 0 for all generators.
    p_gen_fixed = p1d.get('P_gen_fixed', {})
    invalid_p_gen = [g for g in gens if p_gen_fixed.get(g, 0) < 0]
    v.check(len(invalid_p_gen) == 0,
            "P_gen_fixed >= 0 for all generators.",
            f"Generators with negative P_gen_fixed: {invalid_p_gen}")
            
    # 12. S_max > 0 for all branches.
    s_max = p2d.get('S_max', {})
    invalid_s_max = [br for br in branches if s_max.get(br, 0) <= 0]
    v.check(len(invalid_s_max) == 0,
            "S_max > 0 for all branches.",
            f"Branches with S_max <= 0: {invalid_s_max}")
            
    # 13. price_cap > 0, price_floor = 0.
    price_cap = scalars.get('price_cap', 0)
    price_floor = scalars.get('price_floor', -1)
    v.check(price_cap > 0 and price_floor == 0,
            "price_cap > 0 and price_floor == 0.",
            f"Invalid price limits: cap={price_cap}, floor={price_floor}")
            
    # 14. smoothing_eps_1 > smoothing_eps_2 > smoothing_eps_3 > 0.
    eps1 = scalars.get('smoothing_eps_1', 0)
    eps2 = scalars.get('smoothing_eps_2', 0)
    eps3 = scalars.get('smoothing_eps_3', 0)
    v.check(eps1 > eps2 > eps3 > 0,
            "Smoothing epsilons strictly decreasing and positive.",
            f"Invalid epsilons: {eps1}, {eps2}, {eps3}")
            
    # 15. q_init_inj[i] in [0, q_inj_max[i]] for all i.
    q_init_inj = p1d.get('q_init_inj', {})
    invalid_q_init_inj = [g for g in gens if not (0 <= q_init_inj.get(g, 0) <= q_inj_max.get(g, 0) + 1e-6)]
    v.check(len(invalid_q_init_inj) == 0,
            "q_init_inj within bounds.",
            f"Generators with invalid q_init_inj: {invalid_q_init_inj}", is_warn=True)
            
    # 16. q_init_abs[i] in [0, q_abs_max[i]] for all i.
    q_init_abs = p1d.get('q_init_abs', {})
    invalid_q_init_abs = [g for g in gens if not (0 <= q_init_abs.get(g, 0) <= q_abs_max.get(g, 0) + 1e-6)]
    v.check(len(invalid_q_init_abs) == 0,
            "q_init_abs within bounds.",
            f"Generators with invalid q_init_abs: {invalid_q_init_abs}", is_warn=True)
            
    # NETWORK CONSISTENCY CHECKS
    # 17. Y_bus symmetry
    G = p2d.get('G', {})
    B = p2d.get('B', {})
    asym_G = []
    asym_B = []
    for b in buses:
        for j in buses:
            if abs(G.get((b, j), 0) - G.get((j, b), 0)) > 1e-8:
                asym_G.append((b, j))
            if abs(B.get((b, j), 0) - B.get((j, b), 0)) > 1e-8:
                asym_B.append((b, j))
    v.check(len(asym_G) == 0 and len(asym_B) == 0,
            "Y_bus is symmetric.",
            f"Y_bus asymmetry found. G: {len(asym_G)} pairs, B: {len(asym_B)} pairs.", is_warn=True)
            
    # 18. Y_bus row sum check
    # FIX: Removed. In AC literature, row sums equal nodal shunts, not zero.
    # row_sum_G_fails = []
    # for b in buses:
    #     sum_G = sum(G.get((b, j), 0) for j in buses)
    #     if abs(sum_G) > 1e-4:
    #         row_sum_G_fails.append((b, sum_G))
    #         
    # v.check(len(row_sum_G_fails) == 0,
    #         "Y_bus row sums for G are approximately 0.",
    #         f"G row sum != 0 for buses: {row_sum_G_fails}", is_warn=True)
            
    # 19. Active power balance feasibility check
    total_p_gen = sum(p_gen_fixed.get(g, 0) for g in gens)
    p_load = p1d.get('P_load', {})
    total_p_load = sum(p_load.get(b, 0) for b in buses)
    imbalance = total_p_gen - total_p_load
    v.check(abs(imbalance) <= 0.5,
            f"Active power balance feasible (imbalance: {imbalance:.3f} pu).",
            f"Large active power imbalance: {imbalance:.3f} pu (Slack must provide this).", is_warn=True)
            
    # 20. Reactive power balance feasibility check
    q_shunt = p1d.get('Q_shunt', {})
    total_q_shunt = sum(q_shunt.get(b, 0) for b in buses)
    total_q_inj_max = sum(q_inj_max.get(g, 0) for g in gens)
    q_load = p1d.get('Q_load', {})
    total_q_load = sum(q_load.get(b, 0) for b in buses)
    
    v.check(total_q_load <= total_q_shunt + total_q_inj_max,
            f"Reactive power balance feasible (Load: {total_q_load:.2f}, Max Supply: {total_q_shunt + total_q_inj_max:.2f}).",
            f"Q_load ({total_q_load:.2f}) exceeds max supply ({total_q_shunt + total_q_inj_max:.2f}).", is_warn=True)
            
    # DUAL-PRICE SPECIFIC CHECKS
    # 21. For each generator, verify that both injection and absorption cost functions are defined.
    cost_b_inj = p1d.get('cost_b_inj', {})
    cost_c_inj = p1d.get('cost_c_inj', {})
    cost_b_abs = p1d.get('cost_b_abs', {})
    cost_c_abs = p1d.get('cost_c_abs', {})
    
    missing_costs = []
    for g in gens:
        if (g not in cost_a_inj or g not in cost_b_inj or g not in cost_c_inj or
            g not in cost_a_abs or g not in cost_b_abs or g not in cost_c_abs):
            missing_costs.append(g)
            
    v.check(len(missing_costs) == 0,
            "All 6 cost parameters defined for all generators.",
            f"Generators missing cost parameters: {missing_costs}")
            
    # 22. Verify that the deadband for each producer is well-defined.
    print("\n--- Producer Deadbands ---")
    deadband_fails = []
    max_b = 0
    for g in gens:
        a_inj = cost_a_inj.get(g, 1)
        b_inj = cost_b_inj.get(g, 0)
        a_abs = cost_a_abs.get(g, 1)
        b_abs = cost_b_abs.get(g, 0)
        
        max_b = max(max_b, b_inj, b_abs)
        
        if a_inj > 0 and a_abs > 0:
            db_inj = b_inj / (2 * a_inj)
            db_abs = b_abs / (2 * a_abs)
            print(f"  Gen {g}: Inj DB = {db_inj:.3f}, Abs DB = {db_abs:.3f}")
            if db_inj < 0 or db_abs < 0:
                deadband_fails.append(g)
        else:
            deadband_fails.append(g)
            
    v.check(len(deadband_fails) == 0,
            "Deadbands are well-defined and non-negative.",
            f"Invalid deadbands for generators: {deadband_fails}", is_warn=True)
            
    # 23. Verify price_cap > max(b_inj[i], b_abs[i]) for all i.
    v.check(price_cap > max_b,
            f"price_cap ({price_cap}) > max b_coeff ({max_b}).",
            f"price_cap ({price_cap}) is too low for max b_coeff ({max_b}).")
            
    # SUMMARY
    print("\n--- VALIDATION REPORT ---")
    for res in v.results:
        print(res)
        
    print(f"\n{v.passes} checks passed, {v.warns} warnings, {v.fails} failures.")
    
    if v.fails > 0:
        print("VALIDATION FAILED — do not run AMPL")
        sys.exit(1)
    elif v.warns > 0:
        print("VALIDATION PASSED WITH WARNINGS — review before running.")
    else:
        print("VALIDATION PASSED — safe to run AMPL.")

if __name__ == '__main__':
    dat_file = os.path.join(os.path.dirname(__file__), '..', 'ampl', 'network.dat')
    validate_network_dat(dat_file)
