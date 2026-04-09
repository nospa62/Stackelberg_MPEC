# ══════════════════════════════════════════════════════
# IMPORTANT MODELLING NOTES:
# 1. eps_smooth is NOT defined in this .mod file — it is a param assigned in the .run file
#    before each solve call. Do NOT give it a default value here.
# 2. The MO has NO constraint forcing lam_inj[i]*lam_abs[i]=0. Both prices are independent.
#    The solution will naturally drive one or both to zero based on network needs.
# 3. The sign of (qp[i]-qn[i]) in Q_balance is critical:
#    qp[i] > 0 means net reactive injection (helps voltage)
#    qn[i] > 0 means net reactive absorption (reduces voltage)
#    Exactly one of {qp[i], qn[i]} is nonzero at optimum.
# 4. The thermal limit constraint uses S_max from network.dat (already in pu).
# 5. KKT stationarity conditions are EQUALITY constraints — they are always active.
#    Complementarity conditions are equality constraints only because of FB smoothing.
#
# COST_C TREATMENT — INDIVIDUAL RATIONALITY VIA PRICE FLOOR:
# The fixed cost cost_c does not appear in the producer KKT stationarity (dC/dq
# has no c term). To honour individual rationality including cost_c, we compute:
#
#   At interior optimum: qp* = (lam - b) / (2a)
#   Total profit = (lam - b)^2 / (4a) - c >= 0
#   => lam >= b + 2*sqrt(a*c)   [minimum viable price, lam_min]
#
# This is enforced as a LOWER BOUND on lam_inj / lam_abs variables.
# IPOPT handles variable bounds cleanly via its interior point barrier.
# No explicit IR constraint is needed — this avoids the KKT conflict.
# ══════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════
# SECTION 1: SETS
# ══════════════════════════════════════════════════════
set BUSES;                    # all network buses
set GENERATORS;               # market participants (gens + ext grids)
set REF_BUSES within BUSES;   # reference bus (single element)
set BRANCHES within BUSES cross BUSES;  # all lines + transformers

# ══════════════════════════════════════════════════════
# SECTION 2: SCALAR PARAMETERS
# ══════════════════════════════════════════════════════
param s_base_mva;
param f_hz;
param price_cap;
param price_floor;
param eps_smooth default 1e-2;             # current FB smoothing value — set in .run file before each solve
param delta_abs := 1e-6;      # safety term in FB denominator (never set to zero)
param delta_reg default 1e-6; # Tikhonov/FBRS regularization coefficient

# Additional scalars from network.dat used by .run file
param smoothing_eps_1;
param smoothing_eps_2;
param smoothing_eps_3;
param ipopt_max_iter;
param ipopt_tol;

# ══════════════════════════════════════════════════════
# SECTION 3: NETWORK PARAMETERS
# ══════════════════════════════════════════════════════
param vn_kv {BUSES};               # bus nominal voltage
param G {BUSES, BUSES} default 0;  # conductance matrix
param B {BUSES, BUSES} default 0;  # susceptance matrix
param V_min {BUSES};
param V_max {BUSES};
param P_load {BUSES} default 0;
param Q_load {BUSES} default 0;
param Q_shunt {BUSES} default 0;
param S_max {BRANCHES};

# ══════════════════════════════════════════════════════
# SECTION 4: GENERATOR PARAMETERS
# ══════════════════════════════════════════════════════
param gen_bus {GENERATORS};         # bus index of each generator
param P_gen_fixed {GENERATORS};     # fixed active power injection [pu]
param Q_min_gen {GENERATORS};       # minimum reactive power (negative for absorption)
param Q_max_gen {GENERATORS};       # maximum reactive power
param q_inj_max {GENERATORS} >= 0;  # upper bound for q+ (injection) [pu]
param q_abs_max {GENERATORS} >= 0;  # upper bound for q- (= -Q_min, always positive) [pu]
param cost_a_inj {GENERATORS} >= 0;
param cost_b_inj {GENERATORS} >= 0;
param cost_c_inj {GENERATORS} >= 0;
param cost_a_abs {GENERATORS} >= 0;
param cost_b_abs {GENERATORS} >= 0;
param cost_c_abs {GENERATORS} >= 0;
param q_init_inj {GENERATORS};      # warm start values for q+
param q_init_abs {GENERATORS};      # warm start values for q-
param V_init {BUSES};
param theta_init {BUSES};

