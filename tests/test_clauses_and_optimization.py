import pytest
from app.schemas import HourInput, BatteryInput, OptimizeEnergyRequest, DirectiveInterpretation
from app.guardrails import guardrail_validate
from app.directive_engine import build_constraint_arrays
from app.optimizer import solve_energy_optimization
from app.replay_validator import replay_validate


def make_test_request():
    return OptimizeEnergyRequest(
        scenario_id="clause_test_01",
        operator_notes=[
            "Solar output will drop by 80% from 1 PM to 3 PM.",
            "Keep at least 15 kWh in reserve from 6 PM to 9 PM.",
            "Informational note about cafeteria menu.",
        ],
        hours=[
            HourInput(
                hour=h,
                demand_kwh=10.0,
                solar_kwh=15.0 if 10 <= h <= 16 else 0.0,
                tariff_bdt_per_kwh=10.0 if 17 <= h <= 21 else 4.0,
            )
            for h in range(24)
        ],
        battery=BatteryInput(
            capacity_kwh=20.0,
            initial_energy_kwh=5.0,
            minimum_energy_kwh=2.0,
            max_charge_kwh_per_hour=5.0,
            max_discharge_kwh_per_hour=5.0,
        ),
    )


def test_clause_5_1_guardrail_sanitization():
    req = make_test_request()

    # Raw LLM output with mixed formatting, out of order note_index, percentage factor, unsorted hours
    raw_llm = [
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [20, 18, 19], "minimum_energy_kwh": 15.0},
            "explanation": "15 kWh reserve from 6 PM to 9 PM",
        },
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [14, 13], "factor": 0.2},  # 80% reduction leaves factor 0.2 remaining
            "explanation": "80% reduction leaves factor 0.2",
        },
        {
            "note_index": 2,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Cafeteria note",
        },
    ]

    validated = guardrail_validate(raw_llm, req)

    # 1. Exactly 3 entries in note_index order 0, 1, 2
    assert len(validated) == 3
    assert [v.note_index for v in validated] == [0, 1, 2]

    # 2. Note 0: solar reduction factor normalized to 0.2 and hours sorted [13, 14]
    assert validated[0].directive_type == "solar_reduction"
    assert validated[0].applies is True
    assert validated[0].structured_adjustment == {"hours": [13, 14], "factor": 0.2}

    # 3. Note 1: reserve hours sorted [18, 19, 20]
    assert validated[1].directive_type == "minimum_battery_reserve"
    assert validated[1].applies is True
    assert validated[1].structured_adjustment == {"hours": [18, 19, 20], "minimum_energy_kwh": 15.0}

    # 4. Note 2: no_op has applies=False and structured_adjustment=None
    assert validated[2].directive_type == "no_op"
    assert validated[2].applies is False
    assert validated[2].structured_adjustment is None


def test_clause_5_2_5_3_optimization_math_and_objective():
    req = make_test_request()

    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="solar_reduction",
            structured_adjustment={"hours": [13, 14], "factor": 0.2},
            explanation="Solar output factor 0.2 for hours 13, 14",
        ),
        DirectiveInterpretation(
            note_index=1,
            applies=True,
            directive_type="minimum_battery_reserve",
            structured_adjustment={"hours": [18, 19, 20], "minimum_energy_kwh": 15.0},
            explanation="Reserve 15 kWh for hours 18-20",
        ),
        DirectiveInterpretation(
            note_index=2,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Informational",
        ),
    ]

    arrays = build_constraint_arrays(req.hours, req.battery, directives)

    # Verify Section 5.3 math transformations
    assert abs(arrays.effective_solar[13] - 15.0 * 0.2) < 1e-6
    assert abs(arrays.effective_solar[14] - 15.0 * 0.2) < 1e-6
    assert arrays.effective_solar[12] == 15.0

    assert arrays.reserve[18] == 15.0
    assert arrays.reserve[19] == 15.0
    assert arrays.reserve[20] == 15.0
    assert arrays.reserve[17] == 2.0  # base minimum

    # Solve optimization problem
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    # Replay validate energy conservation & objective sum(grid[h] * tariff[h])
    replay_validate(plan, req, directives, totals)

    recomputed_cost = sum(p.grid_kwh * req.hours[p.hour].tariff_bdt_per_kwh for p in plan)
    assert abs(totals["total_cost_bdt"] - recomputed_cost) < 0.01

    # Check that battery reserve constraint was satisfied in hours 18..20
    for p in plan:
        if 18 <= p.hour <= 20:
            assert p.battery_energy_after_kwh >= 15.0 - 0.01
