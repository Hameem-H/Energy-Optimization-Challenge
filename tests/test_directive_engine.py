from app.schemas import HourInput, BatteryInput, DirectiveInterpretation
from app.directive_engine import build_constraint_arrays


def make_dummy_hours():
    return [
        HourInput(hour=h, demand_kwh=100.0, solar_kwh=50.0, tariff_bdt_per_kwh=6.0)
        for h in range(24)
    ]


def make_dummy_battery():
    return BatteryInput(
        capacity_kwh=200.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=20.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )


def test_section_4_2_examples():
    hours = make_dummy_hours()
    battery = make_dummy_battery()

    directives = [
        # Example 1: Solar output drops to 20% from 1 PM to 3 PM -> [13, 14], factor 0.2
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="solar_reduction",
            structured_adjustment={"hours": [13, 14], "factor": 0.2},
            explanation="Solar output will drop to about 20% from 1 PM to 3 PM.",
        ),
        # Example 2: Do not charge between 2 PM and 4 PM -> [14, 15]
        DirectiveInterpretation(
            note_index=1,
            applies=True,
            directive_type="no_charge_window",
            structured_adjustment={"hours": [14, 15]},
            explanation="Do not charge the battery between 2 PM and 4 PM.",
        ),
        # Example 3: Keep at least 120 kWh in reserve from 6 PM until 9 PM -> [18, 19, 20]
        DirectiveInterpretation(
            note_index=2,
            applies=True,
            directive_type="minimum_battery_reserve",
            structured_adjustment={"hours": [18, 19, 20], "minimum_energy_kwh": 120.0},
            explanation="Keep at least 120 kWh in reserve from 6 PM until 9 PM.",
        ),
        # Example 4: Information note -> no_op
        DirectiveInterpretation(
            note_index=3,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="The cafeteria menu changes tomorrow.",
        ),
    ]

    arrays = build_constraint_arrays(hours, battery, directives)

    # Validate Example 1: effective solar at hours 13 and 14 should be 50.0 * 0.2 = 10.0
    assert abs(arrays.effective_solar[13] - 10.0) < 1e-6
    assert abs(arrays.effective_solar[14] - 10.0) < 1e-6
    assert arrays.effective_solar[12] == 50.0

    # Validate Example 2: no_charge at hours 14 and 15
    assert arrays.no_charge[14] is True
    assert arrays.no_charge[15] is True
    assert arrays.no_charge[13] is False

    # Validate Example 3: minimum reserve 120 kWh at hours 18, 19, 20
    assert arrays.reserve[18] == 120.0
    assert arrays.reserve[19] == 120.0
    assert arrays.reserve[20] == 120.0
    assert arrays.reserve[17] == 20.0

    # Validate Example 4: no_op does not modify any constraint arrays
    # (checked above - no spurious changes in other hours)


def test_max_grid_window_and_no_discharge_directives():
    hours = make_dummy_hours()
    battery = make_dummy_battery()

    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="no_discharge_window",
            structured_adjustment={"hours": [8, 9]},
            explanation="No discharge 8 AM to 10 AM",
        ),
        DirectiveInterpretation(
            note_index=1,
            applies=True,
            directive_type="max_grid_window",
            structured_adjustment={"hours": [18, 19], "max_grid_kwh": 150.0},
            explanation="Max grid 150 kWh from 6 PM to 8 PM",
        ),
    ]

    arrays = build_constraint_arrays(hours, battery, directives)

    assert arrays.no_discharge[8] is True and arrays.no_discharge[9] is True
    assert arrays.no_discharge[7] is False

    assert arrays.grid_cap[18] == 150.0 and arrays.grid_cap[19] == 150.0
    assert arrays.grid_cap[17] == float("inf")
