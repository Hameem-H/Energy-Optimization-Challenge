#!/usr/bin/env bash
set -e

echo "=== GridWise Local Smoke Check ==="

echo "1. Checking Python dependencies..."
python -m pytest tests/ -v

echo "2. Starting background Uvicorn server..."
uvicorn app.main:app --port 8000 --host 127.0.0.1 &
SERVER_PID=$!
sleep 2

cleanup() {
  echo "Stopping Uvicorn server (PID $SERVER_PID)..."
  kill $SERVER_PID || true
}
trap cleanup EXIT

echo "3. Testing /health endpoint..."
HEALTH_RESP=$(curl -s http://127.0.0.1:8000/health)
echo "Response: $HEALTH_RESP"

if [[ "$HEALTH_RESP" != *"ok"* ]]; then
  echo "Health check failed!"
  exit 1
fi

echo "4. Testing /optimize-energy endpoint..."
OPTIMIZE_RESP=$(curl -s -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "smoke_test",
    "operator_notes": ["Solar drops by 50% from 12 to 14."],
    "hours": [
      {"hour": 0, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 1, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 2, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 3, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 4, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 5, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 6, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 7, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 8, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 9, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 10, "demand_kwh": 10.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 11, "demand_kwh": 10.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 12, "demand_kwh": 10.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 13, "demand_kwh": 10.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 14, "demand_kwh": 10.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 15, "demand_kwh": 10.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 16, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 17, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 18, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 19, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 20, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 21, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 22, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
      {"hour": 23, "demand_kwh": 10.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0}
    ],
    "battery": {
      "capacity_kwh": 20.0,
      "initial_energy_kwh": 0.0,
      "minimum_energy_kwh": 0.0,
      "max_charge_kwh_per_hour": 5.0,
      "max_discharge_kwh_per_hour": 5.0
    }
  }')

echo "Response: $OPTIMIZE_RESP"

if [[ "$OPTIMIZE_RESP" == *"hourly_plan"* ]]; then
  echo "=== Smoke check PASSED ==="
else
  echo "=== Smoke check FAILED ==="
  exit 1
fi
