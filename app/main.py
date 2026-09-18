import logging
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas import OptimizeEnergyRequest, OptimizeEnergyResponse
from app.llm_interpreter import interpret_operator_notes
from app.guardrails import guardrail_validate
from app.directive_engine import build_constraint_arrays
from app.optimizer import solve_energy_optimization
from app.replay_validator import replay_validate

logger = logging.getLogger("gridwise")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="GridWise API",
    description="Smart Campus Energy Optimization backend service",
    version="1.0.0",
)


from fastapi.encoders import jsonable_encoder

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Validation error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": jsonable_encoder(exc.errors()), "error": "Invalid request schema or parameters"},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled server error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


def generate_plan_summary(directives, totals) -> str:
    applied_types = [d.directive_type for d in directives if d.applies and d.directive_type != "no_op"]
    if applied_types:
        directives_str = f"Applied directives: {', '.join(applied_types)}."
    else:
        directives_str = "No operational directives applied."

    return (
        f"24-hour cost-optimal schedule calculated. {directives_str} "
        f"Total Grid Energy: {totals['total_grid_kwh']:.2f} kWh, Total Cost: {totals['total_cost_bdt']:.2f} BDT, "
        f"Peak Grid Demand: {totals['peak_grid_kwh']:.2f} kWh."
    )


@app.post("/optimize-energy", response_model=OptimizeEnergyResponse)
async def optimize_energy(req: OptimizeEnergyRequest):
    # 1. LLM interpretation call
    raw_directives = await interpret_operator_notes(req.operator_notes)

    # 2. Guardrail validation
    directives = guardrail_validate(raw_directives, req)

    # 3. Directive engine constraint building
    constraint_arrays = build_constraint_arrays(req.hours, req.battery, directives)

    # 4. LP optimization solve — catch infeasibility cleanly
    try:
        hourly_plan, totals = solve_energy_optimization(req.hours, req.battery, constraint_arrays)
    except ValueError as exc:
        logger.error(f"LP solver infeasible for scenario '{req.scenario_id}': {exc}")
        return JSONResponse(
            status_code=422,
            content={
                "scenario_id": req.scenario_id,
                "error": "Optimization infeasible",
                "detail": str(exc),
                "directive_interpretation": [d.model_dump() for d in directives],
            },
        )


    # 5. Internal replay validation check
    replay_validate(hourly_plan, req, directives, totals)

    # 6. Generate summary
    summary = generate_plan_summary(directives, totals)

    return OptimizeEnergyResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=totals["total_grid_kwh"],
        total_cost_bdt=totals["total_cost_bdt"],
        peak_grid_kwh=totals["peak_grid_kwh"],
        plan_summary=summary,
    )
