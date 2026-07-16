# Spreadsheet Rescue 1.0.0

The first public release includes:

- conservative CSV header and whitespace cleanup;
- exact-row deduplication after normalization;
- explicitly configured date, currency, and phone normalization;
- dry-run analysis with zero output writes;
- cleaned CSV plus JSON and Markdown audit reports;
- aggregate formula-injection warnings with an opt-in neutralization policy;
- strict malformed-CSV rejection, no-overwrite output handling, and rollback of
  partial outputs; and
- a synthetic demo and 16-test verification suite.

Runtime: Python 3.10 or newer, standard library only.

The CLI processes one CSV in memory. It does not preserve XLSX formulas,
formatting, charts, macros, merged cells, or multi-sheet workbooks.
Export each workbook sheet to CSV before processing it with this release.
