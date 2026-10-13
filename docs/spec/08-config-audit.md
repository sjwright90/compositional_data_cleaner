## 7. Configuration files (schemas only; contents supplied separately)

| File | Columns / structure |
|---|---|
| `known_analytes.csv` | `name, key, aliases (;-separated), analyte_class, symbol, plot_label, filename_key, transform (log/none), allow_negative (true/false), molar_mass (optional, g/mol), charge (optional, signed int)` |
| `known_units.csv` | `unit, unit_class, applies_to (water/physical/any), column_suffix` — must include `S.U.` in its own class |
| `conversions.csv` | `from_unit, to_unit, factor, offset, analyte (optional; blank = generic)` (converted = value × factor + offset; analyte-specific rows take precedence) |
| `flag_map.json` | optional pre-fill: `{code: action}`, action ∈ `Ignore` / `DL` / `Bad` |
| `date_rules.csv` | `order, source_pattern (optional glob on file name), regex, format` |
| `header_rules.csv` | `order, regex` (analyte/fraction/unit extraction from wide headers) |
| `tokens.json` | no-result tokens, metadata and column-role keyword lists, sample-type alias lists, header-cleanup defaults, numeric locale defaults, default name template |
| `qa_thresholds.json` | `nd_warn_pct`, `dominant_warn_pct`, `ion_balance_tol`, `dt_tol`, `tds_ec_range`, `high_dl_percentile`, `min_obs_per_cell` default |

- **Location:** Shipped defaults live in `config/defaults/`.
- **Overrides:** Project files in `<project>/config/` are layered on top of the defaults. Custom analytes, units and conversions created in-app are written here, tagged `source: user`.
- **Reloading:** A reload button re-reads the files. Changes are logged with before/after hashes and mark dependent stages stale (via the config snapshot hash in the fingerprint).
- **Seed vocabulary:** the client may supply a master analyte list as any delimited text file (.dat/.txt/.csv). Map it onto the `known_analytes.csv` schema with these defaults:
  - missing symbol and plot label → the name;
  - `transform = log`, `allow_negative = false`;
  - Eh and ORP: `allow_negative = true`, `transform = none`;
  - pH: `transform = none`.

  Fill `molar_mass` and `charge` for major ions where known.

---

## 8. Audit & reproducibility

**Recipe vs. log.** The recipe is *what to do* (replayable). The decision log is *what happened and why* (append-only).

**Recipe.**

- A pydantic model tree with one section per stage, plus `schema_version`.
- **Config snapshot:** the resolved configuration entries actually used (analytes, units, conversion rows, date rules, header rules, tokens, thresholds), plus the SHA-256 of each full config file.
- **Migrations:** `core/migrations.py` holds one function per version step (`v1 → v2`, …). Loading an older recipe runs the chain and logs it. Every migration has a test with a stored old-version recipe.

**Decision log.**

- **Source:** The orchestrator writes one log row per report item returned by core functions. Tests assert that every core stage function returns a report.
- **Fields:**
  - `decision_id`, `timestamp`, `stage`
  - `type` (user / auto-applied / default-accepted / uploaded / undo / migration)
  - `action`, `target` (stable ID), `parameters`
  - `rows_affected` / `items_affected`
  - `evidence_file`, `warnings`
  - optional `rationale`
- **Granularity:** Automatic behaviours log summary rows with counts, not one row per cell.
- **Reversals:** Reversing a decision appends a new row that references the original.

**Checkpoints.**

- **When:** On every stage commit.
- **Where:** `<project>/checkpoints/<timestamp>_<stage>/`, containing stage outputs and evidence files (affected rows) as CSV.
- **Manifest:** file, rows, cols, SHA-256, stage fingerprint, recipe hash, config snapshot hash, app version, versions of Python, pandas, numpy and scikit-learn.
- **Immutability:** Checkpoints are immutable; recommitting creates a new one.

**Replay.**

- Loading a recipe on a new file with the same layout applies all decisions by stable ID.
- **Config check:** if current config differs from the recipe's snapshot, the differences are listed. The user chooses "use recipe snapshot" (default; reproducible) or "use current config" (logged).
- A **needs-attention list** shows anything new or unmatched, typically:
  - new ingest groups (unmapped analytes or units)
  - new raw location strings
  - new sample type values
  - new qualifier codes
  - unparsed or ambiguous dates
  - new zeros converted to NaN
  - new or changed duplicate groups
  - new drop-plan conflicts
  - decisions whose ID no longer matches anything (orphaned)

  These go through the normal gates.

---

