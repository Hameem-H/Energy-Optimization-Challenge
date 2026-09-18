import pulp
from typing import Sequence, Tuple
from app.schemas import HourEntry, Battery, HourlyPlanEntry
from app.directive_engine import ConstraintArrays

TOLERANCE = 1e-4


def solve_energy_optimization(
    hours: Sequence[HourEntry],
    battery: Battery,
    constraints: ConstraintArrays,
) -> Tuple[list[HourlyPlanEntry], dict]:
    """
    Solves the 24-hour linear program using PuLP (CBC solver) to minimize total grid cost.
    Returns (hourly_plan, totals_dict).
    """
    model = pulp.LpProblem("GridWise_Energy_Optimization", pulp.LpMinimize)

    # Variables for each hour h=0..23
    grid = [pulp.LpVariable(f"grid_{h}", lowBound=0) for h in range(24)]
    solar_used = [pulp.LpVariable(f"solar_used_{h}", lowBound=0) for h in range(24)]
    charge = [pulp.LpVariable(f"charge_{h}", lowBound=0) for h in range(24)]
    discharge = [pulp.LpVariable(f"discharge_{h}", lowBound=0) for h in range(24)]
    soc = [pulp.LpVariable(f"soc_{h}", lowBound=0) for h in range(24)]

    # Objective: minimize total cost in BDT
    model += pulp.lpSum([grid[h] * hours[h].tariff_bdt_per_kwh for h in range(24)])

    # Constraints per hour
    for h in range(24):
        h_demand = hours[h].demand_kwh
        h_solar_eff = constraints.effective_solar[h]
        h_reserve = constraints.reserve[h]
        h_grid_cap = constraints.grid_cap[h]

        # 1. Solar usage limit
        model += solar_used[h] <= h_solar_eff

        # 2. Energy supply == demand + charging balance
        model += grid[h] + solar_used[h] + discharge[h] == h_demand + charge[h]

        # 3. State of Charge dynamics
        prev_soc = battery.initial_energy_kwh if h == 0 else soc[h - 1]
        model += soc[h] == prev_soc + charge[h] - discharge[h]

        # 4. Battery state of charge bounds & directive reserve
        model += soc[h] >= h_reserve
        model += soc[h] <= battery.capacity_kwh

        # 5. Charge rate limit & window directive
        max_chg = 0.0 if constraints.no_charge[h] else battery.max_charge_kwh_per_hour
        model += charge[h] <= max_chg

        # 6. Discharge rate limit & window directive
        max_dis = 0.0 if constraints.no_discharge[h] else battery.max_discharge_kwh_per_hour
        model += discharge[h] <= max_dis

        # 7. Grid cap directive
        if h_grid_cap < float("inf"):
            model += grid[h] <= h_grid_cap

    # 8. End of day neutrality: final SOC must be >= initial (at least restore starting level)
    model += soc[23] >= battery.initial_energy_kwh

    # Solve using CBC quietly
    solver = pulp.PULP_CBC_CMD(msg=False)
    solver_status = model.solve(solver)

    if pulp.LpStatus[solver_status] != "Optimal":
        status_str = pulp.LpStatus[solver_status]
        raise ValueError(
            f"Energy optimization failed ({status_str}). "
            "Directive constraints may conflict with battery physics or grid limits. "
            "Ensure no_charge/no_discharge windows allow enough flexibility to meet demand."
        )


    hourly_plan: list[HourlyPlanEntry] = []
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0

    for h in range(24):
        val_grid = max(0.0, float(pulp.value(grid[h])))
        val_solar = max(0.0, float(pulp.value(solar_used[h])))
        val_chg = max(0.0, float(pulp.value(charge[h])))
        val_dis = max(0.0, float(pulp.value(discharge[h])))

        # Handle potential degenerate simultaneous charge/discharge
        if val_chg > TOLERANCE and val_dis > TOLERANCE:
            if val_chg >= val_dis:
                val_chg -= val_dis
                val_dis = 0.0
            else:
                val_dis -= val_chg
                val_chg = 0.0

        if val_chg > TOLERANCE:
            action = "charge"
            action_kwh = val_chg
        elif val_dis > TOLERANCE:
            action = "discharge"
            action_kwh = val_dis
        else:
            action = "idle"
            action_kwh = 0.0

        val_soc = float(pulp.value(soc[h]))

        # Round values for display / contract consistency (round to 4 decimal places)
        grid_r = round(val_grid, 4)
        solar_r = round(val_solar, 4)
        action_kwh_r = round(action_kwh, 4)
        soc_r = round(val_soc, 4)

        hourly_plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=grid_r,
                solar_used_kwh=solar_r,
                battery_action=action,
                battery_kwh=action_kwh_r,
                battery_energy_after_kwh=soc_r,
            )
        )

        total_grid += grid_r
        total_cost += grid_r * hours[h].tariff_bdt_per_kwh
        if grid_r > peak_grid:
            peak_grid = grid_r

    totals = {
        "total_grid_kwh": round(total_grid, 4),
        "total_cost_bdt": round(total_cost, 4),
        "peak_grid_kwh": round(peak_grid, 4),
    }

    return hourly_plan, totals
