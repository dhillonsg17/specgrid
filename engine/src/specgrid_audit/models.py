"""Strict canonical contracts. Parse CSV strings explicitly in the adapter."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def missing_text(value: object) -> object:
    """Null tokens apply to optional descriptive text, never identifiers."""
    if isinstance(value, str):
        value = value.strip()
        if value.casefold() in {"", "n/a", "null", "none"}:
            return None
    return value


Text = Annotated[str, Field(min_length=1)]
OptionalText = Annotated[str | None, BeforeValidator(missing_text)]
Positive = Annotated[float, Field(gt=0)]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Platform = Literal["BMW_G80_M3", "AUDI_C8_RS6_AVANT"]
Category = Literal["wheel", "suspension"]
Year = Annotated[int, Field(ge=1980, le=2100)]


class Contract(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", str_strip_whitespace=True,
        allow_inf_nan=False, frozen=True,
    )


class RawRecord(Contract):
    # Raw cell values must retain their whitespace and sentinel tokens.
    model_config = ConfigDict(str_strip_whitespace=False)
    record_id: Text
    tenant_id: Text
    source_file: Text
    source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    row_number: Annotated[int, Field(ge=1)]  # Data-record ordinal, not file line.
    values: dict[str, str | None]


class Part(Contract):
    part_id: Text
    tenant_id: Text
    manufacturer: Text
    mpn: Text
    product_name: Text
    category: Category
    barrel_inner_diameter_mm: Positive | None = None
    center_bore_mm: Positive | None = None
    max_front_axle_load_kg: Positive | None = None

    @model_validator(mode="after")
    def relevant_measurements(self) -> "Part":
        if self.category == "wheel" and self.max_front_axle_load_kg is not None:
            raise ValueError("Wheel rows cannot carry a suspension axle-load rating")
        if self.category == "suspension" and any(
            x is not None for x in (self.barrel_inner_diameter_mm, self.center_bore_mm)
        ):
            raise ValueError("Suspension rows cannot carry wheel measurements")
        return self


class ApplicationClaim(Contract):
    claim_id: Text
    source_record_id: Text
    tenant_id: Text
    part_id: Text
    sku: Text
    platform: Platform
    model_year: Year
    qualifiers: OptionalText = None
    # Fixture test configurations, not authoritative OEM specifications.
    brake_envelope_diameter_mm: Positive | None = None
    hub_diameter_mm: Positive | None = None
    front_axle_load_kg: Positive | None = None


class ReturnLine(Contract):
    return_id: Text
    source_record_id: Text
    tenant_id: Text
    sku: Text
    platform: Platform
    model_year: Year
    returned_at: date
    quantity: Annotated[int, Field(gt=0)]
    reason_code: Literal["BRAKE_INTERFERENCE", "HUB_INTERFERENCE", "AXLE_LOAD_EXCEEDED"]
    note: OptionalText = None
    # Line-total incremental costs, not per-unit costs or refunded retail value.
    freight_usd: Money
    handling_usd: Money


class RejectedRecord(Contract):
    source_record_id: Text
    stage: Literal["catalog", "returns"]
    error: Text
