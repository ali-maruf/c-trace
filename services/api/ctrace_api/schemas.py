"""Request bodies for the C-Trace API (Pydantic v2). Responses are the store's JSON-ready dicts."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

PERIOD = r"^\d{4}-(0[1-9]|1[0-2])$"
FactoryType = Literal["knit_composite", "woven", "denim_wash", "sweater"]


class FactoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    factory_type: FactoryType
    workers: int = Field(gt=0)
    has_wet_processing: bool = False
    captive_gas_gen: bool = False


class AssetIn(BaseModel):
    kind: Literal["solar", "boiler", "genset", "chiller"]
    capacity: float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=20)
    from_period: str = Field(pattern=PERIOD)


Carrier = Literal["natural_gas", "grid_electricity", "diesel"]


class InterventionIn(BaseModel):
    measure: str = Field(min_length=1, max_length=300)
    from_period: str = Field(pattern=PERIOD)
    capex_bdt: float | None = Field(default=None, ge=0)
    notes: str | None = None
    affects: list[Carrier] | None = Field(default=None, description="Carriers the measure changes; inferred from the text if omitted")


class ProductionMonth(BaseModel):
    period: str = Field(pattern=PERIOD)
    pieces: float = Field(ge=0)


class ProductionIn(BaseModel):
    months: list[ProductionMonth] = Field(min_length=1)
    source: str = Field(min_length=1, max_length=300, description="Where these figures come from, e.g. 'ERP export Mar 2026'")


class ReviewIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class CorrectionIn(BaseModel):
    activity: Literal["natural_gas", "grid_electricity", "diesel"] | None = None
    period: str | None = Field(default=None, pattern=PERIOD)
    quantity: float | None = Field(default=None, gt=0)
    amount_bdt: float | None = Field(default=None, ge=0)
    bill_no: str | None = None
    supplier: str | None = None
    reason: str = Field(min_length=3, max_length=1000, description="Stored in the audit log")

    def changes(self) -> dict:
        return self.model_dump(exclude_none=True, exclude={"reason"})


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class OfficialFactorIn(BaseModel):
    activity: Literal["grid_electricity"] = "grid_electricity"
    region: str = Field(default="BGD", pattern=r"^[A-Z]{3}$")
    kgco2e_per_unit: float = Field(gt=0, le=2, description="kgCO2e per kWh")
    unit: Literal["kWh"] = "kWh"
    valid_from: date
    valid_to: date | None = None
    source: str = Field(min_length=5, max_length=300, description="Publication name, e.g. the national grid emission factor notice")
    source_url: str = Field(pattern=r"^https?://", max_length=500)
    notes: str | None = Field(default=None, max_length=1000)


class ResolveIn(BaseModel):
    resolution: Literal["confirmed_correct", "corrected", "evidence_requested", "not_an_issue"]
    comment: str = Field(min_length=3, max_length=2000, description="What the reviewer checked; stored in the audit log")


class SignIn(BaseModel):
    comment: str = Field(min_length=3, max_length=2000, description="What the reviewer checked before signing")


class OnboardingIn(BaseModel):
    organization_name: str = Field(min_length=2, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)


class ReportIn(BaseModel):
    format: Literal["buyer", "ifrs_s2", "bsec"]
    period_from: str = Field(pattern=PERIOD)
    period_to: str = Field(pattern=PERIOD)


class ShareIn(BaseModel):
    days: int = Field(default=14, ge=1, le=90)
    include_documents: bool = Field(default=False, description="Let the viewer open the original bills behind each tonne")


class InviteIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    role: Literal["owner", "manager", "viewer", "reviewer", "buyer", "lender"]


class RoleIn(BaseModel):
    role: Literal["owner", "manager", "viewer", "reviewer", "buyer", "lender"]


class DeleteFactoryIn(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=200)
