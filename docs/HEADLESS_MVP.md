# Headless MVP constraint engine

Status: broader implementation blueprint. The initial Python/DuckDB ingestion scaffold and synthetic wheel/suspension constraint fixtures are implemented in [engine/](../engine/README.md). The general rule language, sourced reference data, and production audit workflow below remain future work.

## First deliverable

Build a reproducible local Python CLI that accepts a distributor's catalog, supplier evidence, and optional return logs, then emits a ranked, traceable review queue. Start with one supplier and engine-code exclusions in one powertrain category. Broader categories reuse the schema and acquire their own validated rule packs.

The engine audits claims in data. It cannot establish physical clearance or discover an undocumented engine swap without evidence. An absent qualifier is not automatically an unrestricted compatibility claim.

Use Python for adapters, explicit normalization, rule interpretation, and reports; DuckDB for local joins and aggregation; Pydantic strict models after deliberate parsing. Pin dependencies in the eventual engine project. Keep the landing page build-free. Go becomes useful if profiling shows an evaluation bottleneck or customers require a portable executable; share versioned rule fixtures before porting semantics. Kubernetes is unnecessary for this pilot.

DuckDB supports `all_varchar=true` on CSV reads, which avoids type inference stripping leading zeroes from identifiers. Strict Pydantic validation rejects inappropriate coercion after the adapter has deliberately converted types.

