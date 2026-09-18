"""Run the GridWise pipeline against MIXED-001 inside the venv.

Per user's MIXED-001 fix spec:
- initial_energy_kwh = 200 (overrides the 250 in the request body)
- Notes 19, 20, 47 -> no_op (solar factor out of range)
- Note 28 -> no_op (negative reserve)
- Note 29 -> no_op (exceeds battery capacity)
- Notes 25 (300), 26 (500), 32 (450) keep their stated values; infeasible ones become no_op via guardrail
- Hour 0 must begin from initial battery energy 200 kWh
- Total grid / cost / peak recalculated from the plan
- plan_summary lists applied (non-no_op) directive types
"""
import json
import sys

from app.schemas import HourInput, BatteryInput, OptimizeEnergyRequest
from app.guardrails import guardrail_validate
from app.directive_engine import build_constraint_arrays
from app.optimizer import solve_energy_optimization
from app.replay_validator import replay_validate


USER_BATTERY = {
    "capacity_kwh": 500.0,
    "initial_energy_kwh": 200.0,   # per user override in section 4
    "minimum_energy_kwh": 50.0,
    "max_charge_kwh_per_hour": 100.0,
    "max_discharge_kwh_per_hour": 100.0,
}

OPERATOR_NOTES = [
    "Reduce solar output to 50% from 10 AM to 12 PM.",
    "Reduce solar output to 30% from 1 PM to 3 PM.",
    "Reduce solar output to 20% from 4 PM to 5 PM.",
    "Keep solar output unchanged from 6 AM to 8 AM.",
    "Solar output will be lower during the afternoon.",
    "Set battery reserve to 100 kWh.",
    "Keep battery reserve at 150 kWh.",
    "Maintain a minimum battery reserve of 200 kWh.",
    "Battery reserve should be 50 kWh.",
    "Do not change the battery reserve.",
    "Do not modify the cafeteria schedule.",
    "The cafeteria menu changes tomorrow.",
    "Solar production is expected to be lower tomorrow.",
    "The weather may be cloudy in the afternoon.",
    "The campus library will close at 8 PM.",
    "Reduce solar output by 40% from 9 AM to 11 AM.",
    "Reduce solar output by 70% from 12 PM to 2 PM.",
    "Reduce solar output to 80% from 3 PM to 4 PM.",
    "Solar reduction factor should be 0.6 from 5 PM to 6 PM.",
    "Solar reduction factor should be 1.5 from 7 AM to 8 AM.",
    "Solar reduction factor should be -0.2 from 9 AM to 10 AM.",
    "Use a solar reduction factor of 0 from 11 AM to 12 PM.",
    "Use a solar reduction factor of 1 from 1 PM to 2 PM.",
    "Use a solar reduction factor of 0.4 from 2 PM to 3 PM.",
    "Keep the original solar forecast unchanged.",
    "Set the battery reserve to 300 kWh.",
    "Set the battery reserve to 500 kWh.",
    "Set the battery reserve to 0 kWh.",
    "Set the battery reserve to -20 kWh.",
    "Set the battery reserve to 600 kWh.",
    "Maintain a reserve of 250 kWh during peak hours.",
    "Keep at least 80 kWh in the battery.",
    "Keep at least 450 kWh in the battery.",
    "The battery should never fall below 50 kWh.",
    "The battery can use its full capacity if necessary.",
    "Do not charge the battery between 2 PM and 4 PM.",
    "Avoid charging during expensive tariff periods.",
    "The battery should remain unchanged.",
    "Ignore this note.",
    "No operational adjustment is required for the cafeteria.",
    "No change is required to the weather information.",
    "No-op: do not change demand, tariff, solar, or battery parameters.",
    "No-op: leave all scheduling parameters unchanged.",
    "No-op: change the battery reserve to 100 kWh.",
    "Reduce solar output to 40% from 12 PM to 1 PM.",
    "Reduce solar output to 25% from 6 PM to 7 PM.",
    "Use a solar reduction factor of 0.75 from 8 AM to 9 AM.",
    "Use a solar reduction factor of 1.2 from 10 AM to 11 AM.",
    "Keep solar production unchanged during the night.",
]
assert len(OPERATOR_NOTES) == 49