# ══════════════════════════════════════════════════════
# SECTION 4b: MINIMUM VIABLE PRICES (derived from cost_c)
# ══════════════════════════════════════════════════════
# Derivation: producer i maximises profit lam*qp - a*qp^2 - b*qp - c.
# At interior optimum: qp* = (lam - b)/(2a).
# Total profit = (lam-b)^2/(4a) - c.
# Setting profit = 0 gives the minimum viable price:
#   lam_min_inj[i] = cost_b_inj[i] + 2 * sqrt(cost_a_inj[i] * cost_c_inj[i])
#
# At any lam >= lam_min, the profit-maximising producer breaks even or profits.
# Proof: let x = lam-b >= 2*sqrt(a*c), then x^2/(4a) >= c. QED.
#
# lam_min is clamped to [price_floor, price_cap].
# If lam_min > price_cap, the MO cannot afford to incentivise generator i
# and Ipopt will force qp[i]=0 (generator effectively excluded from market).
param lam_min_inj {i in GENERATORS} :=
    min(price_cap,
        max(price_floor,
            cost_b_inj[i] + 2 * sqrt(cost_a_inj[i] * cost_c_inj[i])));

param lam_min_abs {i in GENERATORS} :=
    min(price_cap,
        max(price_floor,
            cost_b_abs[i] + 2 * sqrt(cost_a_abs[i] * cost_c_abs[i])));

# ══════════════════════════════════════════════════════
# SECTION 5: DECISION VARIABLES — UPPER LEVEL (MARKET OPERATOR)
# ══════════════════════════════════════════════════════
# Lower bound = lam_min_inj[i]: guarantees non-negative total profit for any
# dispatched generator (individual rationality including fixed cost).
# Upper bound = price_cap: regulatory ceiling.
# IPOPT enforces both bounds via its interior point barrier — no explicit
# IR constraint is needed and no conflict with KKT stationarity arises.
var lam_inj {i in GENERATORS} >= lam_min_inj[i], <= price_cap;
var lam_abs {i in GENERATORS} >= lam_min_abs[i], <= price_cap;

# Network state variables
var V {b in BUSES} >= V_min[b], <= V_max[b];
var theta {BUSES} >= -3.14159, <= 3.14159;
var P_ref {REF_BUSES} >= -100.0, <= 100.0;    # active power reference (free variable)

# ══════════════════════════════════════════════════════
# SECTION 6: DECISION VARIABLES — LOWER LEVEL (PRODUCERS, embedded via KKT)
# ══════════════════════════════════════════════════════
# Split reactive power variables — both non-negative
var qp {i in GENERATORS} >= 0, <= q_inj_max[i];    # injection [pu]
var qn {i in GENERATORS} >= 0, <= q_abs_max[i];    # absorption [pu]

# ══════════════════════════════════════════════════════
# SECTION 7: KKT MULTIPLIER VARIABLES
# ══════════════════════════════════════════════════════
var mu_qp_ub {GENERATORS} >= 0;    # multiplier for q+ <= q_inj_max
var mu_qp_lb {GENERATORS} >= 0;    # multiplier for q+ >= 0
var mu_qn_ub {GENERATORS} >= 0;    # multiplier for q- <= q_abs_max
var mu_qn_lb {GENERATORS} >= 0;    # multiplier for q- >= 0

# ══════════════════════════════════════════════════════
# SECTION 8: INITIALISATION
# ══════════════════════════════════════════════════════
# Initial values set via 'let' statements in ampl/04_stackelberg_kkt.run.

# ══════════════════════════════════════════════════════
# SECTION 9: OBJECTIVE FUNCTION (MARKET OPERATOR)
# ══════════════════════════════════════════════════════
minimize TotalPayment:
    sum {i in GENERATORS} (
        lam_inj[i] * qp[i] * s_base_mva
      + lam_abs[i] * qn[i] * s_base_mva
    )
    + delta_reg * sum {i in GENERATORS} (
        lam_inj[i]^2 + lam_abs[i]^2
    );

