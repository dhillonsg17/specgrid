# SpecGrid local engine

Runnable ingestion scaffold and synthetic physical-rule smoke test. Python 3.11+, DuckDB, and Pydantic v2; no server, pandas, or cloud infrastructure.

## Run

From this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock -e .
python -m specgrid_audit \
  --catalog fixtures/catalog.csv \
  --returns fixtures/returns.csv \
  --rules fixtures/rules.json \
  --out runs/first-audit
python -m unittest discover -s tests -v
```

Choose a new output directory for each run. Existing output directories are never overwritten. Input fixtures are read-only; generated databases, runs, virtual environments, and real private data are ignored by Git. Real imports belong in `private-data/`.

## Structure

```text
engine/
  pyproject.toml
  requirements.lock
  fixtures/
    catalog.csv
    returns.csv
    rules.json
  src/specgrid_audit/
    models.py                 # Strict canonical entities
    adapters/distributor.py   # CSV contract, parsing, reviewed aliases
    db.py                     # DuckDB tables, identity joins, aggregation
    rules.py                  # Validated rule contracts and tiny evaluator
    __main__.py               # Local CLI and artifacts
  tests/test_pipeline.py
  runs/                       # Ignored local output
```

## Expected result

The fixtures have 10 catalog rows, 5 unique return lines, and 3 rules. Ingestion creates 10 parts and 10 application claims, no rejected rows, and 5 exact return matches. Evaluation emits 17 applicable part-rule results: 5 `VIOLATION`, 11 `SATISFIED`, and 1 `UNKNOWN`. The five return lines total $475.00 in fictional incremental costs. Those are observed fixture costs, not predicted savings.

| SKU | Expected issue | Related return |
| --- | --- | --- |
| 0001 | 408 mm barrel < 410 mm envelope + 6 mm diametral margin | R-001 |
| 0002 | 66.5 mm bore < 66.6 mm hub | R-002 |
| 0003 | 1050 kg kit limit < 1120 kg front-axle requirement | R-003 |
| 0004 | 438 mm barrel < 440 mm envelope + 6 mm margin | R-004 |
| 0005 | 66.4 mm bore < 66.6 mm hub | R-005 |
| 0006–0009 | Passing controls for the applicable inequalities | None |
| 0010 | Missing barrel measurement yields `UNKNOWN` | None |

**All manufacturers, MPNs, measurements, and return incidents are fictional.** Platform names provide recognizable test labels; none of the dimensions or axle loads are verified BMW/Audi specifications. `DM-*` products are synthetic multi-piece wheels and `DD-*` products are synthetic coilover kits.

The catalog embeds a synthetic test configuration in each row (`brake_envelope_diameter_mm`, `hub_diameter_mm`, `front_axle_load_kg`). A real distributor export will often lack these fields: missing physical evidence must remain unknown. Move configuration facts to a sourced reference dataset when connecting a real partner.

## Data behavior

- DuckDB reads every cell as text and preserves empty strings and sentinel tokens in `raw_records`. The original files plus SHA-256 hashes remain the source of truth for original bytes. Header spelling is normalized; row numbers are data-record ordinals, not physical file lines.
- The adapter explicitly converts documented units, integers, dates, enums, and money. Identifiers retain leading zeroes. Manufacturer and platform casing is normalized under this adapter's declared policy; MPN case and punctuation are preserved.
- Canonical models reject unknown fields, inappropriate coercions, negative/invalid dimensions, non-finite values, and invalid costs. Optional descriptive null tokens become `None`; missing money is rejected rather than treated as zero.
- Platform aliases are an explicit lookup. Unknown aliases and malformed values are quarantined with their source IDs. Broken CSV structure, unexpected/duplicate headers, conflicting specifications for the same part, and conflicting duplicate return IDs fail the run.
- Part identity is tenant + normalized manufacturer + exact MPN. A SKU mapping to multiple parts is ambiguous. Return resolution requires a unique SKU + platform + model-year application within the tenant; ambiguous/unmatched returns stay visible in `return_matches`.
- Exact duplicate returns are counted once; conflicting duplicates abort. Costs are line totals and are aggregated before joining claims. Multiple rule results do not multiply return costs. Unmatched return cost remains in `return_lines`, not attributed to a guessed claim.
- This pilot materializes each input file in memory and uses explicit typed row inserts for clarity. Batch/Arrow loading can replace inserts after profiling realistic files; the contracts and joins need not change.

## Outputs and inspection

`audit.duckdb` contains raw records, normalized parts/claims/returns, rejects, return matches, the `audit_input` view, and `constraint_results`. Corresponding JSONL review outputs and a manifest are also written. Monetary values are serialized as decimal strings in JSONL.

```python
import duckdb

with duckdb.connect("runs/first-audit/audit.duckdb", read_only=True) as con:
    con.sql("SELECT * FROM audit_input ORDER BY sku").show()
    con.sql("SELECT * FROM constraint_results WHERE physical_status = 'VIOLATION'").show()
    con.sql("SELECT * FROM return_matches WHERE match_status <> 'EXACT'").show()
```

## Constraint scope

The evaluator supports only three allowlisted, unit-checked `part_measurement >= configuration_requirement + margin` comparisons. It never executes expressions from JSON. Rules carry versions and explicit synthetic evidence.

The radial check assumes concentric profiles measured at the same axial cross-section: a 3 mm radial gap requires a 6 mm difference in diameters. It cannot certify spoke/caliper clearance, offsets, swept steering clearance, or full 3D fitment. Bore comparisons also omit manufacturing tolerances and locating-ring requirements. The axle-load check compares a fictional kit approval limit with a fictional configuration requirement.

Free-text qualifiers are preserved but not parsed. `VIOLATION` is a failure of the supplied physical test configuration; it is not proof that the catalog actually promises that configuration. Missing qualifiers trigger review; existing qualifiers may already exclude the configuration. `SATISFIED` means only that this inequality passed, never that a part is universally safe or compatible. Return reason codes do not control physical evaluation.

There is no reference-data licensing integration, fuzzy matching, LLM extraction, cloud pipeline, broad qualifier grammar, sales-denominator model, or production fitment certification in this scaffold. The [broader blueprint](../docs/HEADLESS_MVP.md) describes those future boundaries.

Implementation references: [DuckDB CSV reader](https://duckdb.org/docs/stable/data/csv/overview), [Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/).