HOURS_DATA = [
    {"hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7},
    {"hour": 1, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 7},
    {"hour": 2, "demand_kwh": 170, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
    {"hour": 3, "demand_kwh": 165, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
    {"hour": 4, "demand_kwh": 160, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
    {"hour": 5, "demand_kwh": 170, "solar_kwh": 10, "tariff_bdt_per_kwh": 7},
    {"hour": 6, "demand_kwh": 185, "solar_kwh": 30, "tariff_bdt_per_kwh": 7},
    {"hour": 7, "demand_kwh": 200, "solar_kwh": 60, "tariff_bdt_per_kwh": 8},
    {"hour": 8, "demand_kwh": 210, "solar_kwh": 100, "tariff_bdt_per_kwh": 8},
    {"hour": 9, "demand_kwh": 220, "solar_kwh": 140, "tariff_bdt_per_kwh": 9},
    {"hour": 10, "demand_kwh": 230, "solar_kwh": 180, "tariff_bdt_per_kwh": 9},
    {"hour": 11, "demand_kwh": 240, "solar_kwh": 210, "tariff_bdt_per_kwh": 10},
    {"hour": 12, "demand_kwh": 250, "solar_kwh": 230, "tariff_bdt_per_kwh": 10},
    {"hour": 13, "demand_kwh": 255, "solar_kwh": 240, "tariff_bdt_per_kwh": 10},
    {"hour": 14, "demand_kwh": 250, "solar_kwh": 220, "tariff_bdt_per_kwh": 9},
    {"hour": 15, "demand_kwh": 240, "solar_kwh": 190, "tariff_bdt_per_kwh": 9},
    {"hour": 16, "demand_kwh": 235, "solar_kwh": 150, "tariff_bdt_per_kwh": 8},
    {"hour": 17, "demand_kwh": 240, "solar_kwh": 100, "tariff_bdt_per_kwh": 9},
    {"hour": 18, "demand_kwh": 250, "solar_kwh": 50, "tariff_bdt_per_kwh": 10},
    {"hour": 19, "demand_kwh": 260, "solar_kwh": 20, "tariff_bdt_per_kwh": 11},
    {"hour": 20, "demand_kwh": 255, "solar_kwh": 5, "tariff_bdt_per_kwh": 11},
    {"hour": 21, "demand_kwh": 230, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
    {"hour": 22, "demand_kwh": 210, "solar_kwh": 0, "tariff_bdt_per_kwh": 9},
    {"hour": 23, "demand_kwh": 195, "solar_kwh": 0, "tariff_bdt_per_kwh": 8},
]

req = OptimizeEnergyRequest(
    scenario_id="MIXED-001",
    operator_notes=OPERATOR_NOTES,
    hours=[HourInput(**h) for h in HOURS_DATA],
    battery=BatteryInput(**USER_BATTERY),
)

ALL_HOURS = list(range(24))
raw_directives = [
    {"note_index": 0,  "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [10, 11],         "factor": 0.5},  "explanation": "Reduce solar output to 50% from 10 AM to 12 PM."},
    {"note_index": 1,  "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [13, 14],         "factor": 0.3},  "explanation": "Reduce solar output to 30% from 1 PM to 3 PM."},
    {"note_index": 2,  "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [16],             "factor": 0.2},  "explanation": "Reduce solar output to 20% from 4 PM to 5 PM."},
    {"note_index": 3,  "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [6, 7],           "factor": 1.0},  "explanation": "Keep solar output unchanged from 6 AM to 8 AM."},
    {"note_index": 4,  "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational note about afternoon solar outlook."},
    {"note_index": 5,  "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 100.0}, "explanation": "Set battery reserve to 100 kWh."},
    {"note_index": 6,  "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 150.0}, "explanation": "Keep battery reserve at 150 kWh."},
    {"note_index": 7,  "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 200.0}, "explanation": "Maintain a minimum battery reserve of 200 kWh."},
    {"note_index": 8,  "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 50.0},  "explanation": "Battery reserve should be 50 kWh."},
    {"note_index": 9,  "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: no battery change requested."},
    {"note_index": 10, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: cafeteria schedule unrelated to energy."},
    {"note_index": 11, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: cafeteria menu change."},
    {"note_index": 12, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: future solar outlook."},
    {"note_index": 13, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: weather forecast."},
    {"note_index": 14, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: library hours unrelated to energy."},
    {"note_index": 15, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [9, 10],          "factor": 0.6},  "explanation": "Reduce solar output by 40% from 9 AM to 11 AM."},
    {"note_index": 16, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [12, 13],         "factor": 0.3},  "explanation": "Reduce solar output by 70% from 12 PM to 2 PM."},
    {"note_index": 17, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [15],             "factor": 0.8},  "explanation": "Reduce solar output to 80% from 3 PM to 4 PM."},
    {"note_index": 18, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [17],             "factor": 0.6},  "explanation": "Solar reduction factor 0.6 from 5 PM to 6 PM."},
    {"note_index": 19, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [7],              "factor": 1.5},  "explanation": "Solar reduction factor 1.5 from 7 AM to 8 AM."},
    {"note_index": 20, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [9],              "factor": -0.2}, "explanation": "Solar reduction factor -0.2 from 9 AM to 10 AM."},
    {"note_index": 21, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [11],             "factor": 0.0},  "explanation": "Solar reduction factor 0 from 11 AM to 12 PM."},
    {"note_index": 22, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [13],             "factor": 1.0},  "explanation": "Solar reduction factor 1 from 1 PM to 2 PM."},
    {"note_index": 23, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [14],             "factor": 0.4},  "explanation": "Solar reduction factor 0.4 from 2 PM to 3 PM."},
    {"note_index": 24, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: keep original solar forecast unchanged."},
    {"note_index": 25, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 300.0}, "explanation": "Set battery reserve to 300 kWh."},
    {"note_index": 26, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 500.0}, "explanation": "Set battery reserve to 500 kWh."},
    {"note_index": 27, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 0.0},   "explanation": "Set battery reserve to 0 kWh."},
    {"note_index": 28, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": -20.0}, "explanation": "Set battery reserve to -20 kWh."},
    {"note_index": 29, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 600.0}, "explanation": "Set battery reserve to 600 kWh."},
    {"note_index": 30, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": [17, 18, 19, 20], "minimum_energy_kwh": 250.0}, "explanation": "Maintain 250 kWh reserve during peak hours."},
    {"note_index": 31, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 80.0},  "explanation": "Keep at least 80 kWh in the battery."},
    {"note_index": 32, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 450.0}, "explanation": "Keep at least 450 kWh in the battery."},
    {"note_index": 33, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 50.0},  "explanation": "Battery never falls below 50 kWh."},
    {"note_index": 34, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: full battery capacity available."},
    {"note_index": 35, "applies": True,  "directive_type": "no_charge_window",         "structured_adjustment": {"hours": [14, 15]},                      "explanation": "Do not charge the battery between 2 PM and 4 PM."},
    {"note_index": 36, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: no specific charging avoidance."},
    {"note_index": 37, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: no battery change."},
    {"note_index": 38, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: ignored note."},
    {"note_index": 39, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: no cafeteria adjustment."},
    {"note_index": 40, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: no weather change."},
    {"note_index": 41, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: no parameter changes."},
    {"note_index": 42, "applies": False, "directive_type": "no_op",                    "structured_adjustment": None,                                   "explanation": "Informational: no parameter changes."},
    {"note_index": 43, "applies": True,  "directive_type": "minimum_battery_reserve",  "structured_adjustment": {"hours": ALL_HOURS, "minimum_energy_kwh": 100.0}, "explanation": "Change battery reserve to 100 kWh."},
    {"note_index": 44, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [12],             "factor": 0.4},  "explanation": "Reduce solar output to 40% from 12 PM to 1 PM."},
    {"note_index": 45, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [18],             "factor": 0.25}, "explanation": "Reduce solar output to 25% from 6 PM to 7 PM."},
    {"note_index": 46, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [8],              "factor": 0.75}, "explanation": "Solar reduction factor 0.75 from 8 AM to 9 AM."},
    {"note_index": 47, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [10],             "factor": 1.2},  "explanation": "Solar reduction factor 1.2 from 10 AM to 11 AM."},
    {"note_index": 48, "applies": True,  "directive_type": "solar_reduction",          "structured_adjustment": {"hours": [0, 1, 2, 3, 4, 5, 20, 21, 22, 23], "factor": 1.0}, "explanation": "Keep solar production unchanged during the night."},
]
assert len(raw_directives) == 49

# ---- Pipeline: guardrail_validate -> build_constraint_arrays -> solve -> replay_validate
directives = guardrail_validate(raw_directives, req)
arrays = build_constraint_arrays(req.hours, req.battery, directives)
plan, totals = solve_energy_optimization(req.hours, req.battery, arrays)
replay_validate(plan, req, directives, totals)

# ---- Verify user-required invariants
assert directives[19].directive_type == "no_op" and directives[19].applies is False
assert directives[20].directive_type == "no_op" and directives[20].applies is False
assert directives[28].directive_type == "no_op" and directives[28].applies is False
assert directives[29].directive_type == "no_op" and directives[29].applies is False
assert directives[32].directive_type == "no_op" and directives[32].applies is False
assert directives[47].directive_type == "no_op" and directives[47].applies is False
# Notes 25 (300) and 33 (50) must remain valid
assert directives[25].directive_type == "minimum_battery_reserve" and directives[25].applies is True
assert directives[25].structured_adjustment["minimum_energy_kwh"] == 300.0
# Hour 0 must begin from initial = 200; reserve 300 forces charge[0]=100, so soc_after=300
assert plan[0].battery_energy_after_kwh == 300.0
assert plan[0].grid_kwh == 280.0  # 180 + 100 = 280
assert req.battery.initial_energy_kwh == 200.0
# Hour 23 soc must satisfy soc[23] >= 200
assert plan[23].battery_energy_after_kwh >= 200.0 - 1e-4

# ---- Build response
applied_types = [d.directive_type for d in directives if d.applies and d.directive_type != "no_op"]
summary = (
    f"Applied directives: {', '.join(applied_types)}."
    if applied_types
    else "No operational directives applied."
)

response = {
    "scenario_id": req.scenario_id,
    "directive_interpretation": [d.model_dump() for d in directives],
    "hourly_plan": [p.model_dump() for p in plan],
    "total_grid_kwh": totals["total_grid_kwh"],
    "total_cost_bdt": totals["total_cost_bdt"],
    "peak_grid_kwh": totals["peak_grid_kwh"],
    "plan_summary": summary,
}

out_path = "mixed001_response.json"
with open(out_path, "w") as f:
    json.dump(response, f, indent=2)

print(f"Wrote {out_path}")
print(f"  total_grid_kwh = {totals['total_grid_kwh']}")
print(f"  total_cost_bdt = {totals['total_cost_bdt']}")
print(f"  peak_grid_kwh  = {totals['peak_grid_kwh']}")
print(f"  hour 0 balance = {plan[0].grid_kwh} + {plan[0].solar_used_kwh} + 0 = 180 + {plan[0].battery_kwh}  (soc_after={plan[0].battery_energy_after_kwh})")
print(f"  summary        = {summary[:120]}...")
