"""Three synthetic physical inequalities; deliberately not a production rule DSL."""

from pathlib import Path
from decimal import Decimal
from typing import Annotated, Literal

import duckdb
from pydantic import Field, TypeAdapter, model_validator

from specgrid_audit.db import dictionaries
from specgrid_audit.models import Category, Contract, Text

PAIRS = {
    "barrel_inner_diameter_mm": ("brake_envelope_diameter_mm", "wheel", "mm"),
    "center_bore_mm": ("hub_diameter_mm", "wheel", "mm"),
    "max_front_axle_load_kg": ("front_axle_load_kg", "suspension", "kg"),
}


class PhysicalRule(Contract):
    rule_id: Text
    version: Annotated[int, Field(ge=1)]
    synthetic: Literal[True]
    category: Category
    part_field: Literal["barrel_inner_diameter_mm", "center_bore_mm", "max_front_axle_load_kg"]
    configuration_field: Literal["brake_envelope_diameter_mm", "hub_diameter_mm", "front_axle_load_kg"]
    operator: Literal["gte"]
    minimum_margin: Annotated[float, Field(ge=0)]
    unit: Literal["mm", "kg"]
    evidence: Text

    @model_validator(mode="after")
    def compatible_dimensions(self) -> "PhysicalRule":
        if PAIRS[self.part_field] != (self.configuration_field, self.category, self.unit):
            raise ValueError("Unsupported attribute pair, category, or unit")
        return self


def load_rules(path: Path) -> list[PhysicalRule]:
    rules = TypeAdapter(list[PhysicalRule]).validate_json(path.read_text())
    if len({r.rule_id for r in rules}) != len(rules):
        raise ValueError("Duplicate rule IDs")
    return rules


def evaluate_fixtures(con: duckdb.DuckDBPyConnection, rules: list[PhysicalRule]) -> dict:
    con.execute("""CREATE TABLE constraint_results (
        claim_id VARCHAR, sku VARCHAR, rule_id VARCHAR, rule_version INTEGER,
        measured DOUBLE, required DOUBLE, unit VARCHAR, physical_status VARCHAR,
        review_reason VARCHAR, evidence VARCHAR
    )""")
    for row in dictionaries(con, "SELECT * FROM audit_input ORDER BY sku, claim_id"):
        for rule in rules:
            if row["category"] != rule.category:
                continue
            measured, base = row[rule.part_field], row[rule.configuration_field]
            # Decimal arithmetic preserves decimal boundary equality (0.1 + 0.2).
            required = None if base is None else Decimal(str(base)) + Decimal(str(rule.minimum_margin))
            if row["identity_status"] != "EXACT" or measured is None or required is None:
                status, reason = "UNKNOWN", "UNRESOLVED_IDENTITY_OR_MEASUREMENT"
            elif Decimal(str(measured)) < required:
                status = "VIOLATION"
                reason = "MISSING_QUALIFIER" if row["qualifiers"] is None else "QUALIFIER_REVIEW"
            else:
                status, reason = "SATISFIED", "THIS_INEQUALITY_ONLY"
            con.execute("INSERT INTO constraint_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                row["claim_id"], row["sku"], rule.rule_id, rule.version, measured,
                required, rule.unit, status, reason, rule.evidence,
            ])
    return dict(con.execute(
        "SELECT physical_status, count(*) FROM constraint_results GROUP BY physical_status ORDER BY physical_status"
    ).fetchall())
