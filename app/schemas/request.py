import math
from pydantic import BaseModel, Field, field_validator, model_validator


class HourInput(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0.0)
    solar_kwh: float = Field(..., ge=0.0)
    tariff_bdt_per_kwh: float = Field(..., ge=0.0)

    @field_validator("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh")
    @classmethod
    def check_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Numeric values must be finite")
        return v


class BatteryInput(BaseModel):
    capacity_kwh: float = Field(..., gt=0.0)
    initial_energy_kwh: float = Field(..., ge=0.0)
    minimum_energy_kwh: float = Field(..., ge=0.0)
    max_charge_kwh_per_hour: float = Field(..., ge=0.0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0)

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
    )
    @classmethod
    def check_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Numeric values must be finite")
        return v

    @model_validator(mode="after")
    def validate_battery_limits(self) -> "BatteryInput":
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be less than minimum_energy_kwh")
        return self


class OptimizeEnergyRequest(BaseModel):
    scenario_id: str
    operator_notes: list[str]
    hours: list[HourInput]
    battery: BatteryInput

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, notes: list[str]) -> list[str]:
        if not isinstance(notes, list) or len(notes) == 0:
            raise ValueError("operator_notes must contain at least 1 note")
        return notes

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, hours: list[HourInput]) -> list[HourInput]:
        if len(hours) != 24:
            raise ValueError(f"hours array must contain exactly 24 entries, got {len(hours)}")
        hour_set = {h.hour for h in hours}
        if len(hour_set) != 24 or hour_set != set(range(24)):
            raise ValueError("hours array must contain unique hours from 0 to 23")
        return hours


# Aliases for backward compatibility
HourEntry = HourInput
Battery = BatteryInput
ScenarioRequest = OptimizeEnergyRequest