Sources: [DuckDB CSV import](https://duckdb.org/docs/stable/data/csv/overview), [Pydantic strict mode](https://pydantic.dev/docs/validation/2.12/concepts/strict_mode/).

## Data contract

Every dataset is tenant-scoped. Retain original values and source row references. Use stable UUIDs or hashes for internal identifiers, not a product description or a concatenated year/make/model string.

| Entity | Minimum fields and purpose |
| --- | --- |
| `source_snapshot` | `source_id`, tenant, kind, filename, SHA-256, import time, supplier revision, effective dates, permitted-use metadata. Identifies exactly which evidence was used. |
| `raw_record` | Snapshot ID, stable CSV record ordinal, original text fields, parse errors. A multiline CSV record is not a physical file line. |
| `part` | Part ID, manufacturer namespace, raw MPN, approved normalized MPN, revision, category, optional validated GTIN. |
| `part_alias` | Tenant/source namespace, distributor SKU, part ID, effective interval, match method, review status. One SKU is not a global product identity. |
| `vehicle_configuration` | Internal ID, optional licensed reference ID plus namespace/version, market, model-year range, make/model/generation, body variant, engine code, transmission code, drive type, relevant factory options, validity interval. Store only observed or authoritative combinations. |
| `application_claim` | Claim ID, part ID, supplier revision, include/exclude selector AST, explicit wildcard markers, qualifier text, source record, effective interval. The selector represents a set of configurations. |
| `configuration_fact` | Subject ID (vehicle/configuration/build), typed attribute, value or allowed set, unit, state (`KNOWN`, `UNKNOWN`, `NOT_APPLICABLE`), evidence ID, observation date. Factory facts and observed modifications remain distinguishable. |
| `constraint` | Rule ID/version, subject part/category, applicability selector, requirement AST, completeness assertion if applicable, source evidence, effective interval, reviewer approval. |
| `return_line` | Unique RMA line, optional pseudonymous order-line key, SKU/part, optional configuration/build ID, date, reason code, note, quantity, distinct cost components and currency. Repeated return events do not create new return lines. |
| `sales_exposure` | Optional part/configuration cohort, time window, units sold, currency. Required for return rates and projections, not contradiction detection. |
| `finding` | Finding ID, run ID, claim, rule/version, witness configuration, outcome, evidence references, resolution method/status, suggested correction, reviewer decision. |
| `audit_run` | Input hashes, adapter/alias/rule/reference versions, code commit, configuration, coverage counts, artifact hashes. Supports deterministic reruns. |

Typed attributes must distinguish a missing value from a literal `ANY` claim. Relevant extensions include `engine_code`, `induction`, `transmission_code`, `drive_type`, `body_variant`, `bumper_package`, `ride_height_mm`, and `installed_part_ids`. Displacement alone is not an engine identity. A vehicle's original factory configuration is not evidence of its current modified configuration.

Retain ACES/PIES reference IDs when provided with their database version and namespace. Auto Care's supporting databases are subscription based; use a permitted reference snapshot rather than assuming an open API or that the customer's license transfers to SpecGrid. A CSV pilot can run on the partner's supplied facts without claiming complete vehicle-universe coverage. [Auto Care database documentation](https://www.autocare.org/data-and-information/data-standards/databases).

## Entity resolution

1. Map column names using a reviewed adapter. Import identifiers as strings. Normalize Unicode, whitespace, enums, dates, and units explicitly; log every transformation. Preserve original values. Quarantine malformed rows with reasons.
2. Match a product by a unique, version-valid distributor SKU mapping; otherwise use manufacturer namespace plus MPN and revision. Use punctuation/case folding only where an approved manufacturer-specific policy permits it. Validated GTIN is supporting evidence, not permission to overwrite contradictory product identity.
3. Resolve vehicle candidates by market, year interval, manufacturer/model/generation, then intersect explicit engine, body, gearbox, drive, and package facts. Never generate a Cartesian product of independently valid attributes. Shared trim names across markets do not prove identity.
4. Represent broad application selectors as sets. Multiple configurations can be intentional; do not turn a broad claim into an arbitrary best match. For a supposedly concrete vehicle/build with several candidates, return `AMBIGUOUS` and preserve candidates.
5. Use fuzzy matching to propose candidates for human review. Do not auto-link similar MPNs, names, or embeddings. Store each accepted mapping with reviewer, source, and version so later runs can reuse it.
6. Parse supplier qualifiers using a small, tested grammar (`requires`, `excludes`, `only`, conditional clauses). Unparsed language enters the review queue. An optional language model may draft a structured rule with quoted evidence, but that rule remains inactive until approved. Keep distributor data local unless external processing is separately authorized.

Identity outcomes are `EXACT`, `APPROVED_ALIAS`, `AMBIGUOUS`, and `UNRESOLVED`. Rule confidence never compensates for ambiguous identity.

## Rule representation and semantics

Use a constrained JSON AST with allowlisted operators: `all`, `any`, `not`, `eq`, `in`, `range`, `requires`, and `excludes`. Validate attribute types, units, recursion/size limits, and operator arity before evaluation. Never execute rule strings with `eval`.

Illustrative rule (fictional identifiers):

```json
{
  "rule_id": "PT-001",
  "version": 1,
  "subject_part_id": "demo-part-19",
  "applies_when": {"eq": ["generation", "DEMO-G1"]},
  "requires": {"in": ["engine_code", ["DEMO-T2"]]},
  "evidence": {"source_id": "supplier-v3", "record": 42},
  "approval": "reviewed"
}
```

Use three-valued predicate logic: `TRUE`, `FALSE`, and `UNKNOWN`. `not UNKNOWN` remains `UNKNOWN`; a missing engine code does not make an engine exclusion false. An installed-part dependency is unknown unless the supplied inventory is explicitly complete or contains the required part.

Rule outcomes:

- `NOT_APPLICABLE`: applicability is provably false.
- `CONTRADICTION`: applicability is true and the requirement is provably false on a resolved configuration.
- `NO_CONFLICT_FOUND`: applicability and requirement are true for this rule; this is not a universal fitment certification.
- `INSUFFICIENT_DATA`: applicability or requirement cannot be determined.
- `DISPUTED_EVIDENCE`: applicable sources disagree and source precedence/supersession cannot settle the conflict.

For an application claim representing set C, an authoritative requirement allowing set A implies a contradiction when there is a supported witness in `C minus A`. Apply all existing exclusions before searching for a witness. `ANY` must be an explicit claim, and enumerated witnesses must come from real supplied/reference configurations. If the engine cannot establish the relevant universe or source completeness, emit `MISSING_QUALIFIER`/`INSUFFICIENT_DATA` for review instead of inventing a witness. Keep finding type separate from evaluation outcome.

Examples: a powertrain product limited to an engine code; an aero product requiring a particular bumper variant; a drivetrain part excluding a transmission code; a suspension product requiring a documented ride-height range. Activate each only with category-specific evidence. Diameter, offset, or generic vehicle identity alone cannot prove three-dimensional wheel/brake clearance.

## Execution flow

1. **Snapshot:** fingerprint input bytes and persist an immutable run manifest. Reject duplicate headers, incompatible mappings, and inconsistent declared currencies/units; record row-level rejects separately.
2. **Normalize:** apply the reviewed adapter and validate typed records. No silent row loss or identifier conversion.
3. **Resolve:** create parts, aliases, application sets, and evidenced configuration candidates. Quarantine unresolved identities without removing them from coverage denominators.
4. **Compile:** validate approved rules, scope and version them, and index by part/category and configuration attributes. Reject malformed rules and flag contradictory rules with overlapping scope/time.
5. **Evaluate:** join claims only to relevant rules/configurations; apply qualifiers and modifications with explicit provenance. Preserve unknowns, witnesses, and complete evidence chains.
6. **Prioritize:** rank confirmed contradictions and credible missing qualifiers using return evidence. Join returns exactly where possible; SKU-only returns support SKU-level investigation, not attribution to a specific configuration.
7. **Export:** write `findings.csv`, `findings.jsonl`, `unresolved.csv`, `coverage.json`, `manifest.json`, and a readable HTML report. Escape HTML and protect spreadsheet-facing CSV cells against formula injection while preserving raw values in JSONL. Never mutate the source catalog.

Cache by tenant, input content, reference data, adapter, alias, and rule versions; changes to any must invalidate the affected results. A local DuckDB file and artifact directory per audit are enough. Bound expansion of broad selectors and report truncation as incomplete coverage.

## Quantitative ranking

Begin with observed costs and a transparent review priority. Distinguish technical severity, evidence quality, and financial impact. Returns are corroborating signals, not ground truth: an installation error can produce a `doesn't fit` note.

Observed recoverable handling cost can include return freight, incremental labor, replacement freight, and irrecoverable write-down, less applicable recoveries. Do not treat a refunded retail price as incremental lost profit. Deduplicate return lines and allocate shared shipment costs once; multiple findings must not multiply the same loss.

With matching sales exposure and a sufficiently mature return window, a scenario estimate is:

`future units × estimated fitment-return probability × assumed preventable fraction × marginal cost per return`

Show assumptions and uncertainty. Use category-level shrinkage for sparse SKU histories only after defining comparable cohorts; report intervals and sample sizes. Without sales denominators, return maturity, or credible cause labels, report historical costs and counts, not return rates or promised savings.

## Proposed command and module boundaries

This interface is proposed; it is not implemented yet:

```sh
python -m specgrid_audit audit \
  --catalog private-data/catalog.csv \
  --supplier private-data/supplier.csv \
  --returns private-data/returns.csv \
  --mapping mappings/partner.yaml \
  --rules rules/powertrain.json \
  --out private-data/runs/pilot-001
```

The future package should contain `ingest.py`, `models.py`, `normalize.py`, `resolve.py`, `rules.py`, `evaluate.py`, `rank.py`, `report.py`, and a thin `__main__.py`. Separate schema, rules, source adapters, and fixtures. Keep real exports and findings out of Git; use synthetic fixtures and content hashes in reproducibility manifests.

## Pilot acceptance

Implement the engine-code qualifier family first, then validate blind against a distributor-reviewed sample of both findings and apparently clean records. Report precision, recall within the labeled sample, review time, resolution rate, rule coverage, and unknown rate separately. Do not claim total-catalog recall from reviewing only flagged rows.

Required fixtures: explicit all-engines claim with a supported disallowed witness; permitted engine; already-present exclusion; missing engine; unsupported physical geometry; ambiguous SKU; conflicting supplier revisions; equivalent units; expired evidence; conditional negation; duplicate returns; and idempotent reruns. Keep new, unseen supplier records in the evaluation set to expose overfitting.

The pilot succeeds when reviewers accept useful, evidenced corrections with less effort than manual investigation. Expand to aero/bumper, drivetrain/transmission, and suspension/package rule packs only after their own labeled validation. Broad positioning is a roadmap; measured rule coverage is the product's current capability.
