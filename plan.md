# GridWise — Full Implementation Plan
BUP CSE Fest 2026 · Online Preliminary · LLM-Assisted Energy Optimization

Stack: **Python 3.11 + FastAPI + PuLP (CBC solver) + OpenAI/Anthropic API for interpretation**
Time budget: 4-hour window. Estimated build time below assumes solo, ~4.5–5h including deploy/docs (start before question reveal on boilerplate only).

---

## 0. Repo layout

```
gridwise/
├── app/
│   ├── main.py                # FastAPI app, routes
│   ├── schemas.py             # Pydantic request/response models
│   ├── llm_interpreter.py     # LLM call + prompt
│   ├── guardrails.py          # deterministic validation of LLM output
│   ├── directive_engine.py    # directive -> constraint arrays
│   ├── optimizer.py           # PuLP model + solve
│   ├── replay_validator.py    # shared replay/validation logic (used internally + in tests)
│   └── config.py              # env vars, constants
├── tests/
│   ├── test_public_samples.py
│   └── test_guardrails.py
├── Dockerfile
├── requirements.txt
├── .env.example
├── README.md
└── run_local_check.sh
```

---

## 1. API Contract (lock this first — 10 pts, zero ambiguity allowed)

### 1.1 `GET /health`
- 200, body `{"status": "ok"}`, must respond within 60s of process start.
- No dependencies (no LLM ping, no DB) — must be instant and always green.

### 1.2 `POST /optimize-energy`

**Request** (`ScenarioRequest`):
```python
class HourEntry(BaseModel):
    hour: int              # 0-23, unique
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float

class Battery(BaseModel):
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float

class ScenarioRequest(BaseModel):
    scenario_id: str
    operator_notes: list[str]      # 1-3 items, non-empty
    hours: list[HourEntry]         # exactly 24
    battery: Battery
```

Validation on ingress (return **400**, not 500, on failure):
- `hours` has exactly 24 entries, `hour` values are the set {0..23}, no dupes.
- `operator_notes` length 1-3, all non-empty strings.
- All numeric fields present and finite.

**Response** (`OptimizationResponse`):
```python
class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: Literal["solar_reduction","minimum_battery_reserve",
                             "no_charge_window","no_discharge_window",
                             "max_grid_window","no_op"]
    structured_adjustment: dict | None
    explanation: str

class HourlyPlanEntry(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: Literal["charge","discharge","idle"]
    battery_kwh: float
    battery_energy_after_kwh: float

class OptimizationResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]     # exactly 24
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
```

### 1.3 HTTP codes
| Code | When |
|---|---|
| 200 | health OK / successful optimization |
| 400 | malformed JSON, missing fields, wrong array lengths |
| 422 | (optional) well-formed but semantically invalid (e.g. negative capacity) |
| 500 | controlled internal error — **never** a raw stack trace or leaked secret |

---

## 2. Directive Reference (from Problem Statement §04–05)

| directive_type | structured_adjustment shape | Effect on math |
|---|---|---|
| `solar_reduction` | `{"hours":[...], "factor": float}` | `effective_solar[h] = solar[h] * factor` for h in hours |
| `minimum_battery_reserve` | `{"hours":[...], "minimum_energy_kwh": float}` | `soc[h] >= max(base_min, directive_min)` for h in hours |
| `no_charge_window` | `{"hours":[...]}` | `charge[h] = 0` for h in hours |
| `no_discharge_window` | `{"hours":[...]}` | `discharge[h] = 0` for h in hours |
| `max_grid_window` | `{"hours":[...], "max_grid_kwh": float}` | `grid[h] <= max_grid_kwh` for h in hours |
| `no_op` | `null` | no effect |

