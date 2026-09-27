import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import duckdb
from pydantic import ValidationError

from specgrid_audit.adapters.distributor import CATALOG_COLUMNS, catalog_record, read_raw
from specgrid_audit.db import ingest
from specgrid_audit.models import Part
from specgrid_audit.rules import PhysicalRule, evaluate_fixtures, load_rules

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.con.close)
        self.addCleanup(self.temp.cleanup)

    def fixture_rows(self, name):
        with (FIXTURES / name).open(newline="") as handle:
            return list(csv.DictReader(handle))

    def changed_file(self, name, rows):
        path = Path(self.temp.name) / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_ingest(self, catalog=None, returns=None):
        return ingest(self.con, catalog or FIXTURES / "catalog.csv",
                      returns or FIXTURES / "returns.csv", "test")

    def evaluate(self):
        return evaluate_fixtures(self.con, load_rules(FIXTURES / "rules.json"))

    def test_fixture_end_to_end(self):
        counts = self.run_ingest()
        self.assertEqual(counts["raw_records"], 15)
        self.assertEqual(counts["parts"], 10)
        self.assertEqual(counts["application_claims"], 10)
        self.assertEqual(counts["return_lines"], 5)
        self.assertEqual(counts["rejected_records"], 0)
        self.assertEqual(counts["return_resolution"], {"EXACT": 5})
        self.assertEqual(self.evaluate(), {"SATISFIED": 11, "UNKNOWN": 1, "VIOLATION": 5})
        self.assertEqual(self.con.sql("SELECT sum(observed_cost_usd) FROM audit_input").fetchone()[0], Decimal("475.00"))
        violations = self.con.sql("SELECT sku FROM constraint_results WHERE physical_status='VIOLATION' ORDER BY sku").fetchall()
        self.assertEqual(violations, [(f"{i:04}",) for i in range(1, 6)])

    def test_raw_whitespace_and_leading_zeroes_survive(self):
        records = list(read_raw(self.con, FIXTURES / "catalog.csv", "test", CATALOG_COLUMNS))
        self.assertEqual(records[0].values["sku"], " 0001 ")
        part, claim = catalog_record(records[0])
        self.assertEqual(claim.sku, "0001")
        self.assertEqual(part.barrel_inner_diameter_mm, 408.0)
        self.assertIsNone(claim.qualifiers)
        self.assertEqual(records[1].values["qualifiers"], "N/A")
        self.assertIsNone(catalog_record(records[1])[1].qualifiers)

    def test_strict_models_do_not_coerce_numeric_strings_or_extra_fields(self):
        values = dict(part_id="x", tenant_id="t", manufacturer=" Demo ", mpn="Ab-01", product_name="Demo wheel", category="wheel")
        valid = Part(**values)
        self.assertEqual(valid.manufacturer, "Demo")
        self.assertEqual(valid.mpn, "Ab-01")
        for change in ({"center_bore_mm": "66.6"}, {"center_bore_mm": float("nan")},
                       {"center_bore_mm": -1.0}, {"unexpected": 1}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                Part(**values, **change)

    def test_bad_units_are_quarantined_not_guessed(self):
        rows = self.fixture_rows("catalog.csv")
        rows[0]["barrel_inner_diameter_mm"] = "16 in"
        counts = self.run_ingest(catalog=self.changed_file("catalog.csv", rows))
        self.assertEqual(counts["rejected_records"], 1)
        self.assertEqual(counts["application_claims"], 9)
        self.assertEqual(counts["raw_records"], 15)
        self.assertEqual(counts["return_resolution"], {"EXACT": 4, "UNRESOLVED": 1})

    def test_ambiguous_sku_never_fans_out_return_cost(self):
        rows = self.fixture_rows("catalog.csv")
        rows.append(dict(rows[0], mpn="DIFFERENT-PART"))
        counts = self.run_ingest(catalog=self.changed_file("catalog.csv", rows))
        self.assertEqual(counts["return_resolution"], {"AMBIGUOUS": 1, "EXACT": 4})
        self.assertEqual(self.con.sql("SELECT sum(observed_cost_usd) FROM audit_input WHERE sku='0001'").fetchone()[0], Decimal("0"))
        self.evaluate()
        statuses = self.con.sql("SELECT DISTINCT physical_status FROM constraint_results WHERE sku='0001'").fetchall()
        self.assertEqual(statuses, [("UNKNOWN",)])

    def test_repeated_identical_return_is_not_double_counted(self):
        rows = self.fixture_rows("returns.csv")
        rows.append(rows[0].copy())
        counts = self.run_ingest(returns=self.changed_file("returns.csv", rows))
        self.assertEqual(counts["duplicate_return_rows"], 1)
        self.assertEqual(counts["return_lines"], 5)
        self.assertEqual(self.con.sql("SELECT sum(observed_cost_usd) FROM audit_input").fetchone()[0], Decimal("475"))

    def test_conflicting_return_id_fails_closed(self):
        rows = self.fixture_rows("returns.csv")
        rows.append(dict(rows[0], freight_usd="999.00"))
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate return"):
            self.run_ingest(returns=self.changed_file("returns.csv", rows))

    def test_conflicting_part_specs_fail_closed(self):
        rows = self.fixture_rows("catalog.csv")
        rows.append(dict(rows[0], barrel_inner_diameter_mm="900"))
        with self.assertRaisesRegex(ValueError, "Conflicting specifications"):
            self.run_ingest(catalog=self.changed_file("catalog.csv", rows))

    def test_platform_is_part_of_return_resolution(self):
        rows = self.fixture_rows("returns.csv")
        rows[0]["platform"] = "Audi RS6 Avant C8"
        counts = self.run_ingest(returns=self.changed_file("returns.csv", rows))
        self.assertEqual(counts["return_resolution"], {"EXACT": 4, "UNRESOLVED": 1})

    def test_costs_are_line_totals_not_multiplied_by_quantity(self):
        rows = self.fixture_rows("returns.csv")
        rows[0]["quantity"] = "2"
        self.run_ingest(returns=self.changed_file("returns.csv", rows))
        self.assertEqual(self.con.sql("SELECT returned_units, observed_cost_usd FROM audit_input WHERE sku='0001'").fetchone(), (2, Decimal("80")))

    def test_duplicate_headers_fail_before_duckdb_renames_them(self):
        path = Path(self.temp.name) / "duplicate.csv"
        path.write_text("sku, SKU \n0001,0002\n")
        with self.assertRaisesRegex(ValueError, "duplicate headers"):
            list(read_raw(self.con, path, "test", CATALOG_COLUMNS))

    def test_rule_cannot_compare_incompatible_units(self):
        payload = json.loads((FIXTURES / "rules.json").read_text())[0]
        payload["unit"] = "kg"
        with self.assertRaises(ValidationError):
            PhysicalRule.model_validate(payload)

    def test_boundary_equality_is_satisfied(self):
        rows = self.fixture_rows("catalog.csv")
        rows[0]["barrel_inner_diameter_mm"] = "416"
        self.run_ingest(catalog=self.changed_file("catalog.csv", rows))
        self.evaluate()
        self.assertEqual(self.con.sql("SELECT physical_status FROM constraint_results WHERE sku='0001' AND rule_id='WHEEL_RADIAL_CLEARANCE'").fetchone()[0], "SATISFIED")

    def test_ids_and_evaluations_are_repeatable(self):
        self.run_ingest()
        self.evaluate()
        expected = self.con.sql("SELECT * FROM constraint_results ORDER BY ALL").fetchall()
        with duckdb.connect() as other:
            ingest(other, FIXTURES / "catalog.csv", FIXTURES / "returns.csv", "test")
            evaluate_fixtures(other, load_rules(FIXTURES / "rules.json"))
            self.assertEqual(other.sql("SELECT * FROM constraint_results ORDER BY ALL").fetchall(), expected)

    def test_decimal_margin_does_not_create_false_violation(self):
        self.run_ingest()
        self.con.execute("UPDATE parts SET center_bore_mm=0.3")
        self.con.execute("UPDATE application_claims SET hub_diameter_mm=0.1")
        rule = load_rules(FIXTURES / "rules.json")[1].model_copy(update={"minimum_margin": 0.2})
        counts = evaluate_fixtures(self.con, [rule])
        self.assertEqual(counts, {"SATISFIED": 7})


if __name__ == "__main__":
    unittest.main()
