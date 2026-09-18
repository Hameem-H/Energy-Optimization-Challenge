from app.schemas import ScenarioRequest, HourEntry, Battery
from app.guardrails import guardrail_validate


def make_dummy_request(num_notes: int = 2) -> ScenarioRequest:
    return ScenarioRequest(
        scenario_id="test_scen",
        operator_notes=[f"Note {i}" for i in range(num_notes)],
        hours=[
            HourEntry(hour=h, demand_kwh=5.0, solar_kwh=2.0, tariff_bdt_per_kwh=4.0)
            for h in range(24)
        ],
        battery=Battery(
            capacity_kwh=20.0,
            initial_energy_kwh=5.0,
            minimum_energy_kwh=2.0,
            max_charge_kwh_per_hour=5.0,
            max_discharge_kwh_per_hour=5.0,
        ),
    )


def test_missing_note_index_filled_with_noop():
    req = make_dummy_request(2)
    raw_llm = [
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [10, 11]},
            "explanation": "No charge in hours 10-11",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert len(res) == 2
    assert res[0].directive_type == "no_op"
    assert res[0].applies is False
    assert res[1].directive_type == "no_charge_window"
    assert res[1].applies is True


def test_unsupported_directive_type_downgraded():
    req = make_dummy_request(1)
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "invalid_super_type",
            "structured_adjustment": {"hours": [1, 2]},
            "explanation": "custom directive",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert len(res) == 1
    assert res[0].directive_type == "no_op"
    assert res[0].applies is False


def test_solar_factor_percentage_normalization():
    req = make_dummy_request(1)
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12, 13], "factor": 25.0},
            "explanation": "25 percent factor",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "solar_reduction"
    assert res[0].structured_adjustment["factor"] == 0.25


def test_invalid_hours_downgrades_to_noop():
    req = make_dummy_request(1)
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [23, 24, 25]},  # 24 and 25 invalid
            "explanation": "out of range hours",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "no_op"


def test_excessive_reserve_downgrades_to_noop():
    req = make_dummy_request(1)
    # battery capacity is 20.0
    raw_llm = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [10], "minimum_energy_kwh": 50.0},
            "explanation": "excessive reserve",
        }
    ]
    res = guardrail_validate(raw_llm, req)
    assert res[0].directive_type == "no_op"
