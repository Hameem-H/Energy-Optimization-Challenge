# GridWise — Smart Campus Energy Optimization

**BUP CSE Fest 2026 · Online Preliminary · LLM-Assisted Energy Optimization**

GridWise is a production-grade FastAPI service that produces a 24-hour cost-optimal energy schedule for a smart campus. A language model (Google Gemini) interprets 1–3 free-text operator notes into structured numerical constraints; a PuLP linear program then minimizes total electricity cost (BDT) while honoring every applicable directive and the physical battery model.

The service exposes the two endpoints required by the rubric (`GET /health`, `POST /optimize-energy`) and a self-contained local quickstart that judges can run from a clean machine.

---

## Table of Contents
1. [Architecture](#architecture)
2. [LLM Role & Guardrail Pipeline](#llm-role--guardrail-pipeline)
3. [Optimizer & Solver Specification](#optimizer--solver-specification)
4. [API Contract](#api-contract)
5. [Environment Variables](#environment-variables)
6. [Running Locally (Clean-Machine Quickstart)](#running-locally-clean-machine-quickstart)
7. [Docker Fallback](#docker-fallback)
8. [Public-Sample Test Procedure](#public-sample-test-procedure)
9. [Repository Layout](#repository-layout)
10. [Known Limitations & Assumptions](#known-limitations--assumptions)
11. [Acknowledgments & Credits](#acknowledgments--credits)

---

## Architecture

```
[ POST /optimize-energy ]
          │
          ▼
┌──────────────────────────┐
│   llm_interpreter.py     │  →  Gemini 2.5 Flash parses operator notes
└──────────┬───────────────┘     into structured JSON directives
           │ (raw, untrusted JSON)
           ▼
┌──────────────────────────┐
│     guardrails.py        │  →  Deterministic validation, repair,
└──────────┬───────────────┘     range checks, applies/hours sanity
           │ (validated DirectiveInterpretation list)
           ▼
┌──────────────────────────┐
│   directive_engine.py    │  →  Pure function: collapses N directives
└──────────┬───────────────┘     into 24-hour constraint arrays
           │ (ConstraintArrays: effective_solar, reserve,
           │                   no_charge, no_discharge, grid_cap)
           ▼
┌──────────────────────────┐
│      optimizer.py        │  →  PuLP + CBC LP:
└──────────┬───────────────┘     minimize Σ grid[h]·tariff[h]
           │ (HourlyPlanEntry[24] + totals)
           ▼
┌──────────────────────────┐
│   replay_validator.py    │  →  Independent replay of the LP plan
└──────────┬───────────────┘     against the same constraints
           │
           ▼
[ OptimizeEnergyResponse JSON ]
```

A failure at any stage returns a controlled HTTP error — never a 5xx on valid input.

---

## LLM Role & Guardrail Pipeline

### LLM Role

`gemini-2.5-flash` (via the `google-genai` SDK) is strictly scoped to converting 1–3 free-text operator notes into one of six structured directive shapes:

| `directive_type` | `structured_adjustment` |
|---|---|
| `solar_reduction` | `{ hours: int[0..23], factor: float[0..1] }` |
| `minimum_battery_reserve` | `{ hours: int[0..23], minimum_energy_kwh: float }` |
| `no_charge_window` | `{ hours: int[0..23] }` |
| `no_discharge_window` | `{ hours: int[0..23] }` |
| `max_grid_window` | `{ hours: int[0..23], max_grid_kwh: float ≥ 0 }` |
| `no_op` | `null` (irrelevant / informational notes) |

The LLM never computes energy balance or produces numerical schedules; that is the LP's job.

### Guardrails (`app/guardrails.py`)

Every raw LLM object passes through deterministic validation before reaching the optimizer:

1. **Coverage check** — exactly `N` entries for `note_index` 0..N-1; missing indices become `no_op`.
2. **Type allow-list** — `directive_type` must be one of the six above; anything else becomes `no_op`.
3. **Applies & adjustment consistency** — `no_op` forces `applies=False` and `structured_adjustment=None`; every other type forces `applies=True`.
4. **Hours array validation** — integers in 0..23, deduplicated, sorted ascending.
5. **Type-specific numeric range checks**:
   - `solar_reduction`: `factor ∈ [0, 1]`; percentages 10..100 are normalized.
   - `minimum_battery_reserve`: `0 ≤ minimum_energy_kwh ≤ capacity_kwh`.
   - `max_grid_window`: `max_grid_kwh ≥ 0`.
6. **Value-only enforcement** — the guardrail only checks the value of a directive (finite, non-negative, within capacity). Whether the resulting constraint is physically reachable is a **feasibility** concern and is detected later by the LP solver, which then surfaces a 422 with a clear message rather than silently rewriting the directive.

Any unrepairable violation falls back to `no_op` with a human-readable reason in `explanation`.

---

## Optimizer & Solver Specification

The linear program (`app/optimizer.py`) is solved by **PuLP** with the bundled **CBC** solver.

### Decision Variables (per hour `h ∈ 0..23`)
| Variable | Meaning |
|---|---|
| `grid[h] ≥ 0` | Grid energy purchased (kWh) |
| `solar_used[h] ≥ 0` | Solar generation consumed (kWh) |
| `charge[h] ≥ 0` | Energy charged to battery (kWh) |
| `discharge[h] ≥ 0` | Energy discharged from battery (kWh) |
| `soc[h]` | Battery state-of-charge at end of hour `h` (kWh) |

### Objective

```
minimize   Σ grid[h] · tariff[h]      over h = 0..23
```

### Constraints

```
1. solar_used[h]             ≤  effective_solar[h]
2. grid[h] + solar_used[h] + discharge[h]  =  demand[h] + charge[h]
3. soc[h]                    =  soc[h-1] + charge[h] - discharge[h]
                              (soc[-1] = initial_energy_kwh)
4. reserve[h]                ≤  soc[h]  ≤  capacity_kwh
5. charge[h]                 ≤  max_charge_kwh_per_hour  (0 in no_charge_window)
6. discharge[h]              ≤  max_discharge_kwh_per_hour (0 in no_discharge_window)
7. grid[h]                   ≤  grid_cap[h]
8. soc[23]                   ≥  initial_energy_kwh    (end-of-day neutrality)
```

The LP is built from the JSON request only; no ambient state is shared between requests.

---

## API Contract

### `GET /health`
```bash
curl -s http://localhost:8000/health
```
```json
{"status":"ok"}
```
Returns 200 with `{"status":"ok"}` whenever the service is ready. Does not require any environment variables.

### `POST /optimize-energy`
Accepts a JSON `OptimizeEnergyRequest` and returns `OptimizeEnergyResponse`. The full request schema is in `app/schemas/request.py`; the response schema is in `app/schemas/response.py`.

**Status codes**
| Code | Meaning |
|---|---|
| `200` | Successful optimization with `hourly_plan` and totals |
| `400` | Malformed JSON / schema violation (returned by FastAPI validation handler) |
| `422` | LP infeasible — directives conflict with battery physics or grid limits. Response body contains the sanitized `directive_interpretation` and a human-readable `detail` |
| `500` | Unhandled internal error (caught by global handler, body does not leak stack traces) |

---

## Environment Variables

| Variable | Required | Description | Default |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes (live mode) | API key for Google Gemini. Also accepts `GOOGLE_API_KEY` as alias | — |
| `LLM_MODEL` | No | Gemini model identifier | `gemini-2.5-flash` |
| `PORT` | No | HTTP listen port (used by Docker & `uvicorn`) | `8000` |

Supply them at runtime via shell exports, a `.env` file, or your deployment platform's secret store. **Never commit secrets** — `.env` is in `.gitignore`.

`.env.example` is provided as a template:
```env
GEMINI_API_KEY=""
LLM_MODEL=gemini-2.5-flash
PORT=8000
```

---

## Running Locally (Clean-Machine Quickstart)

```bash
# 1. Clone
git clone https://github.com/Hameem-H/Energy-Optimization-Challenge.git
cd Energy-Optimization-Challenge

# 2. Install dependencies (Python 3.11+)
python -m venv .venv
source .venv/bin/activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=<your_key>

# 4. Start the service
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5. Health check (in another shell)
curl -s http://localhost:8000/health
#   → {"status":"ok"}

# 6. Run an optimization against a public sample (see next section)

# 7. Run the test suite
python -m pytest tests/ -v

# 8. Run the bundled smoke check
bash run_local_check.sh
```

---

## Docker Fallback

```bash
# Build
docker build -t gridwise:1.0.0 .

# Run (port + Gemini key supplied at runtime; nothing baked into the image)
docker run --rm -p 8000:8000 \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  gridwise:1.0.0
```

The image:
- Uses `python:3.11-slim` as the base.
- Exposes port `8000` and binds `0.0.0.0`.
- Contains **no baked-in secrets**; all credentials come from `-e` or `--env-file`.
- Reaches `/health` within a few seconds of container start.

A pre-built image is published to GitHub Container Registry:

```bash
docker pull ghcr.io/hameem-h/energy-optimization-challenge:1.0.0
docker run --rm -p 8000:8000 -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  ghcr.io/hameem-h/energy-optimization-challenge:1.0.0
```

---

## Public-Sample Test Procedure

A minimal valid request body is:

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "scen_demo",
    "operator_notes": [
      "Solar output drops by 80% from 1 PM to 3 PM.",
      "Maintain at least 5 kWh battery reserve during 6 PM to 10 PM."
    ],
    "hours": [
      {"hour":0, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":1, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":2, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":3, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":4, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":5, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":6, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":7, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":8, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":9, "demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":10,"demand_kwh":6.0, "solar_kwh":10.0, "tariff_bdt_per_kwh":3.0},
      {"hour":11,"demand_kwh":6.0, "solar_kwh":10.0, "tariff_bdt_per_kwh":3.0},
      {"hour":12,"demand_kwh":6.0, "solar_kwh":10.0, "tariff_bdt_per_kwh":3.0},
      {"hour":13,"demand_kwh":6.0, "solar_kwh":10.0, "tariff_bdt_per_kwh":3.0},
      {"hour":14,"demand_kwh":6.0, "solar_kwh":10.0, "tariff_bdt_per_kwh":3.0},
      {"hour":15,"demand_kwh":6.0, "solar_kwh":10.0, "tariff_bdt_per_kwh":3.0},
      {"hour":16,"demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":17,"demand_kwh":12.0,"solar_kwh":0.0,  "tariff_bdt_per_kwh":10.0},
      {"hour":18,"demand_kwh":12.0,"solar_kwh":0.0,  "tariff_bdt_per_kwh":10.0},
      {"hour":19,"demand_kwh":12.0,"solar_kwh":0.0,  "tariff_bdt_per_kwh":10.0},
      {"hour":20,"demand_kwh":12.0,"solar_kwh":0.0,  "tariff_bdt_per_kwh":10.0},
      {"hour":21,"demand_kwh":12.0,"solar_kwh":0.0,  "tariff_bdt_per_kwh":10.0},
      {"hour":22,"demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0},
      {"hour":23,"demand_kwh":6.0, "solar_kwh":0.0,  "tariff_bdt_per_kwh":3.0}
    ],
    "battery": {
      "capacity_kwh": 20.0,
      "initial_energy_kwh": 5.0,
      "minimum_energy_kwh": 2.0,
      "max_charge_kwh_per_hour": 5.0,
      "max_discharge_kwh_per_hour": 5.0
    }
  }'
```

A successful response includes:
- `directive_interpretation` — exactly one entry per `operator_notes`, in `note_index` order
- `hourly_plan` — 24 entries with `grid_kwh`, `solar_used_kwh`, `battery_kwh`, `battery_action`, `battery_energy_after_kwh`
- `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` — totals consistent with `hourly_plan`
- `plan_summary` — one-sentence natural-language summary

---

## Repository Layout

```
.
├── app/
│   ├── main.py              # FastAPI app, /health + /optimize-energy, error handlers
│   ├── config.py            # Env-var loading (GEMINI_API_KEY, LLM_MODEL, PORT)
│   ├── llm_interpreter.py   # Gemini call returning raw directive JSON
│   ├── guardrails.py        # Deterministic validate/repair/sanitize
│   ├── directive_engine.py  # Collapse directives into per-hour constraint arrays
│   ├── optimizer.py         # PuLP LP build + solve
│   ├── replay_validator.py  # Independent re-check of the LP output
│   └── schemas/             # Pydantic request/response/directive models
├── tests/                   # Pytest suite (guardrails, optimizer, replay, health, public samples)
├── Dockerfile               # python:3.11-slim + uvicorn, 0.0.0.0:8000
├── requirements.txt         # fastapi, uvicorn, pydantic, pulp, google-genai, pytest, httpx, dotenv
├── run_local_check.sh       # End-to-end smoke check (pytest → /health → /optimize-energy)
├── .env.example             # Template; real .env is gitignored
└── README.md
```

---

## Known Limitations & Assumptions

1. **Assumes feasible input per the organizer spec.** The solver returns 422 with an actionable message if valid directives make the LP infeasible (for example: a reserve that requires more energy than the battery can store within the available hours). It will **not** silently rewrite directives, in line with the rubric's "Applicable ground-truth directive not reflected in hourly_plan ⇒ invalid" rule.
2. **LLM dependency.** The service requires a reachable Gemini endpoint and a valid `GEMINI_API_KEY` for live runs. Judges are responsible for the key per the "EXTERNAL MODEL RESPONSIBILITY" clause. A local model is allowed by the rules but not configured by default.
3. **Numeric tolerance.** All internal checks use `TOL = 0.01 kWh` / `0.01 BDT`, matching the Problem Statement's default precision.
4. **End-of-day neutrality.** The LP constraint is `soc[23] ≥ initial_energy_kwh`. The battery may finish the day with more energy than it started, provided every reserve directive is satisfied — never less.
5. **Single-tenant.** The service holds no per-user state; each request is fully self-contained.

---

## Acknowledgments & Credits

- **Python 3.11**, **FastAPI**, **Uvicorn**, **Pydantic v2** — HTTP & data layer
- **PuLP** with the bundled **CBC** solver — linear programming
- **google-genai** — Gemini 2.5 Flash SDK
- **pytest** + **httpx** — testing
- **python-dotenv** — local environment loading

Per the official rulebook, AI coding assistants and public libraries/frameworks were used; all architecture and core logic is original to the team.
