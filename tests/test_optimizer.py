from app.schemas import HourEntry, Battery
from app.directive_engine import build_constraint_arrays
from app.optimizer import solve_energy_optimization


def test_basic_optimization():
    # 24 hours constant demand 10, solar 0, tariff 5
    hours = [
        HourEntry(hour=h, demand_kwh=10.0, solar_kwh=0.0, tariff_bdt_per_kwh=5.0)
        for h in range(24)
    ]
    battery = Battery(
        capacity_kwh=20.0,
        initial_energy_kwh=0.0,
        minimum_energy_kwh=0.0,
        max_charge_kwh_per_hour=5.0,
        max_discharge_kwh_per_hour=5.0,
    )
    constraints = build_constraint_arrays(hours, battery, [])

    hourly_plan, totals = solve_energy_optimization(hours, battery, constraints)

    assert len(hourly_plan) == 24
    assert totals["total_grid_kwh"] == 240.0
    assert totals["total_cost_bdt"] == 1200.0
    assert totals["peak_grid_kwh"] == 10.0


def test_optimization_with_solar_and_battery():
    # High solar in daytime (hours 10-15), high tariff in evening (hours 18-21)
    hours = []
    for h in range(24):
        solar = 15.0 if 10 <= h <= 15 else 0.0
        tariff = 10.0 if 18 <= h <= 21 else 2.0
        demand = 8.0
        hours.append(HourEntry(hour=h, demand_kwh=demand, solar_kwh=solar, tariff_bdt_per_kwh=tariff))

    battery = Battery(
        capacity_kwh=20.0,
        initial_energy_kwh=0.0,
        minimum_energy_kwh=0.0,
        max_charge_kwh_per_hour=5.0,
        max_discharge_kwh_per_hour=5.0,
    )
    constraints = build_constraint_arrays(hours, battery, [])
    hourly_plan, totals = solve_energy_optimization(hours, battery, constraints)

    # Battery should charge during solar/low-tariff hours and discharge during peak tariff hours (18-21)
    evening_discharges = [p.battery_kwh for p in hourly_plan if 18 <= p.hour <= 21 and p.battery_action == "discharge"]
    assert sum(evening_discharges) > 0
    assert hourly_plan[23].battery_energy_after_kwh == 0.0  # end of day neutrality
