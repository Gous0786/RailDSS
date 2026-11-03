# Provenance card: KTV–PSA dataset (raw)

| Field (spec FR-DATA-03) | Value |
|---|---|
| Dataset | Kottavalasa Jn (KTV) – Palasa (PSA), East Coast Railway, Waltair division (Howrah–Chennai trunk) |
| Source | CRIS Control Office Application (COA) master-table export, WTT-style passenger schedule, and the Waltair division goods-train movement log for 1–27 Aug 2025. The same extract was distributed for Smart India Hackathon 2025 problem SIH25022 |
| Usage | Free to use; confirmed by the project owner. Committed to this repository on purpose |
| Real / synthetic | **Real** (as supplied). No values are changed in this folder |
| ETL / generator version | n/a: raw input. All cleaning and corrections happen in the ETL (issues #6–#12), never in these files (spec C-13) |
| Seed | n/a |
| Calibration targets | n/a (this dataset *is* the calibration source; see spec Appendix B) |
| Integrity | `MANIFEST.sha256` (SHA-256 per file). Verified in CI by `tools/manifest.py --check` |
| Line endings | Files are stored byte-for-byte with CRLF line endings; `.gitattributes` marks `data/**` as `-text`, so Git never converts them |
| Assessment | RailDSS research report 06, "Assessment of the KTV–PSA Dataset" (data dictionary, weekday order, Palasa lines); spec v1.3 §6.6 maps it to the canonical schema |

## Files

The row counts below are **raw** data rows (CSV records minus the header), before cleaning. Several tables contain blank rows, repeated header rows or duplicates, which the ETL removes (report 06 §4).

| File | Bytes | Raw rows | Contents |
|---|---:|---:|---|
| `RouteSttnInfo/KTV-PSA-Table 1.csv` | 3,454 | 22 | Ordered route KTV→PSA: station, block section, COA block section, distance, cumulative km, signal type, tracks |
| `RouteSttnInfo/PSA-KTV-Table 1.csv` | 3,460 | 22 | Ordered route PSA→KTV |
| `KTV-PSA-Infra/STATION-Table 1.csv` | 5,677 | 22 | Station master: class, junction/halt, interlocking standard, signalling, block instrument, crossing facility, crew change, lat/lon, chainage |
| `KTV-PSA-Infra/BLOCKSCTN-Table 1.csv` | 17,881 | 131 | Block sections (63 unique after cleaning): distance, lines, AB/AT, max speed, direction, COA flag |
| `KTV-PSA-Infra/BLOCKSCTNLINE-Table 1.csv` | 14,549 | 203 | Lines per block section (DN/MID/UP), length, category, signal type, speed |
| `KTV-PSA-Infra/STTNLINE-Table 1.csv` | 8,390 | 103 | Station lines: main/loop/siding, length, CSR, direction, line speed, platform flag. PSA rows have number and name columns swapped (fixed in the ETL, issue #8) |
| `KTV-PSA-Infra/Platform-Table 1.csv` | 5,042 | 70 | Platforms: number, length, type, side, facilities |
| `KTV-PSA-Infra/Connections-Table 1.csv` | 20,459 | 392 | Station line ↔ block-section line receive/send edges |
| `KTV-PSA-Infra/Stationposition-Table 1.csv` | 2,807 | 57 | Schematic layout positions |
| `KTV-PSA-Infra/WAT_GOODS_TRAIN_AUG25_01082025_27082025.csv` | 19,431,094 | 95,219 | Actual freight movements, whole Waltair division, 1–27 Aug 2025 |
| `KTV_PSA_Passenger_Schedule/TRAIN-KTV-PSA-Table 1.csv` | 30,234 | 250 | Passenger train versions: number, name, type, days (Monday-first), coaches, colour, validity |
| `KTV_PSA_Passenger_Schedule/SCHEDULE-KTV-PSA-Table 1.csv` | 878,692 | 4,260 | Train × station WTT times (seconds since day 1), allowances, platforms per weekday |
| `KTV_PSA_Passenger_Schedule/PSR (KTV-PSA)-Table 1.csv` | 980 | 5 | Permanent speed restrictions, KTV→PSA |
| `KTV_PSA_Passenger_Schedule/PSR (PSA-KTV)-Table 1.csv` | 700 | 3 | Permanent speed restrictions, PSA→KTV |

SHA-256 digests: see `MANIFEST.sha256`.