Hard rules to bake into every layer (interpreter, guardrail, optimizer, replay):
- Hour windows are **start-inclusive, end-exclusive**: "1 PM–3 PM" → `[13,14]`.
- `factor` = fraction **remaining**: "80% reduction" → `factor = 0.2`.
- `hours` arrays: unique ints 0–23, ascending.
- Exactly one interpretation entry per note, `note_index` order 0..N-1.
- `no_op` is the *only* type allowed `applies=false`; every other type is `applies=true`.
- LLM must never invent demand/solar/tariff/battery values or an unsupported directive type.

---

## 3. Component-by-component plan

### 3.1 `llm_interpreter.py` — LLM call

**Goal:** turn 1-3 free-text notes into a raw (untrusted) list of directive objects, batched in one call.

- Use structured output / tool-calling / JSON mode — do not parse free text with regex.
- System prompt must contain, verbatim:
  - the 6 directive types + required adjustment shapes (table above)
  - the start-inclusive/end-exclusive hour rule with a worked example
  - the "fraction remaining" rule for `solar_reduction.factor` with a worked example (both "drop to 20%" and "80% reduction" phrasings)
  - instruction: if a note doesn't affect the 24h schedule, emit `no_op`
  - instruction: never invent a directive type outside the 6, never touch demand/tariff/battery params directly
- Few-shot examples pulled from public samples (use these 3, they cover the tricky conversions):
  - SAMPLE-01 note 0 → `solar_reduction` (absolute % framing, "roughly 25% of forecast")
  - SAMPLE-03 note 0 → `minimum_battery_reserve` (relative %, "50% of capacity" → convert to kWh)
  - SAMPLE-09 note 0 → `solar_reduction` (reduction framing, "80% reduction" → factor 0.2)
- Output schema requested from the model:
  ```json
  [{"note_index": 0, "directive_type": "...", "structured_adjustment": {...}|null, "explanation": "..."}]
  ```
- Wrap in try/except with a hard timeout (~10s). On any failure (timeout, malformed JSON, refusal): return an empty/failed marker — **do not crash**, let guardrails fall back to all-`no_op` (safe failure).
- **This call must exist in the code path that produces `directive_interpretation`.** Using an LLM only to generate `plan_summary` does NOT satisfy the mandatory requirement — verify this explicitly before submitting.

### 3.2 `guardrails.py` — deterministic validation

Runs on raw LLM output. Never trust it past this layer.

