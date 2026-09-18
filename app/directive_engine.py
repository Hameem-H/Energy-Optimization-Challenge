from dataclasses import dataclass
from typing import Sequence
from app.schemas import HourEntry, Battery, DirectiveInterpretation


@dataclass
class ConstraintArrays:
    effective_solar: list[float]  # 24 floats
    reserve: list[float]          # 24 floats
    no_charge: list[bool]         # 24 bools
    no_discharge: list[bool]      # 24 bools
    grid_cap: list[float]         # 24 floats


def build_constraint_arrays(
    hours: Sequence[HourEntry],
    battery: Battery,
    directives: Sequence[DirectiveInterpretation],
) -> ConstraintArrays:
    """
    Transforms scenario hours, battery spec, and validated directive interpretations
    into 24-element hourly constraint arrays for solver & validator.
    """
    effective_solar = [float(h.solar_kwh) for h in hours]
    reserve = [float(battery.minimum_energy_kwh)] * 24
    no_charge = [False] * 24
    no_discharge = [False] * 24
    grid_cap = [float("inf")] * 24

    for d in directives:
        if not d.applies or d.directive_type == "no_op" or not d.structured_adjustment:
            continue

        adj = d.structured_adjustment
        hours_affected = adj.get("hours", [])
        if not isinstance(hours_affected, list):
            continue

        dtype = d.directive_type

        if dtype == "solar_reduction":
            factor = float(adj.get("factor", 1.0))
            for h in hours_affected:
                if isinstance(h, int) and 0 <= h < 24:
                    effective_solar[h] *= factor

        elif dtype == "minimum_battery_reserve":
            min_kwh = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
            for h in hours_affected:
                if isinstance(h, int) and 0 <= h < 24:
                    reserve[h] = max(reserve[h], min_kwh)

        elif dtype == "no_charge_window":
            for h in hours_affected:
                if isinstance(h, int) and 0 <= h < 24:
                    no_charge[h] = True

        elif dtype == "no_discharge_window":
            for h in hours_affected:
                if isinstance(h, int) and 0 <= h < 24:
                    no_discharge[h] = True

        elif dtype == "max_grid_window":
            max_grid = float(adj.get("max_grid_kwh", float("inf")))
            for h in hours_affected:
                if isinstance(h, int) and 0 <= h < 24:
                    grid_cap[h] = min(grid_cap[h], max_grid)

    return ConstraintArrays(
        effective_solar=effective_solar,
        reserve=reserve,
        no_charge=no_charge,
        no_discharge=no_discharge,
        grid_cap=grid_cap,
    )
