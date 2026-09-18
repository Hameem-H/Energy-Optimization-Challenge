from typing import Optional
from pydantic import BaseModel


class SolarReductionAdjustment(BaseModel):
    hours: list[int]
    factor: float


class MinimumBatteryReserveAdjustment(BaseModel):
    hours: list[int]
    minimum_energy_kwh: float


class NoChargeWindowAdjustment(BaseModel):
    hours: list[int]


class NoDischargeWindowAdjustment(BaseModel):
    hours: list[int]


class MaxGridWindowAdjustment(BaseModel):
    hours: list[int]
    max_grid_kwh: float


class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: str
    structured_adjustment: Optional[dict] = None
    explanation: str
