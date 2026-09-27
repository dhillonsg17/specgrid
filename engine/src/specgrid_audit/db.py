"""DuckDB owns storage, exact joins, and return aggregation; no pandas needed."""

import json
from collections.abc import Iterable
from pathlib import Path

import duckdb
from pydantic import BaseModel

from specgrid_audit.adapters.distributor import (
    CATALOG_COLUMNS, RETURN_COLUMNS, catalog_record, read_raw, return_record,
)
from specgrid_audit.models import Part, RejectedRecord, ReturnLine


SCHEMA = """
CREATE TABLE raw_records (
    record_id VARCHAR PRIMARY KEY, tenant_id VARCHAR, source_file VARCHAR,
    source_sha256 VARCHAR, row_number INTEGER, "values" JSON
);
CREATE TABLE parts (
    part_id VARCHAR PRIMARY KEY, tenant_id VARCHAR, manufacturer VARCHAR, mpn VARCHAR, product_name VARCHAR,
    category VARCHAR, barrel_inner_diameter_mm DOUBLE, center_bore_mm DOUBLE,
    max_front_axle_load_kg DOUBLE
);
CREATE TABLE application_claims (
    claim_id VARCHAR PRIMARY KEY, source_record_id VARCHAR, tenant_id VARCHAR,
    part_id VARCHAR, sku VARCHAR, platform VARCHAR, model_year INTEGER, qualifiers VARCHAR,
    brake_envelope_diameter_mm DOUBLE, hub_diameter_mm DOUBLE, front_axle_load_kg DOUBLE
);
CREATE TABLE return_lines (
    return_id VARCHAR, source_record_id VARCHAR, tenant_id VARCHAR, sku VARCHAR,
    platform VARCHAR, model_year INTEGER, returned_at DATE, quantity INTEGER,
    reason_code VARCHAR, note VARCHAR, freight_usd DECIMAL(12,2), handling_usd DECIMAL(12,2),
    PRIMARY KEY (tenant_id, return_id)
);
CREATE TABLE rejected_records (source_record_id VARCHAR, stage VARCHAR, error VARCHAR);
"""

JOINS = """
CREATE VIEW sku_identity AS
SELECT tenant_id, sku, count(DISTINCT part_id) AS part_count
FROM application_claims GROUP BY tenant_id, sku;

CREATE VIEW return_matches AS
SELECT r.tenant_id, r.return_id,
    CASE WHEN coalesce(s.part_count, 0) > 1 OR count(c.claim_id) > 1 THEN 'AMBIGUOUS'
         WHEN count(c.claim_id) = 1 THEN 'EXACT' ELSE 'UNRESOLVED' END AS match_status,
    CASE WHEN s.part_count = 1 AND count(c.claim_id) = 1
         THEN min(c.claim_id) ELSE NULL END AS claim_id
FROM return_lines r
LEFT JOIN sku_identity s ON r.tenant_id = s.tenant_id AND r.sku = s.sku
LEFT JOIN application_claims c
    ON r.tenant_id = c.tenant_id AND r.sku = c.sku
    AND r.platform = c.platform AND r.model_year = c.model_year
GROUP BY r.tenant_id, r.return_id, s.part_count;

CREATE VIEW audit_input AS
WITH return_stats AS (
    SELECT m.claim_id, count(*) AS return_count, sum(r.quantity) AS returned_units,
        sum(r.freight_usd + r.handling_usd) AS observed_cost_usd,
        string_agg(DISTINCT r.reason_code, ', ' ORDER BY r.reason_code) AS return_reason_codes
    FROM return_matches m
    JOIN return_lines r ON m.tenant_id = r.tenant_id AND m.return_id = r.return_id
    WHERE m.match_status = 'EXACT'
    GROUP BY m.claim_id
)
SELECT c.*, p.manufacturer, p.mpn, p.product_name, p.category,
    p.barrel_inner_diameter_mm, p.center_bore_mm, p.max_front_axle_load_kg,
    CASE WHEN s.part_count = 1 THEN 'EXACT' ELSE 'AMBIGUOUS' END AS identity_status,
    coalesce(r.return_count, 0) AS return_count,
    coalesce(r.returned_units, 0) AS returned_units,
    coalesce(r.observed_cost_usd, 0::DECIMAL(12,2)) AS observed_cost_usd,
    r.return_reason_codes
FROM application_claims c
JOIN parts p ON c.part_id = p.part_id AND c.tenant_id = p.tenant_id
JOIN sku_identity s ON c.tenant_id = s.tenant_id AND c.sku = s.sku
LEFT JOIN return_stats r ON c.claim_id = r.claim_id;
"""


def insert(con: duckdb.DuckDBPyConnection, table: str, model: BaseModel) -> None:
    """Table and column identifiers are internal constants, never CSV values."""
    values = model.model_dump()
    if table == "raw_records":
        values["values"] = json.dumps(values["values"], ensure_ascii=False)
    columns = ", ".join(f'"{key}"' for key in values)
    placeholders = ", ".join("?" for _ in values)
    con.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})', list(values.values()))


def dictionaries(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    result = con.execute(query)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def ingest(
    con: duckdb.DuckDBPyConnection, catalog: Path, returns: Path, tenant: str,
) -> dict:
    con.execute(SCHEMA)
    known_parts: dict[str, Part] = {}
    known_returns: dict[str, ReturnLine] = {}
    duplicates = 0
    for stage, path, columns in (
        ("catalog", catalog, CATALOG_COLUMNS), ("returns", returns, RETURN_COLUMNS),
    ):
        # Materialize this small pilot file before reusing the connection for inserts.
        for raw in list(read_raw(con, path, tenant, columns)):
            insert(con, "raw_records", raw)
            try:
                parsed = catalog_record(raw) if stage == "catalog" else return_record(raw)
            except ValueError as exc:  # Includes Pydantic ValidationError.
                insert(con, "rejected_records", RejectedRecord(
                    source_record_id=raw.record_id, stage=stage, error=str(exc),
                ))
                continue
            if stage == "catalog":
                part, claim = parsed
                previous = known_parts.get(part.part_id)
                if previous is not None and previous != part:
                    raise ValueError(f"Conflicting specifications for {part.manufacturer}/{part.mpn}")
                if previous is None:
                    insert(con, "parts", part)
                    known_parts[part.part_id] = part
                insert(con, "application_claims", claim)
            else:
                previous = known_returns.get(parsed.return_id)
                if previous is not None:
                    if previous.model_dump(exclude={"source_record_id"}) != parsed.model_dump(exclude={"source_record_id"}):
                        raise ValueError(f"Conflicting duplicate return ID: {parsed.return_id}")
                    duplicates += 1
                    continue
                insert(con, "return_lines", parsed)
                known_returns[parsed.return_id] = parsed
    con.execute(JOINS)
    counts = {name: con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
              for name in ("raw_records", "parts", "application_claims", "return_lines", "rejected_records")}
    counts["duplicate_return_rows"] = duplicates
    counts["return_resolution"] = dict(con.execute(
        "SELECT match_status, count(*) FROM return_matches GROUP BY match_status ORDER BY match_status"
    ).fetchall())
    return counts