Checks, in order:
1. **Coverage**: exactly one entry per `note_index` in `0..N-1`, no missing/duplicate. If broken → rebuild list, filling any gap with `no_op`.
2. **Type allow-list**: `directive_type` must be one of the 6. Anything else → force `no_op`.
3. **Applies semantics**: `no_op` → `applies=False`, `structured_adjustment=None`. All others → `applies=True`; if adjustment is `None` or malformed for a non-no_op type → downgrade to `no_op` (safe failure, don't guess).
4. **Hours**: must be list of unique ints, each `0 <= h <= 23`, sorted ascending. If violated → attempt to fix (dedupe + sort); if still invalid (out of range) → downgrade to `no_op`.
5. **Type-specific numeric checks**:
   - `solar_reduction.factor` ∈ [0, 1] inclusive.
   - `minimum_battery_reserve.minimum_energy_kwh` finite, ≥ 0, ≤ `battery.capacity_kwh`.
   - `max_grid_window.max_grid_kwh` finite, ≥ 0.
   - Any violation → downgrade to `no_op`, log why (for your own debugging, never in the response).
6. Return the **validated, guardrailed list** — this is what goes into `directive_interpretation` in the response, and is the only thing `directive_engine.py` is allowed to consume.

### 3.3 `directive_engine.py` — directives → constraint arrays

Pure function, no side effects, used by both optimizer and replay validator (single source of truth — do not duplicate this logic anywhere else):

```python
def build_constraint_arrays(hours: list[HourEntry], battery: Battery,
                             directives: list[DirectiveInterpretation]) -> ConstraintArrays:
    effective_solar = [h.solar_kwh for h in hours]        # 24 floats
    reserve         = [battery.minimum_energy_kwh]*24      # 24 floats
    no_charge       = [False]*24
    no_discharge    = [False]*24
    grid_cap        = [float("inf")]*24

    for d in directives:
        if not d.applies:
            continue
        adj = d.structured_adjustment
        if d.directive_type == "solar_reduction":
            for h in adj["hours"]:
                effective_solar[h] *= adj["factor"]
        elif d.directive_type == "minimum_battery_reserve":
            for h in adj["hours"]:
                reserve[h] = max(reserve[h], adj["minimum_energy_kwh"])
        elif d.directive_type == "no_charge_window":
            for h in adj["hours"]:
                no_charge[h] = True
        elif d.directive_type == "no_discharge_window":
            for h in adj["hours"]:
                no_discharge[h] = True
        elif d.directive_type == "max_grid_window":
            for h in adj["hours"]:
                grid_cap[h] = min(grid_cap[h], adj["max_grid_kwh"])

    return ConstraintArrays(effective_solar, reserve, no_charge, no_discharge, grid_cap)
```

### 3.4 `optimizer.py` — LP model (PuLP + CBC)

Variables, per hour `h = 0..23`:
- `grid[h] >= 0`
- `solar_used[h] >= 0`
- `charge[h] >= 0`, `discharge[h] >= 0`
- `soc[h]` (state of charge after hour h)

Constraints:
```
1.  solar_used[h] <= effective_solar[h]
2.  grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h]
3.  soc[h] == soc[h-1] + charge[h] - discharge[h]     (soc[-1] := initial_energy_kwh)
4.  reserve[h] <= soc[h] <= capacity_kwh
5.  charge[h]    <= max_charge_kwh_per_hour;   charge[h]    == 0 if no_charge[h]
6.  discharge[h] <= max_discharge_kwh_per_hour; discharge[h] == 0 if no_discharge[h]
7.  grid[h] <= grid_cap[h]
8.  soc[23] == initial_energy_kwh                      (end-of-day neutrality)
```

Objective: `minimize sum(grid[h] * tariff[h] for h in 0..23)`

Implementation notes:
- Simultaneous charge+discharge in the same hour is never optimal under a linear cost objective with no round-trip loss modeled, so you generally don't need a binary "action" variable — but **verify** post-solve that `charge[h] * discharge[h] ≈ 0` for every hour; if the solver ever returns both nonzero (can happen at degenerate optima), pick the net direction and zero the smaller one, then re-derive `grid`/`solar_used` consistency.
- Derive `battery_action`: `"charge"` if `charge[h] > tol`, `"discharge"` if `discharge[h] > tol`, else `"idle"`. `battery_kwh` = the nonzero one, rounded; force to `0.0` exactly when idle.
- Round all outputs to avoid float noise beyond the 0.01 tolerance (e.g. round to 4 decimals internally, but ensure equality constraints like energy balance hold within 0.01 after rounding — round consistently, don't round grid and solar independently and then let the balance equation drift).
- If solver status is not `Optimal` → this indicates a bug (spec guarantees feasible ground-truth scenarios) — return a controlled 500, log internally, do not fabricate a plan.

### 3.5 `replay_validator.py` — self-check before responding

Reuses `directive_engine.build_constraint_arrays` with the **same guardrailed directive list**, then walks the returned `hourly_plan` hour by hour and asserts:
- Energy balance: `grid + solar_used + battery_discharge == demand + battery_charge` (±0.01)
- `solar_used <= effective_solar` (±0.01)
- `reserve[h] <= battery_energy_after <= capacity` (±0.01)
- Charge/discharge rate limits respected; zero in no-charge/no-discharge hours
- `grid[h] <= grid_cap[h]` (±0.01)
- `soc[23] == initial_energy_kwh` (±0.01)
- Recomputed `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` match what you're about to return (±0.01)

If any check fails: **this is a bug in your optimizer/rounding, not something to paper over** — log it, fix the root cause before the round ends. Do not ship a response you know is invalid.

### 3.6 `main.py` — orchestration

```
@app.post("/optimize-energy")
def optimize(req: ScenarioRequest):
    raw_directives = llm_interpret(req.operator_notes)          # 3.1, try/except, timeout
    directives = guardrail_validate(raw_directives, req)        # 3.2
    arrays = build_constraint_arrays(req.hours, req.battery, directives)  # 3.3
    plan, totals = solve(req.hours, req.battery, arrays)        # 3.4
    replay_validate(plan, req, directives)                      # 3.5 — internal assertion, not user-facing
    return OptimizationResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=directives,
        hourly_plan=plan,
        **totals,
        plan_summary=make_summary(directives, plan),            # cheap/templated, NOT the LLM-requirement path
    )
```

Wrap the whole handler body in try/except → on any unhandled exception, log internally and return 500 with a generic `{"error": "internal error"}` body (no stack trace, no secrets).

---

## 4. Guardrail / edge-case checklist (map directly to rubric line items)

- [ ] Note count mismatch (LLM returns 2 entries for 3 notes) → filled with `no_op`
- [ ] Duplicate `note_index` → deduped, missing one added as `no_op`
- [ ] Unsupported `directive_type` string from LLM → forced to `no_op`
- [ ] `solar_reduction.factor` outside [0,1] (e.g. LLM gives 80 instead of 0.2) → guardrail catches and either normalizes (`if factor > 1: factor/=100` — risky) or downgrades to `no_op` (safer; log which you chose and be consistent)
- [ ] Hours not ascending / duplicated / out of 0-23 range → sorted+deduped, or downgraded if still invalid
- [ ] `no_op` with non-null adjustment → forced to null
- [ ] Non-`no_op` with `applies=false` → forced to `true` (or downgrade whole entry to `no_op` if the LLM seems confused — pick one policy and be consistent)
- [ ] LLM call times out / returns non-JSON / API error → entire directive list becomes all-`no_op`, service still responds 200 with a valid (if conservative) schedule
- [ ] Contradictory directives on same hours (spec says won't happen in valid scenarios, but don't crash if it does — apply both, let LP fail cleanly if infeasible, return 500 controlled)
- [ ] Malformed request JSON → 400, not 500
- [ ] Negative or missing numeric fields in request → 400

---

## 5. Testing plan

### 5.1 Local unit tests (`tests/test_guardrails.py`)
- Feed guardrails deliberately broken LLM outputs (missing note_index, bad factor, bad hours, wrong enum) → assert correct downgrade/fix behavior for each case in the checklist above.

### 5.2 Public sample harness (`tests/test_public_samples.py`)
```python
for case in samples["cases"]:
    resp = client.post("/optimize-energy", json=case["input"])
    assert resp.status_code == 200
    body = resp.json()
    # interpretation: check type/hours/values, not free-text explanation
    for i, exp in enumerate(case["expected_output"]["directive_interpretation"]):
        got = body["directive_interpretation"][i]
        assert got["directive_type"] == exp["directive_type"]
        assert got["applies"] == exp["applies"]
        if exp["structured_adjustment"]:
            assert_close(got["structured_adjustment"], exp["structured_adjustment"])
    # schedule: validate against constraints, NOT byte-equality with expected hourly_plan
    replay_validate(body["hourly_plan"], case["input"])
    # cost should be <= expected cost + tolerance (your LP should be at least as good)
    assert body["total_cost_bdt"] <= exp_output["total_cost_bdt"] + 0.5
```
Run this after every change to the optimizer or guardrails — it's your fastest signal.

### 5.3 Paraphrase robustness spot-check
Manually test 2-3 reworded versions of each directive type (the Problem Statement gives you exact paraphrase examples for `solar_reduction` in §11.4 — reuse that pattern for the other 4 types) to sanity-check the LLM prompt generalizes before the hidden set does it for you.

### 5.4 Load / latency check
Fire all 10 public cases with `asyncio.gather` or a simple loop with timing; note p95. Target: p95 ≤ 5s for full points on that sub-metric. If the LLM call dominates latency, consider a faster/cheaper model or reducing prompt size.

### 5.5 Malformed-input fuzz
- Empty `operator_notes` array, 4+ notes, non-numeric fields, `hours` with 23 or 25 entries, missing `battery` object → confirm clean 400s, no 500s, no crashes.

---

## 6. Deployment

- **Platform**: whatever you already have working (Render/Railway/Fly/a VPS you control) — judged on reachability, not provider.
- Bind `0.0.0.0`, expose the port you document.
- Env vars: `LLM_API_KEY`, `LLM_MODEL`, `PORT` — document names in README, never commit values, never log them.
- Test both endpoints from a **different network** than your dev machine before submitting (phone hotspot works).
- Keep the service running for the entire evaluation window — don't stop your dev server or let a free-tier instance sleep.

### 6.1 Dockerfile skeleton
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
- Build, push to a registry (Docker Hub/GHCR) with an exact tag (not just `latest` — pin it).
- Verify with a clean `docker pull <image> && docker run -p 8000:8000 -e LLM_API_KEY=... <image>` then curl `/health` — from a machine that never had the repo cloned.
- No secrets baked into the image (env vars only, never `.env` copied into the image).

---

## 7. README checklist (10 pts, all mechanical — don't lose these)

- [ ] Clone/pull instructions
- [ ] Required env var **names** (not values)
- [ ] Model/provider or local model identifier used for interpretation
- [ ] LLM's role in the pipeline (one paragraph, matches the diagram)
- [ ] Guardrail description (what's checked, what happens on failure)
- [ ] Optimizer/solver used (PuLP + CBC), and how it models the objective/constraints
- [ ] Exact run command (local, no Docker)
- [ ] `/health` curl example
- [ ] `/optimize-energy` curl example using one public sample
- [ ] Dependencies list (or point to `requirements.txt`)
- [ ] Docker pull/run fallback commands, exact tag
- [ ] Known limitations (be honest — e.g. "assumes feasible input per spec guarantee")
- [ ] Explicit statement: no secrets committed; how secrets are supplied at runtime
- [ ] Credit any AI coding assistants / libraries used, per rulebook

---

## 8. Submission checklist (final pass, in order)

1. `/health` reachable externally, instant 200.
2. `/optimize-energy` reachable externally, handles all 10 public samples correctly.
3. Repo created after question reveal, private during event.
4. README complete per §7 above.
5. Docker image pushed, pullable, tested clean.
6. Repo made **public** immediately after deadline.
7. 3-minute video recorded and uploaded/linked (tie-break only — don't over-invest, but don't skip).
8. Final smoke test: run `run_local_check.sh` (clone fresh → health → one sample) exactly as a judge would.

---

## 9. Time-boxed build order (fits a 4-hour window with margin)

| Time | Task |
|---|---|
| 0:00–0:20 | Skeleton: FastAPI, `/health`, Pydantic schemas, stub `/optimize-energy` |
| 0:20–0:40 | `directive_engine.py` pure function + unit tests |
| 0:40–1:20 | `optimizer.py` LP model, validate against 2-3 public samples by hand |
| 1:20–2:00 | `llm_interpreter.py` prompt + structured output, test against all 10 public notes |
| 2:00–2:30 | `guardrails.py` full checklist from §4 |
| 2:30–2:50 | `replay_validator.py`, wire into `main.py` |
| 2:50–3:10 | Full public-sample test harness run, fix mismatches |
| 3:10–3:30 | Malformed-input fuzzing, latency check, error handling pass |
| 3:30–3:50 | Dockerfile, deploy, external reachability test |
| 3:50–4:10 | README |
| 4:10–4:30 | Final submission checklist, buffer for surprises |
| (post-window) | 3-minute video, repo made public |

Priority if you're falling behind: **schema correctness → guardrails → directive application correctness → deployment/Docker → optimization polish → docs → video**, in that order (matches rubric weight, see prior discussion).