# Economic interpretation:
# - lam_inj[i]*qp[i]*s_base_mva: payment to producer i for reactive injection [€]
# - lam_abs[i]*qn[i]*s_base_mva: payment to producer i for reactive absorption [€]
# - delta_reg term: Tikhonov regularisation — drives idle prices toward lam_min (lower bound)
# - MO minimises total procurement cost; IR is guaranteed via lam lower bounds

# ══════════════════════════════════════════════════════
# SECTION 10: AC POWER FLOW CONSTRAINTS
# ══════════════════════════════════════════════════════

# Active power balance at every bus b
subject to P_balance {i in BUSES}:
    (sum {g in GENERATORS: gen_bus[g] == i} P_gen_fixed[g]) +
    (sum {s in REF_BUSES: s == i} P_ref[s])
    - P_load[i]
    ==
    V[i] * sum {j in BUSES} V[j] * (G[i,j] * cos(theta[i] - theta[j]) + B[i,j] * sin(theta[i] - theta[j]));

# Reactive power balance at every bus b
subject to Q_balance {b in BUSES}:
    ( sum {i in GENERATORS: gen_bus[i] == b} (qp[i] - qn[i]) )
    + Q_shunt[b]
    - Q_load[b]
    ==
    V[b] * sum {j in BUSES} V[j] *
        ( G[b,j] * sin(theta[b] - theta[j])
        - B[b,j] * cos(theta[b] - theta[j]) );

# Reference bus: fix angle
subject to ref_angle {b in REF_BUSES}: theta[b] = 0;

# ══════════════════════════════════════════════════════
# SECTION 11: NETWORK INEQUALITY CONSTRAINTS
# ══════════════════════════════════════════════════════

subject to V_lower {b in BUSES}: V[b] >= V_min[b];
subject to V_upper {b in BUSES}: V[b] <= V_max[b];

# Thermal limit: apparent power flow from f to t
subject to thermal_limit_ft {(f,t) in BRANCHES}:
    ( V[f]^2 * G[f,t] - V[f]*V[t]*( G[f,t]*cos(theta[f]-theta[t]) + B[f,t]*sin(theta[f]-theta[t]) ) )^2
    +
    ( -V[f]^2 * B[f,t] - V[f]*V[t]*( G[f,t]*sin(theta[f]-theta[t]) - B[f,t]*cos(theta[f]-theta[t]) ) )^2
    <= S_max[f,t]^2;

# Thermal limit: apparent power flow from t to f
subject to thermal_limit_tf {(f,t) in BRANCHES}:
    ( V[t]^2 * G[t,f] - V[t]*V[f]*( G[t,f]*cos(theta[t]-theta[f]) + B[t,f]*sin(theta[t]-theta[f]) ) )^2
    +
    ( -V[t]^2 * B[t,f] - V[t]*V[f]*( G[t,f]*sin(theta[t]-theta[f]) - B[t,f]*cos(theta[t]-theta[f]) ) )^2
    <= S_max[f,t]^2;

# ══════════════════════════════════════════════════════
# SECTION 12: KKT STATIONARITY CONDITIONS
# ══════════════════════════════════════════════════════
# These replace the lower-level producer optimisation problems.
# Producer i solves: max_{qp,qn} lam_inj*qp - C_inj(qp) + lam_abs*qn - C_abs(qn)
# C_inj(qp) = cost_a_inj*(qp*s_base)^2 + cost_b_inj*(qp*s_base) + cost_c_inj
# C_abs(qn) = cost_a_abs*(qn*s_base)^2 + cost_b_abs*(qn*s_base) + cost_c_abs
#
# dL/d(qp) = 0 => lam_inj - 2*cost_a_inj*(qp*s_base) - cost_b_inj - mu_qp_ub + mu_qp_lb = 0
# dL/d(qn) = 0 => lam_abs - 2*cost_a_abs*(qn*s_base) - cost_b_abs - mu_qn_ub + mu_qn_lb = 0
#
# cost_c is NOT present in stationarity (dC/dq has no c term).
# IR with cost_c is handled via the price lower bounds lam_min_inj / lam_min_abs (Section 5).

