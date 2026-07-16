"""Core CSV cleanup engine.

The audit deliberately records aggregate counts and schema-level information,
never cell values. This makes reports useful without turning them into a second
copy of potentially sensitive customer data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from .config import ConfigError, load_config


class RescueError(RuntimeError):
    """A safe, user-facing rescue failure."""


@dataclass
class FieldStats:
    column: str
    kind: str
    nonblank_values: int = 0
    changed: int = 0
    already_normalized: int = 0
    invalid: int = 0
    ambiguous: int = 0


@dataclass
class RescueResult:
    dry_run: bool
    input_path: Path
    cleaned_path: Path | None
    json_report_path: Path | None
    markdown_report_path: Path | None
    audit: dict[str, Any]
    cleaned_rows: list[list[str]] = field(repr=False)
    cleaned_headers: list[str] = field(repr=False)


@dataclass
class _ResolvedTransform:
    index: int
    stats: FieldStats
    normalize: Callable[[str], tuple[str, str]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _header_base(value: str, config: dict[str, Any], position: int) -> str:
    normalization = config["unicode_normalization"]
    if normalization:
        value = unicodedata.normalize(normalization, value)
    value = re.sub(r"\s+", " ", value.strip())
    case = config["case"]
    if case == "lower":
        value = value.lower()
    elif case == "upper":
        value = value.upper()
    elif case == "snake":
        value = re.sub(r"[^\w]+", "_", value, flags=re.UNICODE).strip("_").lower()
    return value or f"column_{position}"


def _normalize_headers(
    original_headers: list[str], config: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]], dict[str, list[int]]]:
    headers: list[str] = []
    mappings: list[dict[str, Any]] = []
    aliases: dict[str, list[int]] = {}
    used: set[str] = set()

    for index, original in enumerate(original_headers):
        base = _header_base(original, config, index + 1)
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate.casefold())
        headers.append(candidate)
        mappings.append(
            {
                "position": index + 1,
                "original": original,
                "cleaned": candidate,
                "changed": original != candidate,
            }
        )
        for alias in {original.casefold(), base.casefold(), candidate.casefold()}:
            aliases.setdefault(alias, []).append(index)
    return headers, mappings, aliases


def _clean_whitespace(value: str, config: dict[str, Any]) -> str:
    result = value
    normalization = config["unicode_normalization"]
    if normalization:
        result = unicodedata.normalize(normalization, result)
    if config["trim"]:
        result = result.strip()
    if config["collapse_internal"]:
        result = re.sub(r"\s+", " ", result)
    return result


def _date_normalizer(spec: dict[str, Any]) -> Callable[[str], tuple[str, str]]:
    formats = spec["input_formats"]
    output_format = spec["output_format"]

    def normalize(value: str) -> tuple[str, str]:
        parsed: list[datetime] = []
        for date_format in formats:
            try:
                parsed.append(datetime.strptime(value, date_format))
            except ValueError:
                continue
        unique = {item for item in parsed}
        if not unique:
            return value, "invalid"
        if len(unique) > 1:
            return value, "ambiguous"
        try:
            output = next(iter(unique)).strftime(output_format)
        except (ValueError, UnicodeError):
            return value, "invalid"
        return output, "changed" if output != value else "already_normalized"

    return normalize


def _strip_edge_tokens(value: str, tokens: list[str]) -> tuple[str, bool]:
    result = value
    removed = False
    for _ in range(2):
        matched = False
        for token in sorted(tokens, key=len, reverse=True):
            if result.startswith(token):
                result = result[len(token) :].strip()
                removed = matched = True
                break
            if result.endswith(token):
                result = result[: -len(token)].strip()
                removed = matched = True
                break
        if not matched:
            break
    return result, removed


def _currency_normalizer(spec: dict[str, Any]) -> Callable[[str], tuple[str, str]]:
    symbols = spec.get("symbols", ["$"])
    decimal_separator = spec.get("decimal_separator", ".")
    thousands_separator = spec.get("thousands_separator", ",")
    decimal_places = spec.get("decimal_places", 2)
    allow_parentheses = spec.get("allow_parentheses", True)

    decimal_re = re.escape(decimal_separator)
    if thousands_separator:
        thousands_re = re.escape(thousands_separator)
        integer_pattern = rf"(?:\d+|\d{{1,3}}(?:{thousands_re}\d{{3}})+)"
    else:
        integer_pattern = r"\d+"
    number_pattern = re.compile(rf"^{integer_pattern}(?:{decimal_re}\d+)?$")
    quantum = Decimal(1).scaleb(-decimal_places)

    def normalize(value: str) -> tuple[str, str]:
        work = value.strip()
        negative = False
        sign_seen = False
        if work.startswith("(") or work.endswith(")"):
            if not (allow_parentheses and work.startswith("(") and work.endswith(")")):
                return value, "invalid"
            negative = True
            sign_seen = True
            work = work[1:-1].strip()
        if work.startswith(("+", "-")):
            if sign_seen:
                return value, "invalid"
            negative = work[0] == "-"
            sign_seen = True
            work = work[1:].strip()
        work, _ = _strip_edge_tokens(work, symbols)
        if work.startswith(("+", "-")):
            if sign_seen:
                return value, "invalid"
            negative = work[0] == "-"
            sign_seen = True
            work = work[1:].strip()
        if not work or not number_pattern.fullmatch(work):
            return value, "invalid"
        canonical = work
        if thousands_separator:
            canonical = canonical.replace(thousands_separator, "")
        canonical = canonical.replace(decimal_separator, ".")
        try:
            amount = Decimal(canonical)
            if negative:
                amount = -amount
            amount = amount.quantize(quantum, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return value, "invalid"
        output = f"{amount:.{decimal_places}f}"
        return output, "changed" if output != value else "already_normalized"

    return normalize


_EXTENSION_RE = re.compile(r"(?i)\s*(?:ext\.?|x|#)\s*(\d{1,10})\s*$")
_PHONE_BODY_RE = re.compile(r"^\+?[\d\s().-]+$")
_FORMULA_PREFIXES = {"=", "+", "-", "@", "\t", "\r", "\n"}


def _phone_normalizer(spec: dict[str, Any]) -> Callable[[str], tuple[str, str]]:
    country_code = str(spec.get("default_country_code", "1"))
    national_lengths = set(spec.get("national_lengths", [10]))
    output_format = spec.get("output_format", "international")
    allow_extensions = spec.get("allow_extensions", True)

    def normalize(value: str) -> tuple[str, str]:
        work = value.strip()
        extension = ""
        match = _EXTENSION_RE.search(work)
        if match:
            if not allow_extensions:
                return value, "invalid"
            extension = match.group(1)
            work = work[: match.start()].strip()
        if not _PHONE_BODY_RE.fullmatch(work):
            return value, "invalid"
        if work.count("(") != work.count(")") or work.count("(") > 1:
            return value, "invalid"
        if "(" in work and work.index("(") > work.index(")"):
            return value, "invalid"
        had_plus = work.startswith("+")
        digits = re.sub(r"\D", "", work)
        if had_plus:
            if not 8 <= len(digits) <= 15:
                return value, "invalid"
            international = digits
        elif len(digits) in national_lengths:
            international = country_code + digits
        elif any(
            digits.startswith(country_code) and len(digits) == len(country_code) + length
            for length in national_lengths
        ):
            international = digits
        else:
            return value, "invalid"
        if not 8 <= len(international) <= 15:
            return value, "invalid"
        if extension and output_format in {"e164", "digits"}:
            return value, "invalid"
        if output_format == "rfc3966":
            output = f"tel:+{international}"
            if extension:
                output += f";ext={extension}"
        elif output_format == "digits":
            output = international
        else:
            output = f"+{international}"
            if extension:
                output += f" x{extension}"
        return output, "changed" if output != value else "already_normalized"

    return normalize


def _is_formula_like(value: str) -> bool:
    """Return whether a cell may be interpreted as a formula by a spreadsheet app."""

    candidate = value.lstrip(" ")
    return bool(candidate) and candidate[0] in _FORMULA_PREFIXES


def _resolve_column(
    requested: str,
    headers: list[str],
    aliases: dict[str, list[int]],
    header_config: dict[str, Any],
) -> int | None:
    exact = [index for index, header in enumerate(headers) if header.casefold() == requested.casefold()]
    if len(exact) == 1:
        return exact[0]
    normalized = _header_base(requested, header_config, 1).casefold()
    matches = sorted(set(aliases.get(requested.casefold(), []) + aliases.get(normalized, [])))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RescueError(
            f"Configured column '{requested}' is ambiguous after header normalization; "
            "use the final suffixed header name"
        )
    return None


def _build_transforms(
    config: dict[str, Any],
    headers: list[str],
    aliases: dict[str, list[int]],
) -> tuple[list[_ResolvedTransform], list[str]]:
    transforms: list[_ResolvedTransform] = []
    warnings: list[str] = []
    claimed: dict[int, str] = {}
    factories = {
        "dates": ("date", _date_normalizer),
        "currencies": ("currency", _currency_normalizer),
        "phones": ("phone", _phone_normalizer),
    }
    for section, (kind, factory) in factories.items():
        for spec in config[section]:
            index = _resolve_column(spec["column"], headers, aliases, config["headers"])
            if index is None:
                message = f"Configured {kind} column '{spec['column']}' was not found"
                if config["behavior"]["missing_columns"] == "error":
                    raise RescueError(message)
                warnings.append(message)
                continue
            if index in claimed:
                raise RescueError(
                    f"Column '{headers[index]}' is configured as both {claimed[index]} and {kind}"
                )
            claimed[index] = kind
            transforms.append(
                _ResolvedTransform(
                    index=index,
                    stats=FieldStats(column=headers[index], kind=kind),
                    normalize=factory(spec),
                )
            )
    return transforms, warnings


def _detect_delimiter(sample: str, configured: str) -> str:
    if configured != "auto":
        return configured
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_csv(
    path: Path, config: dict[str, Any]
) -> tuple[list[str], list[list[str]], str, str, int]:
    encoding = config["csv"]["encoding"]
    try:
        raw_input = path.read_bytes()
        text_input = raw_input.decode(encoding)
        sample = text_input[: 32 * 1024]
        delimiter = _detect_delimiter(sample, config["csv"]["delimiter"])
        rows = list(
            csv.reader(io.StringIO(text_input, newline=""), delimiter=delimiter, strict=True)
        )
    except LookupError as exc:
        raise RescueError(f"Unknown input encoding '{encoding}'") from exc
    except UnicodeError as exc:
        raise RescueError(
            f"Input could not be decoded as {encoding}; choose the correct encoding in config"
        ) from exc
    except (OSError, csv.Error) as exc:
        raise RescueError(f"Could not read CSV: {exc}") from exc
    if not rows:
        raise RescueError("Input CSV is empty and has no header row")
    input_hash = hashlib.sha256(raw_input).hexdigest()
    return rows[0], rows[1:], delimiter, input_hash, len(raw_input)


def _safe_prefix(value: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise RescueError("Output prefix must be a simple file-name component")
    if any(character in value for character in '<>:"/\\|?*'):
        raise RescueError("Output prefix contains a character Windows file names do not allow")
    return value


def _markdown_escape(value: Any) -> str:
    """Render untrusted labels without activating Markdown or remote images.

    Markdown parsers resolve numeric entities only after parsing constructs, so
    entity-encoding punctuation preserves the visible label while preventing a
    header such as ``![tracking](https://...)`` from becoming active content.
    """

    flattened = str(value).replace("\r", " ").replace("\n", " ")
    return "".join(
        character if character.isalnum() or character == " " else f"&#{ord(character)};"
        for character in flattened
    )


def _render_markdown(audit: dict[str, Any]) -> str:
    rows = audit["rows"]
    changes = audit["changes"]
    lines = [
        "# Spreadsheet Rescue Change Report",
        "",
        f"Generated: `{audit['generated_at']}`  ",
        f"Run mode: **{audit['mode']}**  ",
        f"Input file: {_markdown_escape(audit['input']['file_name'])}  ",
        f"Input SHA-256: `{audit['input']['sha256']}`  ",
        f"Output file: {_markdown_escape(audit['output']['file_name'])}  ",
        f"Output SHA-256: `{audit['output']['sha256']}`",
        "",
        "## Outcome",
        "",
        "| Measure | Count |",
        "|---|---:|",
        f"| Input data rows | {rows['input']} |",
        f"| Output data rows | {rows['output']} |",
        f"| Exact duplicates removed | {rows['exact_duplicates_removed']} |",
        f"| Blank rows removed | {rows['blank_removed']} |",
        f"| Rows padded to match the schema | {rows['padded']} |",
        f"| Whitespace-normalized cells | {changes['whitespace_cells']} |",
        f"| Renamed headers | {changes['headers_renamed']} |",
        f"| Formula-like cells detected | {changes['formula_like_cells']} |",
        f"| Formula-like cells neutralized | {changes['formula_cells_neutralized']} |",
        "",
        "## Field normalization",
        "",
    ]
    if audit["field_normalization"]:
        lines.extend(
            [
                "| Column | Type | Nonblank | Changed | Already normalized | Invalid | Ambiguous |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for item in audit["field_normalization"]:
            lines.append(
                "| {column} | {kind} | {nonblank_values} | {changed} | "
                "{already_normalized} | {invalid} | {ambiguous} |".format(
                    **{key: _markdown_escape(value) for key, value in item.items()}
                )
            )
    else:
        lines.append("No date, currency, or phone fields were configured.")
    lines.extend(["", "## Header map", "", "| Position | Original | Cleaned | Changed |", "|---:|---|---|---|"])
    for item in audit["header_map"]:
        lines.append(
            f"| {item['position']} | {_markdown_escape(item['original'])} | "
            f"{_markdown_escape(item['cleaned'])} | {'yes' if item['changed'] else 'no'} |"
        )
    lines.extend(["", "## Warnings", ""])
    if audit["warnings"]:
        lines.extend(f"- {_markdown_escape(item)}" for item in audit["warnings"])
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "## Privacy note",
            "",
            "This report contains aggregate counts, file fingerprints, and header names only. "
            "No cell values are copied into the audit reports.",
            "",
        ]
    )
    return "\n".join(lines)


def rescue_csv(
    input_path: str | Path,
    *,
    config_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    output_prefix: str | None = None,
    dry_run: bool = False,
) -> RescueResult:
    """Clean a CSV and produce privacy-conscious audit reports.

    Existing files are never overwritten. In dry-run mode, no files or
    directories are created.
    """

    source = Path(input_path).expanduser()
    if not source.exists():
        raise RescueError(f"Input CSV does not exist: {source}")
    if not source.is_file():
        raise RescueError(f"Input path is not a file: {source}")
    source = source.resolve()
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise RescueError(str(exc)) from exc

    (
        original_headers,
        source_rows,
        input_delimiter,
        input_sha256,
        input_bytes,
    ) = _read_csv(source, config)
    input_row_count = len(source_rows)
    widest = max([len(original_headers), *(len(row) for row in source_rows)], default=len(original_headers))
    extra_columns_added = max(0, widest - len(original_headers))
    if extra_columns_added:
        original_headers = original_headers + [
            f"extra_column_{index}"
            for index in range(len(original_headers) + 1, widest + 1)
        ]

    headers, header_map, aliases = _normalize_headers(original_headers, config["headers"])
    transforms, warnings = _build_transforms(config, headers, aliases)
    if extra_columns_added:
        warnings.append(
            f"Added {extra_columns_added} generated header(s) because data rows were wider than the header row"
        )

    whitespace_changes = 0
    padded_rows = 0
    blank_removed = 0
    duplicate_removed = 0
    formula_like_cells = 0
    formula_cells_neutralized = 0
    output_rows: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    for source_row in source_rows:
        row = list(source_row)
        if len(row) < widest:
            row.extend([""] * (widest - len(row)))
            padded_rows += 1
        cleaned: list[str] = []
        for value in row:
            normalized = _clean_whitespace(value, config["whitespace"])
            if normalized != value:
                whitespace_changes += 1
            cleaned.append(normalized)
        for transform in transforms:
            value = cleaned[transform.index]
            if value == "":
                continue
            transform.stats.nonblank_values += 1
            normalized, status = transform.normalize(value)
            setattr(transform.stats, status, getattr(transform.stats, status) + 1)
            cleaned[transform.index] = normalized
        row_formula_like_cells = 0
        for index, value in enumerate(cleaned):
            if not _is_formula_like(value):
                continue
            row_formula_like_cells += 1
            if config["security"]["formula_policy"] == "neutralize":
                cleaned[index] = "'" + value
        if config["rows"]["remove_blank"] and not any(cleaned):
            blank_removed += 1
            continue
        key = tuple(cleaned)
        if config["rows"]["remove_exact_duplicates"] and key in seen:
            duplicate_removed += 1
            continue
        seen.add(key)
        formula_like_cells += row_formula_like_cells
        if config["security"]["formula_policy"] == "neutralize":
            formula_cells_neutralized += row_formula_like_cells
        output_rows.append(cleaned)

    for transform in transforms:
        if transform.stats.invalid:
            warnings.append(
                f"{transform.stats.invalid} nonblank value(s) in '{transform.stats.column}' "
                f"were left unchanged because they were not safely recognized as {transform.stats.kind}"
            )
        if transform.stats.ambiguous:
            warnings.append(
                f"{transform.stats.ambiguous} value(s) in '{transform.stats.column}' were left unchanged "
                "because more than one configured date format matched with different meanings"
            )
    if formula_like_cells:
        if config["security"]["formula_policy"] == "neutralize":
            warnings.append(
                f"Neutralized {formula_cells_neutralized} formula-like cell(s) with a leading apostrophe "
                "to reduce spreadsheet formula-injection risk"
            )
        else:
            warnings.append(
                f"Detected {formula_like_cells} formula-like cell(s); values were preserved, so review "
                "them before opening the CSV in Excel or another spreadsheet application"
            )

    prefix = _safe_prefix(output_prefix or source.stem)
    destination = Path(output_dir).expanduser() if output_dir is not None else source.parent / f"{prefix}_rescue"
    destination = destination.resolve()
    cleaned_path = destination / f"{prefix}_cleaned.csv"
    json_path = destination / f"{prefix}_changes.json"
    markdown_path = destination / f"{prefix}_changes.md"
    targets = (cleaned_path, json_path, markdown_path)
    if any(path.resolve() == source for path in targets):
        raise RescueError("Refusing to overwrite the input CSV; choose another output prefix or directory")
    if not dry_run:
        existing = [path for path in targets if path.exists()]
        if existing:
            raise RescueError(
                "Refusing to overwrite existing output file(s): " + ", ".join(path.name for path in existing)
            )

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    audit: dict[str, Any] = {
        "tool": {"name": "Spreadsheet Rescue", "version": "1.0.0"},
        "generated_at": generated_at,
        "mode": "dry-run" if dry_run else "write",
        "privacy": {"raw_cell_values_logged": False},
        "input": {
            "file_name": source.name,
            "sha256": input_sha256,
            "encoding": config["csv"]["encoding"],
            "delimiter": input_delimiter,
            "bytes": input_bytes,
        },
        "output": {
            "file_name": cleaned_path.name,
            "sha256": None,
            "encoding": config["csv"]["output_encoding"],
            "delimiter": input_delimiter,
        },
        "rows": {
            "input": input_row_count,
            "output": len(output_rows),
            "blank_removed": blank_removed,
            "exact_duplicates_removed": duplicate_removed,
            "padded": padded_rows,
        },
        "columns": {
            "input_header_count": widest - extra_columns_added,
            "output_count": len(headers),
            "generated_for_wide_rows": extra_columns_added,
        },
        "changes": {
            "headers_renamed": sum(item["changed"] for item in header_map),
            "whitespace_cells": whitespace_changes,
            "typed_values_changed": sum(item.stats.changed for item in transforms),
            "formula_like_cells": formula_like_cells,
            "formula_cells_neutralized": formula_cells_neutralized,
        },
        "field_normalization": [asdict(item.stats) for item in transforms],
        "header_map": header_map,
        "warnings": warnings,
    }

    if dry_run:
        return RescueResult(
            dry_run=True,
            input_path=source,
            cleaned_path=None,
            json_report_path=None,
            markdown_report_path=None,
            audit=audit,
            cleaned_rows=output_rows,
            cleaned_headers=headers,
        )

    created: list[Path] = []
    try:
        destination.mkdir(parents=True, exist_ok=True)
        with cleaned_path.open(
            "x", encoding=config["csv"]["output_encoding"], newline=""
        ) as handle:
            created.append(cleaned_path)
            writer = csv.writer(handle, delimiter=input_delimiter, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(output_rows)
        audit["output"]["sha256"] = _sha256(cleaned_path)
        with json_path.open("x", encoding="utf-8", newline="\n") as handle:
            created.append(json_path)
            json.dump(audit, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        with markdown_path.open("x", encoding="utf-8", newline="\n") as handle:
            created.append(markdown_path)
            handle.write(_render_markdown(audit))
    except (OSError, LookupError, UnicodeError, csv.Error) as exc:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise RescueError(f"Could not write rescue outputs safely: {exc}") from exc

    return RescueResult(
        dry_run=False,
        input_path=source,
        cleaned_path=cleaned_path,
        json_report_path=json_path,
        markdown_report_path=markdown_path,
        audit=audit,
        cleaned_rows=output_rows,
        cleaned_headers=headers,
    )
