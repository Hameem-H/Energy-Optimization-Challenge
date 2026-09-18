import math
import logging
from typing import Any, Sequence
from app.schemas import DirectiveInterpretation, ScenarioRequest, Battery

logger = logging.getLogger("gridwise.guardrails")

ALLOWED_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _make_fallback_noop(idx: int, reason: str) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=idx,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation=f"Guardrail fallback to no_op: {reason}",
    )


def guardrail_validate(
    raw_llm_output: Sequence[Any],
    req: ScenarioRequest,
) -> list[DirectiveInterpretation]:
    """
    Deterministically validates, repairs, and sanitizes untrusted raw LLM output.
    Returns a clean list of DirectiveInterpretation models matching 0..N-1 notes.
    """
    n_notes = len(req.operator_notes)
    raw_by_index: dict[int, dict] = {}

    if isinstance(raw_llm_output, (list, tuple)):
        for item in raw_llm_output:
            if isinstance(item, dict):
                idx = item.get("note_index")
                if isinstance(idx, int) and 0 <= idx < n_notes and idx not in raw_by_index:
                    raw_by_index[idx] = item

    sanitized: list[DirectiveInterpretation] = []

    for idx in range(n_notes):
        item = raw_by_index.get(idx)
        if not item:
            sanitized.append(_make_fallback_noop(idx, "Missing note_index in LLM response"))
            continue

        dtype = str(item.get("directive_type", "")).strip().lower()
        if dtype not in ALLOWED_DIRECTIVE_TYPES:
            sanitized.append(_make_fallback_noop(idx, f"Unsupported directive_type '{dtype}'"))
            continue

        explanation = str(item.get("explanation", "")).strip() or f"Parsed {dtype} directive"

        if dtype == "no_op":
            sanitized.append(
                DirectiveInterpretation(
                    note_index=idx,
                    applies=False,
                    directive_type="no_op",
                    structured_adjustment=None,
                    explanation=explanation,
                )
            )
            continue

        adj = item.get("structured_adjustment")
        if not isinstance(adj, dict):
            sanitized.append(_make_fallback_noop(idx, f"Missing structured_adjustment dict for {dtype}"))
            continue

        # Check and normalize hours
        raw_hours = adj.get("hours")
        if not isinstance(raw_hours, (list, tuple)):
            sanitized.append(_make_fallback_noop(idx, f"Missing or invalid hours array for {dtype}"))
            continue

        valid_hours: set[int] = set()
        hour_error = False
        for h in raw_hours:
            if isinstance(h, int) and 0 <= h <= 23:
                valid_hours.add(h)
            else:
                hour_error = True

        if not valid_hours or hour_error:
            sanitized.append(_make_fallback_noop(idx, f"Invalid hours array content for {dtype}"))
            continue

        sorted_hours = sorted(valid_hours)
        clean_adj: dict[str, Any] = {"hours": sorted_hours}

        # Type-specific numeric validations
        if dtype == "solar_reduction":
            factor = adj.get("factor")
            if not isinstance(factor, (int, float)) or not math.isfinite(factor):
                sanitized.append(_make_fallback_noop(idx, "Invalid or missing solar factor"))
                continue
            factor_val = float(factor)
            if 1.0 < factor_val <= 100.0:
                factor_val = factor_val / 100.0
            if not (0.0 <= factor_val <= 1.0):
                sanitized.append(_make_fallback_noop(idx, f"Solar reduction factor {factor} out of range [0, 1]"))
                continue
            clean_adj["factor"] = round(factor_val, 4)

        elif dtype == "minimum_battery_reserve":
            min_kwh = adj.get("minimum_energy_kwh")
            if not isinstance(min_kwh, (int, float)) or not math.isfinite(min_kwh):
                sanitized.append(_make_fallback_noop(idx, "Invalid or missing minimum_energy_kwh"))
                continue
            min_val = float(min_kwh)
            if min_val < 0.0 or min_val > req.battery.capacity_kwh:
                sanitized.append(
                    _make_fallback_noop(
                        idx,
                        f"Reserve {min_val} kWh out of range [0, capacity={req.battery.capacity_kwh}]",
                    )
                )
                continue
            clean_adj["minimum_energy_kwh"] = round(min_val, 4)

        elif dtype == "max_grid_window":
            max_grid = adj.get("max_grid_kwh")
            if not isinstance(max_grid, (int, float)) or not math.isfinite(max_grid):
                sanitized.append(_make_fallback_noop(idx, "Invalid or missing max_grid_kwh"))
                continue
            max_val = float(max_grid)
            if max_val < 0.0:
                sanitized.append(_make_fallback_noop(idx, f"Negative max_grid_kwh {max_val}"))
                continue
            clean_adj["max_grid_kwh"] = round(max_val, 4)

        # For no_charge_window and no_discharge_window, clean_adj contains only "hours"

        sanitized.append(
            DirectiveInterpretation(
                note_index=idx,
                applies=True,
                directive_type=dtype,  # type: ignore
                structured_adjustment=clean_adj,
                explanation=explanation,
            )
        )

    return sanitized
