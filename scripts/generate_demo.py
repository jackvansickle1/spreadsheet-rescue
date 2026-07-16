"""Generate intentionally messy data about fictional composite customers."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


CONFIG = {
    "headers": {"case": "snake", "unicode_normalization": "NFKC"},
    "whitespace": {
        "trim": True,
        "collapse_internal": False,
        "unicode_normalization": None,
    },
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
            "symbols": ["$", "USD"],
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="demo", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    csv_path = args.directory / "demo_dirty.csv"
    config_path = args.directory / "demo_config.json"
    with csv_path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([" Sample Customer ", "Order Date", " Amount ", "Phone", "Notes "])
        writer.writerow(["  Avery North ", "03/14/2026", "$1,250", "(312) 555-0100", " Fictional composite - priority "])
        writer.writerow(["Avery North", "2026-03-14", "1250.00", "+13125550100", "Fictional composite - priority"])
        writer.writerow([" Riley Harbor", "03-15-2026", "USD 89.5", "312.555.0199 x42", " Fictional composite - follow up"])
        writer.writerow([" Riley Harbor", "03-15-2026", "USD 89.5", "312.555.0199 x42", " Fictional composite - follow up"])
        writer.writerow(["", "", "", "", ""])
        writer.writerow(["Morgan Juniper", "not supplied", "$75.00", "call office", "Fictional composite - keep unchanged safely"])
    with config_path.open("x", encoding="utf-8") as handle:
        json.dump(CONFIG, handle, indent=2)
        handle.write("\n")
    print(f"Created synthetic demo CSV: {csv_path}")
    print(f"Created demo config: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
