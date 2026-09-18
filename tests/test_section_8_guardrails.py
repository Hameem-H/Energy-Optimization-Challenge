import pytest
from app.schemas import OptimizeEnergyRequest, HourInput, BatteryInput
from app.guardrails import guardrail_validate
from app.replay_validator import replay_validate
from app.directive_engine import build_constraint_arrays
from app.optimizer import solve_energy_optimization


def make_sample_request():
    return OptimizeEnergyRequest(
        scenario_id="section_8_test",
        operator_notes=["Note 0", "Note 1", "Note 2"],
        hours=[
            HourInput(hour=h, demand_kwh=10.0, solar_kwh=5.0, tariff_bdt_per_kwh=4.0)
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


def test_guardrail_allowed_types():
    req = make_sample_request()
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "unknown_invented_type",
            "structured_adjustment": {"hours": [1, 2]},
            "explanation": "Invented type",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "no_op"
    assert res[0].applies is False


def test_guardrail_note_mapping_coverage():
    req = make_sample_request()
    # 3 operator notes, LLM returns note_index 1 and duplicates note_index 1
    raw_llm = [
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [10, 11]},
            "explanation": "Note 1",
        },
        {
            "note_index": 1,  # Duplicate note_index 1
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [12, 13]},
            "explanation": "Duplicate note 1",
        },
    ]
    res = guardrail_validate(raw_llm, req)
    assert len(res) == 3
    assert res[0].directive_type == "no_op"  # Fill missing index 0
    assert res[1].directive_type == "no_charge_window"  # Keep valid index 1
    assert res[2].directive_type == "no_op"  # Fill missing index 2


def test_guardrail_hours_unique_range_ascending():
    req = make_sample_request()
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [15, 12, 15, 12]},  # Unsorted & duplicate
            "explanation": "Unsorted hours",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "no_charge_window"
    assert res[0].structured_adjustment["hours"] == [12, 15]


def test_guardrail_solar_factor_bounds():
    req = make_sample_request()
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": -0.5},  # Factor < 0.0 invalid
            "explanation": "Invalid factor",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "no_op"


def test_guardrail_battery_reserve_bounds():
    req = make_sample_request()
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [12], "minimum_energy_kwh": 30.0},  # Exceeds capacity 20.0
            "explanation": "Excessive reserve",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "no_op"


def test_guardrail_grid_cap_bounds():
    req = make_sample_request()
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": [12], "max_grid_kwh": -5.0},  # Negative max grid
            "explanation": "Negative max grid",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "no_op"


def test_guardrail_applies_semantics():
    req = make_sample_request()
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,  # LLM erroneously set applies=True for no_op
            "directive_type": "no_op",
            "structured_adjustment": {"some": "data"},  # Non-null adjustment
            "explanation": "no op test",
        },
        {
            "note_index": 1,
            "applies": False,  # LLM erroneously set applies=False for non-no_op
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [10]},
            "explanation": "no charge test",
        },
    ]
    res = guardrail_validate(raw_llm, req)
    # no_op must have applies=False and structured_adjustment=None
    assert res[0].directive_type == "no_op"
    assert res[0].applies is False
    assert res[0].structured_adjustment is None

    # non-no_op must have applies=True
    assert res[1].directive_type == "no_charge_window"
    assert res[1].applies is True


def test_final_replay_validation():
    req = make_sample_request()
    directives = guardrail_validate([], req)
    arrays = build_constraint_arrays(req.hours, req.battery, directives)
    plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)

    # Replay validation passes on valid plan
    replay_validate(plan, req, directives, totals)
