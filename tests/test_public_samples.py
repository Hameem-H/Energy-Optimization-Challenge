from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def make_valid_payload():
    return {
        "scenario_id": "sample_scen_01",
        "operator_notes": [
            "Solar output drops by 80% from 1 PM to 3 PM due to storm clouds.",
            "Maintain at least 5 kWh battery reserve during 6 PM to 10 PM.",
        ],
        "hours": [
            {
                "hour": h,
                "demand_kwh": 12.0 if 17 <= h <= 21 else 6.0,
                "solar_kwh": 10.0 if 10 <= h <= 15 else 0.0,
                "tariff_bdt_per_kwh": 10.0 if 17 <= h <= 21 else 3.0,
            }
            for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 20.0,
            "initial_energy_kwh": 5.0,
            "minimum_energy_kwh": 2.0,
            "max_charge_kwh_per_hour": 5.0,
            "max_discharge_kwh_per_hour": 5.0,
        },
    }


def test_end_to_end_optimize_energy():
    payload = make_valid_payload()
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["scenario_id"] == "sample_scen_01"
    assert len(data["directive_interpretation"]) == 2
    assert len(data["hourly_plan"]) == 24
    assert data["total_grid_kwh"] >= 0
    assert data["total_cost_bdt"] >= 0
    assert data["peak_grid_kwh"] >= 0
    assert isinstance(data["plan_summary"], str)


def test_large_operator_notes_list_50_items():
    payload = make_valid_payload()
    payload["operator_notes"] = [f"Note {i}" for i in range(50)]
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["directive_interpretation"]) == 50


def test_malformed_hours_count_returns_400():
    payload = make_valid_payload()
    payload["hours"] = payload["hours"][:23]  # Only 23 hours
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 400
    assert "error" in response.json()


def test_empty_notes_returns_400():
    payload = make_valid_payload()
    payload["operator_notes"] = []
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 400


def test_invalid_battery_capacity_returns_400():
    payload = make_valid_payload()
    payload["battery"]["capacity_kwh"] = -10.0
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 400
