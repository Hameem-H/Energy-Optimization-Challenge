# GridWise — Smart Campus Energy Optimization

BUP CSE Fest 2026 · Online Preliminary · LLM-Assisted Energy Optimization

GridWise is a production-grade FastAPI service that optimizes 24-hour smart campus energy schedules. It uses Google's Gemini API to parse natural language operator notes into structured numerical constraints and solves a linear programming model using PuLP (CBC solver) to minimize total electricity cost (in BDT).

---

## Architecture Overview

```
[ POST /optimize-energy ]
           │
           ▼
┌──────────────────────────┐
│   llm_interpreter.py     │  ──> Parses operator notes via Google Gemini API (structured JSON)
└──────────┬───────────────┘
           │ (raw untrusted JSON)
           ▼
┌──────────────────────────┐
│     guardrails.py        │  ──> Deterministic validation, repair & type/range checks
└──────────┬───────────────┘
           │ (validated DirectiveInterpretation objects)
           ▼
┌──────────────────────────┐
│   directive_engine.py    │  ──> Pure function: maps directives into 24-hour constraint arrays
└──────────┬───────────────┘
           │ (ConstraintArrays: effective_solar, reserve, no_charge, no_discharge, grid_cap)
           ▼
┌──────────────────────────┐
│      optimizer.py        │  ──> PuLP + CBC linear solver: minimizes sum(grid[h] * tariff[h])
└──────────┬───────────────┘
           │ (HourlyPlanEntry array & totals)
           ▼
┌──────────────────────────┐
│   replay_validator.py    │  ──> Internal sanity check asserting energy balance & constraints
└──────────┬───────────────┘
           │
           ▼
[ OptimizationResponse JSON ]
```

---

## LLM Role & Guardrail Pipeline

### LLM Role
The LLM (`gemini-2.5-flash` via `google-genai` SDK) is strictly scoped to interpreting 1–3 free-text operator notes into structured JSON directive objects (`solar_reduction`, `minimum_battery_reserve`, `no_charge_window`, `no_discharge_window`, `max_grid_window`, `no_op`). It **never** computes energy balance or generates numerical schedules directly.

### Guardrails
All raw output from the LLM passes through `guardrails.py` before hitting the optimizer:
1. **Coverage Check:** Ensures exactly $N$ entries matching note indices `0..N-1`. Missing indices are filled with `no_op`.
2. **Type Allow-list:** Restricts `directive_type` strictly to the 6 allowed types. Unrecognized types fall back to `no_op`.
3. **Applies & Adjustment Consistency:** `no_op` forces `applies=False` and `structured_adjustment=None`. All other types force `applies=True`.
4. **Hours Array Validation:** Verifies hours are unique integers in $0..23$, sorted ascending.
5. **Type-Specific Numeric Range Checks:**
   - `solar_reduction`: `factor` $\in [0, 1]$. Normalizes percentages ($>1.0$) if necessary.
   - `minimum_battery_reserve`: $0 \le \text{minimum\_energy\_kwh} \le \text{capacity\_kwh}$.
   - `max_grid_window`: $\text{max\_grid\_kwh} \ge 0$.
   - Any unrepairable violation automatically downgrades that note to a safe `no_op`.

---

## Optimizer & Solver Specification

The linear programming solver (`optimizer.py`) is implemented using **PuLP** with the **CBC solver**.

### Decision Variables (for each hour $h \in 0..23$):
- $\text{grid}[h] \ge 0$: Grid energy purchased (kWh)
- $\text{solar\_used}[h] \ge 0$: Solar generation used (kWh)
- $\text{charge}[h] \ge 0$: Energy charged to battery (kWh)
- $\text{discharge}[h] \ge 0$: Energy discharged from battery (kWh)
- $\text{soc}[h]$: Battery state of charge at end of hour $h$ (kWh)

### Objective Function:
$$\min \sum_{h=0}^{23} (\text{grid}[h] \cdot \text{tariff}[h])$$

