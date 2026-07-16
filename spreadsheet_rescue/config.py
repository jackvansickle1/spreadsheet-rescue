"""Configuration loading and validation for Spreadsheet Rescue."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "csv": {
        "delimiter": "auto",
        "encoding": "utf-8-sig",
        "output_encoding": "utf-8-sig",
    },
    "headers": {
        "case": "snake",
        "unicode_normalization": "NFKC",
    },
    "whitespace": {
        "trim": True,
        "collapse_internal": False,
        "unicode_normalization": None,
    },
    "rows": {
        "remove_blank": True,
        "remove_exact_duplicates": True,
    },
    "behavior": {
        "missing_columns": "error",
    },
    "security": {
        "formula_policy": "warn",
    },
    "dates": [],
    "currencies": [],
    "phones": [],
}


class ConfigError(ValueError):
    """Raised when a configuration cannot be used safely."""


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if key not in base:
            raise ConfigError(f"Unknown top-level configuration key: {key}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"Configuration key '{key}' must be an object")
            unknown = set(value) - set(base[key])
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ConfigError(f"Unknown key(s) under '{key}': {names}")
            merged[key].update(copy.deepcopy(value))
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _require_bool(section: dict[str, Any], key: str, section_name: str) -> None:
    if not isinstance(section[key], bool):
        raise ConfigError(f"'{section_name}.{key}' must be true or false")


def _validate_transform_list(config: dict[str, Any], name: str) -> None:
    items = config[name]
    if not isinstance(items, list):
        raise ConfigError(f"'{name}' must be an array")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"'{name}' item {index} must be an object")
        if not isinstance(item.get("column"), str) or not item["column"].strip():
            raise ConfigError(f"'{name}' item {index} needs a non-empty 'column'")


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    csv_config = config["csv"]
    delimiter = csv_config["delimiter"]
    if delimiter != "auto" and (
        not isinstance(delimiter, str)
        or len(delimiter) != 1
        or delimiter in {"\r", "\n", "\x00"}
    ):
        raise ConfigError("'csv.delimiter' must be 'auto' or one character")
    for key in ("encoding", "output_encoding"):
        if not isinstance(csv_config[key], str) or not csv_config[key]:
            raise ConfigError(f"'csv.{key}' must be a non-empty encoding name")

    headers = config["headers"]
    if not isinstance(headers["case"], str) or headers["case"] not in {
        "preserve",
        "lower",
        "upper",
        "snake",
    }:
        raise ConfigError("'headers.case' must be preserve, lower, upper, or snake")
    header_unicode = headers["unicode_normalization"]
    if header_unicode is not None and (
        not isinstance(header_unicode, str) or header_unicode not in {"NFC", "NFKC"}
    ):
        raise ConfigError("'headers.unicode_normalization' must be null, NFC, or NFKC")

    whitespace = config["whitespace"]
    _require_bool(whitespace, "trim", "whitespace")
    _require_bool(whitespace, "collapse_internal", "whitespace")
    cell_unicode = whitespace["unicode_normalization"]
    if cell_unicode is not None and (
        not isinstance(cell_unicode, str) or cell_unicode not in {"NFC", "NFKC"}
    ):
        raise ConfigError("'whitespace.unicode_normalization' must be null, NFC, or NFKC")

    rows = config["rows"]
    _require_bool(rows, "remove_blank", "rows")
    _require_bool(rows, "remove_exact_duplicates", "rows")
    missing_columns = config["behavior"]["missing_columns"]
    if not isinstance(missing_columns, str) or missing_columns not in {"error", "warn"}:
        raise ConfigError("'behavior.missing_columns' must be error or warn")
    formula_policy = config["security"]["formula_policy"]
    if not isinstance(formula_policy, str) or formula_policy not in {"warn", "neutralize"}:
        raise ConfigError("'security.formula_policy' must be warn or neutralize")

    for name in ("dates", "currencies", "phones"):
        _validate_transform_list(config, name)

    for index, item in enumerate(config["dates"], start=1):
        formats = item.get("input_formats")
        if not isinstance(formats, list) or not formats or not all(
            isinstance(value, str) and value for value in formats
        ):
            raise ConfigError(f"'dates' item {index} needs a non-empty 'input_formats' array")
        if not isinstance(item.get("output_format"), str) or not item["output_format"]:
            raise ConfigError(f"'dates' item {index} needs a non-empty 'output_format'")
        unknown = set(item) - {"column", "input_formats", "output_format"}
        if unknown:
            raise ConfigError(f"Unknown key(s) in 'dates' item {index}: {', '.join(sorted(unknown))}")

    for index, item in enumerate(config["currencies"], start=1):
        unknown = set(item) - {
            "column",
            "symbols",
            "decimal_separator",
            "thousands_separator",
            "decimal_places",
            "allow_parentheses",
        }
        if unknown:
            raise ConfigError(
                f"Unknown key(s) in 'currencies' item {index}: {', '.join(sorted(unknown))}"
            )
        symbols = item.get("symbols", ["$"])
        if not isinstance(symbols, list) or not all(isinstance(value, str) and value for value in symbols):
            raise ConfigError(f"'currencies' item {index} has an invalid 'symbols' array")
        decimal_separator = item.get("decimal_separator", ".")
        thousands_separator = item.get("thousands_separator", ",")
        if not isinstance(decimal_separator, str) or len(decimal_separator) != 1:
            raise ConfigError(f"'currencies' item {index} needs a one-character decimal separator")
        if not isinstance(thousands_separator, str) or len(thousands_separator) > 1:
            raise ConfigError(f"'currencies' item {index} has an invalid thousands separator")
        if decimal_separator == thousands_separator:
            raise ConfigError(f"'currencies' item {index} separators must differ")
        places = item.get("decimal_places", 2)
        if not isinstance(places, int) or isinstance(places, bool) or not 0 <= places <= 8:
            raise ConfigError(f"'currencies' item {index} decimal_places must be from 0 to 8")
        if not isinstance(item.get("allow_parentheses", True), bool):
            raise ConfigError(f"'currencies' item {index} allow_parentheses must be true or false")

    for index, item in enumerate(config["phones"], start=1):
        unknown = set(item) - {
            "column",
            "default_country_code",
            "national_lengths",
            "output_format",
            "allow_extensions",
        }
        if unknown:
            raise ConfigError(f"Unknown key(s) in 'phones' item {index}: {', '.join(sorted(unknown))}")
        country_code = str(item.get("default_country_code", "1"))
        if not country_code.isdigit() or not 1 <= len(country_code) <= 3:
            raise ConfigError(f"'phones' item {index} has an invalid default_country_code")
        lengths = item.get("national_lengths", [10])
        if not isinstance(lengths, list) or not lengths or not all(
            isinstance(value, int) and not isinstance(value, bool) and 4 <= value <= 14 for value in lengths
        ):
            raise ConfigError(f"'phones' item {index} has invalid national_lengths")
        output_format = item.get("output_format", "international")
        if not isinstance(output_format, str) or output_format not in {
            "international",
            "e164",
            "digits",
            "rfc3966",
        }:
            raise ConfigError(
                f"'phones' item {index} output_format must be international, e164, digits, or rfc3966"
            )
        if not isinstance(item.get("allow_extensions", True), bool):
            raise ConfigError(f"'phones' item {index} allow_extensions must be true or false")

    return config


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load, merge, and validate a JSON configuration file."""

    overlay: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except OSError as exc:
            raise ConfigError(f"Could not read configuration: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Invalid JSON configuration at line {exc.lineno}, column {exc.colno}"
            ) from exc
        if not isinstance(loaded, dict):
            raise ConfigError("Configuration root must be a JSON object")
        overlay = loaded
    return validate_config(_deep_merge(DEFAULT_CONFIG, overlay))
