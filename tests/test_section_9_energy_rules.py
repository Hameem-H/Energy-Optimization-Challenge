import pytest
from app.schemas import OptimizeEnergyRequest, HourInput, BatteryInput
from app.directive_engine import build_constraint_arrays
from app.optimizer import solve_energy_optimization
from app.replay_validator import replay_validate


def make_section_9_request():
    return OptimizeEnergyRequest(
        scenario_id="section_9_math_test",
        operator_notes=["No special directives"],
        hours=[
            HourInput(
                hour=h,
                demand_kwh=12.0 if 18 <= h <= 21 else 6.0,
                solar_kwh=15.0 if 10 <= h <= 15 else 0.0,
                tariff_bdt_per_kwh=12.0 if 18 <= h <= 21 else 3.0,
            )
            for h in range(24)
        ],
        battery=BatteryInput(
            capacity_kwh=20.0,
            initial_energy_kwh=5.0,
            minimum_energy_kwh=2.0,
            max_charge_kwh_per_hour=4.0,
            max_discharge_kwh_per_hour=4.0,
        ),
    )


def test_rule_9_1_battery_state_tracking():
    req = make_section_9_request()
    arrays = build_constraint_arrays(req.hours, req.battery, [])
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    prev_soc = req.battery.initial_energy_kwh
    for entry in plan:
        kwh = entry.battery_kwh
        action = entry.battery_action
        soc_after = entry.battery_energy_after_kwh

        if action == "charge":
            assert abs(soc_after - (prev_soc + kwh)) < 1e-3
        elif action == "discharge":
            assert abs(soc_after - (prev_soc - kwh)) < 1e-3
        elif action == "idle":
            assert kwh == 0.0
            assert abs(soc_after - prev_soc) < 1e-3

        prev_soc = soc_after


def test_rule_9_2_battery_bounds():
    req = make_section_9_request()
    arrays = build_constraint_arrays(req.hours, req.battery, [])
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    for entry in plan:
        soc = entry.battery_energy_after_kwh
        assert req.battery.minimum_energy_kwh - 1e-3 <= soc <= req.battery.capacity_kwh + 1e-3


def test_rule_9_3_charge_discharge_rate_limits():
    req = make_section_9_request()
    arrays = build_constraint_arrays(req.hours, req.battery, [])
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    for entry in plan:
        if entry.battery_action == "charge":
            assert entry.battery_kwh <= req.battery.max_charge_kwh_per_hour + 1e-3
        elif entry.battery_action == "discharge":
            assert entry.battery_kwh <= req.battery.max_discharge_kwh_per_hour + 1e-3


def test_rule_9_4_solar_usage_and_curtailment():
    req = make_section_9_request()
    arrays = build_constraint_arrays(req.hours, req.battery, [])
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    for entry in plan:
        h = entry.hour
        base_solar = req.hours[h].solar_kwh
        assert 0.0 <= entry.solar_used_kwh <= base_solar + 1e-3


def test_rule_9_5_exact_energy_balance():
    req = make_section_9_request()
    arrays = build_constraint_arrays(req.hours, req.battery, [])
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    for entry in plan:
        h = entry.hour
        demand = req.hours[h].demand_kwh
        grid = entry.grid_kwh
        solar_used = entry.solar_used_kwh
        action = entry.battery_action
        kwh = entry.battery_kwh

        chg = kwh if action == "charge" else 0.0
        dis = kwh if action == "discharge" else 0.0

        supply = grid + solar_used + dis
        load = demand + chg
        assert abs(supply - load) < 1e-3


def test_rule_9_6_end_of_day_neutrality():
    req = make_section_9_request()
    arrays = build_constraint_arrays(req.hours, req.battery, [])
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    # Hour 23 battery_energy_after_kwh must equal initial_energy_kwh
    final_soc = plan[23].battery_energy_after_kwh
    assert abs(final_soc - req.battery.initial_energy_kwh) < 1e-3
