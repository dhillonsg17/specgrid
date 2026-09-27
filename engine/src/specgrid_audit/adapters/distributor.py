"""A deliberately small adapter for the synthetic distributor's CSV contract."""

import csv
import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

import duckdb

from specgrid_audit.models import ApplicationClaim, Part, RawRecord, ReturnLine


CATALOG_COLUMNS = (
    "sku manufacturer mpn product_name category platform model_year qualifiers "
    "barrel_inner_diameter_mm center_bore_mm max_front_axle_load_kg "
    "brake_envelope_diameter_mm hub_diameter_mm front_axle_load_kg"
).split()
RETURN_COLUMNS = (
    "return_id sku platform model_year returned_at quantity reason_code note "
    "freight_usd handling_usd"
).split()
NULLS = {"", "n/a", "null", "none"}
PLATFORMS = {
    "BMW G80 M3": "BMW_G80_M3", "G80 M3": "BMW_G80_M3",
    "AUDI RS6 AVANT C8": "AUDI_C8_RS6_AVANT",
    "AUDI C8 RS 6 AVANT": "AUDI_C8_RS6_AVANT",
    "RS6 AVANT C8": "AUDI_C8_RS6_AVANT",
}


def stable_id(*values: object) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def text(value: str | None) -> str:
    return "" if value is None else value.strip()


def number(value: str | None, unit: str) -> float | None:
    clean = text(value)
    if clean.casefold() in NULLS:
        return None
    # Explicit units only: '430 mm' is accepted; '17 in' and '1,200' are not.
    match = re.fullmatch(rf"([0-9]+(?:\.[0-9]+)?)\s*(?:{unit})?", clean, re.I)
    if not match:
        raise ValueError(f"Expected a nonnegative {unit} value, received {value!r}")
    return float(match[1])


def integer(value: str | None) -> int:
    clean = text(value)
    if not re.fullmatch(r"[0-9]+", clean):
        raise ValueError(f"Expected integer, received {value!r}")
    return int(clean)


def money(value: str | None) -> Decimal:
    clean = text(value)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", clean):
        raise ValueError(f"Expected USD amount with up to two decimals: {value!r}")
    try:
        return Decimal(clean)
    except InvalidOperation as exc:
        raise ValueError("Invalid money value") from exc


def platform(value: str | None) -> str:
    key = " ".join(text(value).upper().split())
    if key not in PLATFORMS:
        raise ValueError(f"Unmapped platform: {value!r}; add an approved alias")
    return PLATFORMS[key]


def read_raw(
    con: duckdb.DuckDBPyConnection, path: Path, tenant: str, expected: list[str],
) -> Iterator[RawRecord]:
    path = path.resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        original_headers = next(csv.reader(handle), [])
    headers = [h.strip().lower() for h in original_headers]
    if len(set(headers)) != len(headers) or set(headers) != set(expected):
        raise ValueError(f"{path.name}: missing, extra, or duplicate headers: {headers}")

    # No row dropping, auto numeric conversion, or parallel order ambiguity.
    rows = con.execute(
        "SELECT * FROM read_csv(?, header=true, all_varchar=true, "
        "delim=',', quote='\"', escape='\"', force_not_null=?, "
        "strict_mode=true, ignore_errors=false, parallel=false)", [str(path), original_headers],
    )
    ordinal = 0
    while batch := rows.fetchmany(1000):
        for values in batch:
            ordinal += 1
            yield RawRecord(
                record_id=stable_id(tenant, path.name, digest, ordinal),
                tenant_id=tenant, source_file=path.name, source_sha256=digest,
                row_number=ordinal, values=dict(zip(headers, values, strict=True)),
            )


def catalog_record(raw: RawRecord) -> tuple[Part, ApplicationClaim]:
    d = raw.values
    # This fixture's manufacturer labels are case-insensitive; MPNs are not.
    manufacturer = " ".join(text(d["manufacturer"]).upper().split())
    mpn = text(d["mpn"])
    category = {"wheels": "wheel", "coilover": "suspension"}.get(
        text(d["category"]).lower(), text(d["category"]).lower(),
    )
    part = Part(
        part_id=stable_id(raw.tenant_id, manufacturer, mpn),
        tenant_id=raw.tenant_id, manufacturer=manufacturer, mpn=mpn,
        product_name=text(d["product_name"]), category=category,
        barrel_inner_diameter_mm=number(d["barrel_inner_diameter_mm"], "mm"),
        center_bore_mm=number(d["center_bore_mm"], "mm"),
        max_front_axle_load_kg=number(d["max_front_axle_load_kg"], "kg"),
    )
    claim = ApplicationClaim(
        claim_id=stable_id("claim", raw.record_id), source_record_id=raw.record_id,
        tenant_id=raw.tenant_id, part_id=part.part_id, sku=text(d["sku"]),
        platform=platform(d["platform"]), model_year=integer(d["model_year"]),
        qualifiers=d["qualifiers"],
        brake_envelope_diameter_mm=number(d["brake_envelope_diameter_mm"], "mm"),
        hub_diameter_mm=number(d["hub_diameter_mm"], "mm"),
        front_axle_load_kg=number(d["front_axle_load_kg"], "kg"),
    )
    return part, claim


def return_record(raw: RawRecord) -> ReturnLine:
    d = raw.values
    return ReturnLine(
        return_id=text(d["return_id"]), source_record_id=raw.record_id,
        tenant_id=raw.tenant_id, sku=text(d["sku"]), platform=platform(d["platform"]),
        model_year=integer(d["model_year"]), returned_at=date.fromisoformat(text(d["returned_at"])),
        quantity=integer(d["quantity"]), reason_code=text(d["reason_code"]).upper(),
        note=d["note"], freight_usd=money(d["freight_usd"]), handling_usd=money(d["handling_usd"]),
    )
