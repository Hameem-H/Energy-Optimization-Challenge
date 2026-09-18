import logging
from typing import Sequence
from app.schemas import ScenarioRequest, HourlyPlanEntry, DirectiveInterpretation
from app.directive_engine import build_constraint_arrays

logger = logging.getLogger("gridwise.replay")
TOL = 0.01


def replay_validate(
    hourly_plan: Sequence[HourlyPlanEntry],
    req: ScenarioRequest,
    directives: Sequence[DirectiveInterpretation],
    totals: dict,
) -> None:
    """
    Performs internal verification of the generated 24-hour schedule against all constraints,
    energy conservation laws, battery physical bounds, directives, and recomputed totals.
    Raises ValueError if any assertion is violated.
    """
    if len(hourly_plan) != 24:
        raise ValueError(f"Replay validation failed: expected 24 hourly entries, got {len(hourly_plan)}")

    constraints = build_constraint_arrays(req.hours, req.battery, directives)
    recomputed_total_grid = 0.0
    recomputed_total_cost = 0.0
    recomputed_peak_grid = 0.0

    prev_soc = req.battery.initial_energy_kwh

    for h in range(24):
        entry = hourly_plan[h]
        hour_spec = req.hours[h]

        if entry.hour != h:
            raise ValueError(f"Replay validation failed hour mismatch: index {h} vs entry.hour {entry.hour}")

        # Extract values
        grid = entry.grid_kwh
        solar_used = entry.solar_used_kwh
        battery_kwh = entry.battery_kwh
        soc = entry.battery_energy_after_kwh
        action = entry.battery_action

        chg = battery_kwh if action == "charge" else 0.0
        dis = battery_kwh if action == "discharge" else 0.0

        # 1. Energy conservation: grid + solar_used + discharge == demand + charge
        supply = grid + solar_used + dis
        demand_load = hour_spec.demand_kwh + chg
        if abs(supply - demand_load) > TOL:
            raise ValueError(
                f"Replay validation failed hour {h}: Energy balance discrepancy supply={supply:.4f} vs load={demand_load:.4f}"
            )

        # 2. Solar usage limit
        if solar_used > constraints.effective_solar[h] + TOL:
            raise ValueError(
                f"Replay validation failed hour {h}: solar_used {solar_used:.4f} > effective_solar {constraints.effective_solar[h]:.4f}"
            )

        # 3. Battery State of Charge dynamics
        expected_soc = prev_soc + chg - dis
        if abs(soc - expected_soc) > TOL:
            raise ValueError(
                f"Replay validation failed hour {h}: SoC mismatch {soc:.4f} vs expected {expected_soc:.4f}"
            )

        # 4. Battery bounds and minimum reserve directive
        if soc < constraints.reserve[h] - TOL:
            raise ValueError(
                f"Replay validation failed hour {h}: SoC {soc:.4f} below required reserve {constraints.reserve[h]:.4f}"
            )
        if soc > req.battery.capacity_kwh + TOL:
            raise ValueError(
                f"Replay validation failed hour {h}: SoC {soc:.4f} exceeds capacity {req.battery.capacity_kwh:.4f}"
            )

        # 5. Charge/Discharge rate and window constraints
        if chg > req.battery.max_charge_kwh_per_hour + TOL:
            raise ValueError(f"Replay validation failed hour {h}: charge rate {chg:.4f} exceeds limit")
        if dis > req.battery.max_discharge_kwh_per_hour + TOL:
            raise ValueError(f"Replay validation failed hour {h}: discharge rate {dis:.4f} exceeds limit")

        if constraints.no_charge[h] and chg > TOL:
            raise ValueError(f"Replay validation failed hour {h}: charging during no_charge_window")
        if constraints.no_discharge[h] and dis > TOL:
            raise ValueError(f"Replay validation failed hour {h}: discharging during no_discharge_window")

        # 6. Grid cap window constraint
        if grid > constraints.grid_cap[h] + TOL:
            raise ValueError(
                f"Replay validation failed hour {h}: grid {grid:.4f} exceeds cap {constraints.grid_cap[h]:.4f}"
            )

        recomputed_total_grid += grid
        recomputed_total_cost += grid * hour_spec.tariff_bdt_per_kwh
        if grid > recomputed_peak_grid:
            recomputed_peak_grid = grid

        prev_soc = soc

    # 7. End-of-day neutrality check (LP constraint is soc[23] >= initial; SoC may finish higher)
    if prev_soc + TOL < req.battery.initial_energy_kwh:
        raise ValueError(
            f"Replay validation failed: end-of-day SoC {prev_soc:.4f} < initial SoC {req.battery.initial_energy_kwh:.4f}"
        )

    # 8. Totals check
    if abs(recomputed_total_grid - totals.get("total_grid_kwh", 0.0)) > TOL:
        raise ValueError(
            f"Replay validation failed: total grid kWh mismatch {recomputed_total_grid:.4f} vs {totals.get('total_grid_kwh')}"
        )
    if abs(recomputed_total_cost - totals.get("total_cost_bdt", 0.0)) > TOL:
        raise ValueError(
            f"Replay validation failed: total cost BDT mismatch {recomputed_total_cost:.4f} vs {totals.get('total_cost_bdt')}"
        )
    if abs(recomputed_peak_grid - totals.get("peak_grid_kwh", 0.0)) > TOL:
        raise ValueError(
            f"Replay validation failed: peak grid kWh mismatch {recomputed_peak_grid:.4f} vs {totals.get('peak_grid_kwh')}"
        )

    logger.info("Replay validation passed successfully.")