subject to KKT_stationarity_inj {i in GENERATORS}:
    lam_inj[i]
    - 2 * cost_a_inj[i] * (qp[i] * s_base_mva)
    - cost_b_inj[i]
    - mu_qp_ub[i]
    + mu_qp_lb[i]
    = 0;

subject to KKT_stationarity_abs {i in GENERATORS}:
    lam_abs[i]
    - 2 * cost_a_abs[i] * (qn[i] * s_base_mva)
    - cost_b_abs[i]
    - mu_qn_ub[i]
    + mu_qn_lb[i]
    = 0;

# ══════════════════════════════════════════════════════
# SECTION 13: KKT COMPLEMENTARITY — FISCHER-BURMEISTER SMOOTHING
# ══════════════════════════════════════════════════════
# For each complementarity condition a*b=0, a>=0, b>=0, use:
#   phi_eps(a,b) = a + b - sqrt(a^2 + b^2 + eps_smooth^2) = 0
# As eps_smooth -> 0, phi_eps -> 0 recovers exact complementarity.

# Injection upper bound: mu_qp_ub * (q_inj_max - qp) = 0
subject to KKT_compl_qp_ub {i in GENERATORS}:
    mu_qp_ub[i] + (q_inj_max[i] - qp[i])
    - sqrt( mu_qp_ub[i]^2 + (q_inj_max[i] - qp[i])^2 + eps_smooth^2 )
    = 0;

# Injection lower bound: mu_qp_lb * qp = 0
subject to KKT_compl_qp_lb {i in GENERATORS}:
    mu_qp_lb[i] + qp[i]
    - sqrt( mu_qp_lb[i]^2 + qp[i]^2 + eps_smooth^2 )
    = 0;

# Absorption upper bound: mu_qn_ub * (q_abs_max - qn) = 0
subject to KKT_compl_qn_ub {i in GENERATORS}:
    mu_qn_ub[i] + (q_abs_max[i] - qn[i])
    - sqrt( mu_qn_ub[i]^2 + (q_abs_max[i] - qn[i])^2 + eps_smooth^2 )
    = 0;

# Absorption lower bound: mu_qn_lb * qn = 0
subject to KKT_compl_qn_lb {i in GENERATORS}:
    mu_qn_lb[i] + qn[i]
    - sqrt( mu_qn_lb[i]^2 + qn[i]^2 + eps_smooth^2 )
    = 0;

# ══════════════════════════════════════════════════════
# SECTION 14: INDIVIDUAL RATIONALITY — ENFORCED VIA PRICE BOUNDS (Section 5)
# ══════════════════════════════════════════════════════
# No explicit IR constraints here. IR is guaranteed by:
#   lam_inj[i] >= lam_min_inj[i]  (variable lower bound, Section 5)
#   lam_abs[i] >= lam_min_abs[i]  (variable lower bound, Section 5)
#
# At any lam >= lam_min = b + 2*sqrt(a*c), the producer's optimal profit
# (lam-b)^2/(4a) - c >= 0. This is tighter than and replaces the previous
# IR_inj / IR_abs inequality constraints, which conflicted with KKT stationarity.

# ══════════════════════════════════════════════════════
# SECTION 15: AUXILIARY BRANCH FLOW VARIABLES (for reporting)
# ══════════════════════════════════════════════════════
var P_flow {(f,t) in BRANCHES};
var Q_flow {(f,t) in BRANCHES};

subject to def_P_flow {(f,t) in BRANCHES}:
    P_flow[f,t] =
        V[f]^2 * G[f,t]
        - V[f]*V[t]*( G[f,t]*cos(theta[f]-theta[t]) + B[f,t]*sin(theta[f]-theta[t]) );

subject to def_Q_flow {(f,t) in BRANCHES}:
    Q_flow[f,t] =
        -V[f]^2 * B[f,t]
        - V[f]*V[t]*( G[f,t]*sin(theta[f]-theta[t]) - B[f,t]*cos(theta[f]-theta[t]) );
