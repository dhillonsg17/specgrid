"""Run with python -m specgrid_audit --help."""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import duckdb

from specgrid_audit.db import dictionaries, ingest, jsonl
from specgrid_audit.rules import evaluate_fixtures, load_rules


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize local catalog data and evaluate synthetic physical fixtures.")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--returns", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New output directory; existing directories are not overwritten")
    parser.add_argument("--tenant", default="demo-distributor")
    args = parser.parse_args()
    if not args.tenant.strip():
        parser.error("--tenant cannot be blank")
    rules = load_rules(args.rules)
    args.out.mkdir(parents=True, exist_ok=False)
    with duckdb.connect(str(args.out / "audit.duckdb")) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            summary = ingest(con, args.catalog, args.returns, args.tenant.strip())
            summary["physical_results"] = evaluate_fixtures(con, rules)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        for name in ("audit_input", "rejected_records", "return_matches", "constraint_results"):
            jsonl(args.out / f"{name}.jsonl", dictionaries(con, f'SELECT * FROM "{name}" ORDER BY ALL'))
        # Print a compact clean table. The complete joined view is in audit.duckdb.
        con.sql("""SELECT sku, platform, category, identity_status, return_count,
                   observed_cost_usd FROM audit_input ORDER BY sku""").show(max_rows=30)
    manifest = {
        "adapter_version": "synthetic-distributor-v1", "synthetic": True,
        "tenant": args.tenant.strip(),
        "inputs": {k: {"filename": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                   for k, p in (("catalog", args.catalog), ("returns", args.returns), ("rules", args.rules))},
        "packages": {p: version(p) for p in ("specgrid-audit", "duckdb", "pydantic")},
        "summary": summary,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"Saved {args.out / 'audit.duckdb'}")


if __name__ == "__main__":
    main()