### Constraints:
1. $\text{solar\_used}[h] \le \text{effective\_solar}[h]$
2. $\text{grid}[h] + \text{solar\_used}[h] + \text{discharge}[h] = \text{demand}[h] + \text{charge}[h]$
3. $\text{soc}[h] = \text{soc}[h-1] + \text{charge}[h] - \text{discharge}[h] \quad (\text{soc}[-1] = \text{initial\_energy\_kwh})$
4. $\text{reserve}[h] \le \text{soc}[h] \le \text{capacity\_kwh}$
5. $\text{charge}[h] \le \text{max\_charge\_kwh\_per\_hour} \quad (\text{forced to } 0 \text{ in } \text{no\_charge\_window})$
6. $\text{discharge}[h] \le \text{max\_discharge\_kwh\_per\_hour} \quad (\text{forced to } 0 \text{ in } \text{no\_discharge\_window})$
7. $\text{grid}[h] \le \text{grid\_cap}[h]$
8. $\text{soc}[23] = \text{initial\_energy\_kwh}$ (End-of-day neutrality)

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | API key for Google Gemini API | Required for live LLM calls |
| `LLM_MODEL` | Gemini model identifier | `gemini-2.5-flash` |
| `PORT` | HTTP server port | `8000` |

*Secrets policy:* No API keys or secrets are committed to version control. Secrets are supplied at runtime via environment variables or `.env`.

---

## Running Locally

### Prerequisites
- Python 3.11+
- `pip`

### Installation & Run
```bash
# Clone repository
git clone <repository_url>
cd Smart_Campus_Energy_Optimization_hallenge

# Install dependencies
pip install -r requirements.txt

# Set environment variables (or copy .env.example to .env)
export GEMINI_API_KEY="your_api_key_here"

# Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Running Tests
```bash
python -m pytest tests/ -v
```

### Run Local Check Script
```bash
bash run_local_check.sh
```

---

## API Usage Examples

### 1. Health Check (`GET /health`)
```bash
curl -s http://localhost:8000/health
```
**Response:**
```json
{"status": "ok"}
```

### 2. Optimize Energy (`POST /optimize-energy`)
```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "scen_demo",
    "operator_notes": [
      "Solar output drops by 80% from 1 PM to 3 PM due to storm clouds.",
      "Maintain at least 5 kWh battery reserve during 6 PM to 10 PM."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 1, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 2, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 3, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 4, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 5, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 6, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 7, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 8, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 9, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 10, "demand_kwh": 6.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 11, "demand_kwh": 6.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 12, "demand_kwh": 6.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 13, "demand_kwh": 6.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 14, "demand_kwh": 6.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 15, "demand_kwh": 6.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 16, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 17, "demand_kwh": 12.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.0},
      {"hour": 18, "demand_kwh": 12.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.0},
      {"hour": 19, "demand_kwh": 12.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.0},
      {"hour": 20, "demand_kwh": 12.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.0},
      {"hour": 21, "demand_kwh": 12.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.0},
      {"hour": 22, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0},
      {"hour": 23, "demand_kwh": 6.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 3.0}
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

---

## Docker Support

### Build & Run Container
```bash
docker build -t gridwise:1.0.0 .
docker run -p 8000:8000 -e GEMINI_API_KEY="your_api_key_here" gridwise:1.0.0
```

---

## Dependencies
- FastAPI & Uvicorn
- Pydantic v2
- PuLP (with bundled CBC solver)
- Google GenAI SDK (`google-genai`)
- Pytest & HTTPX (for testing)

---

## Known Limitations & Assumptions
1. Assumes input scenario parameters (demand, solar, battery capacity) adhere to physical bounds as specified by the problem definition.
2. In case of API quota limits or LLM timeouts (>10s), the system safely falls back to `no_op` directives while producing a valid cost-optimized LP schedule.

---

## Acknowledgments
Built with Python 3.11, FastAPI, PuLP, and Google Gemini API.
#   E n e r g y - O p t i m i z a t i o n - C h a l l e n g e  
 