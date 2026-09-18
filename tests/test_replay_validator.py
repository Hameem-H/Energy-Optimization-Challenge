import pytest
from app.schemas import ScenarioRequest, HourEntry, Battery, HourlyPlanEntry, DirectiveInterpretation
from app.replay_validator import replay_validate


def make_sample_plan():
    req = ScenarioRequest(
        scenario_id="s1",
        operator_notes=["Note 1"],
        hours=[
            HourEntry(hour=h, demand_kwh=10.0, solar_kwh=0.0, tariff_bdt_per_kwh=5.0)
            for h in range(24)
        ],
        battery=Battery(
            capacity_kwh=20.0,
            initial_energy_kwh=0.0,
            minimum_energy_kwh=0.0,
            max_charge_kwh_per_hour=5.0,
            max_discharge_kwh_per_hour=5.0,
        ),
    )
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="no-op",
        )
    ]
    plan = [
        HourlyPlanEntry(
            hour=h,
            grid_kwh=10.0,
            solar_used_kwh=0.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=0.0,
        )
        for h in range(24)
    ]
    totals = {
        "total_grid_kwh": 240.0,
        "total_cost_bdt": 1200.0,
        "peak_grid_kwh": 10.0,
    }
    return req, directives, plan, totals


def test_replay_validator_pass():
    req, directives, plan, totals = make_sample_plan()
    replay_validate(plan, req, directives, totals)


def test_replay_validator_energy_mismatch_fails():
    req, directives, plan, totals = make_sample_plan()
    # Corrupt grid kWh in hour 0
    plan[0] = HourlyPlanEntry(
        hour=0,
        grid_kwh=5.0,  # demand is 10.0, so 5.0 + 0 + 0 != 10.0
        solar_used_kwh=0.0,
        battery_action="idle",
        battery_kwh=0.0,
        battery_energy_after_kwh=0.0,
    )
    with pytest.raises(ValueError, match="Energy balance discrepancy"):
        replay_validate(plan, req, directives, totals)
