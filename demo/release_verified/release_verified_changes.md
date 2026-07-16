# Spreadsheet Rescue Change Report

Generated: `2026-07-16T21:39:23.054757Z`  
Run mode: **write**  
Input file: demo&#95;dirty&#46;csv  
Input SHA-256: `e88b0fd22bc28f340bd97b33f6a5d6754a19f4755bedfe799ab0daf76d649c13`  
Output file: release&#95;verified&#95;cleaned&#46;csv  
Output SHA-256: `da9d75ecc79de9e4fb2d7129ded76b0ae23b5d5d76bc800f4da3919d551d8d63`

## Outcome

| Measure | Count |
|---|---:|
| Input data rows | 6 |
| Output data rows | 3 |
| Exact duplicates removed | 2 |
| Blank rows removed | 1 |
| Rows padded to match the schema | 0 |
| Whitespace-normalized cells | 6 |
| Renamed headers | 5 |
| Formula-like cells detected | 2 |
| Formula-like cells neutralized | 0 |

## Field normalization

| Column | Type | Nonblank | Changed | Already normalized | Invalid | Ambiguous |
|---|---|---:|---:|---:|---:|---:|
| order&#95;date | date | 5 | 3 | 1 | 1 | 0 |
| amount | currency | 5 | 4 | 1 | 0 | 0 |
| phone | phone | 5 | 3 | 1 | 1 | 0 |

## Header map

| Position | Original | Cleaned | Changed |
|---:|---|---|---|
| 1 |  Sample Customer  | sample&#95;customer | yes |
| 2 | Order Date | order&#95;date | yes |
| 3 |  Amount  | amount | yes |
| 4 | Phone | phone | yes |
| 5 | Notes  | notes | yes |

## Warnings

- 1 nonblank value&#40;s&#41; in &#39;order&#95;date&#39; were left unchanged because they were not safely recognized as date
- 1 nonblank value&#40;s&#41; in &#39;phone&#39; were left unchanged because they were not safely recognized as phone
- Detected 2 formula&#45;like cell&#40;s&#41;&#59; values were preserved&#44; so review them before opening the CSV in Excel or another spreadsheet application

## Privacy note

This report contains aggregate counts, file fingerprints, and header names only. No cell values are copied into the audit reports.
