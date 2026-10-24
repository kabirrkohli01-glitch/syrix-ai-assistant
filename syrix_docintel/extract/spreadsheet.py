"""Spreadsheet extraction — XLSX / XLS / CSV / TSV / ODS.

Reads every sheet into a TableObject (a row-capped preview + the true row
count), computes per-column numeric statistics + simple anomaly flags, and —
for XLSX — pulls a best-effort map of formula cells so the brain can answer
"explain the formula in D12". Each sheet also becomes a retrieval chunk (a
markdown preview + a stats summary) so questions like "highest value / average
/ growth / outliers" have grounded text to reason over.

Data-quality pass (2026-07-16): broken formula results (#REF! / #DIV/0! /...),
missing cells per column, duplicate rows, and a first-half vs second-half
trend direction per numeric column are detected deterministically and written
into both ``doc.statistics`` and a per-sheet "Data quality" retrieval chunk,
so "find mistakes / missing data / trends / anomalies" grounds on real
findings. Cross-sheet references are mapped from the formula list. Macros are
NEVER executed — a workbook carrying a VBA project is only flagged.
"""
from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Dict, List

import syrix_obs

from .. import types as T
from ..chunk import finalize
from ..safety import Deadline, ProcessingError, load_limits
from ..types import DocumentObject, TableObject

_PREVIEW_ROWS = 200      # rows stored in the TableObject (full stats still computed)
_MAX_FORMULAS = 300

# Excel error literals that survive into displayed values.
_FORMULA_ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!", "#NUM!", "#SPILL!")

# `'Sheet Name'!A1` or `Sheet2!A1` inside a formula body.
_SHEET_REF_RE = re.compile(r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_. ]{0,30}?))!\$?[A-Z]{1,3}\$?\d")


def _read_sheets(blob: bytes, subtype: str) -> Dict[str, Any]:
    import pandas as pd  # type: ignore
    bio = io.BytesIO(blob)
    if subtype in ("csv", "tsv"):
        sep = "\t" if subtype == "tsv" else ","
        try:
            df = pd.read_csv(bio, sep=sep)
            # A one-column result on a multi-char file usually means the wrong
            # delimiter (`;` CSVs are common) — retry with sniffing.
            if len(df.columns) <= 1:
                bio.seek(0)
                df = pd.read_csv(bio, sep=None, engine="python")
        except Exception:
            bio.seek(0)
            df = pd.read_csv(bio, sep=None, engine="python")
        return {"Sheet1": df}
    if subtype == "xls":
        return pd.read_excel(bio, sheet_name=None, engine="xlrd")
    if subtype == "ods":
        return pd.read_excel(bio, sheet_name=None, engine="odf")
    # xlsx / xlsm (default) — openpyxl reads values only; macros never run.
    return pd.read_excel(bio, sheet_name=None, engine="openpyxl")


def _column_stats(df) -> Dict[str, Any]:
    import pandas as pd  # type: ignore
    stats: Dict[str, Any] = {}
    for col in df.columns:
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if series.empty:
            continue
        col_stat = {
            "count": int(series.count()),
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": round(float(series.mean()), 4),
            "sum": round(float(series.sum()), 4),
        }
        # Anomaly flag: values beyond mean ± 3σ.
        std = float(series.std()) if series.count() > 1 else 0.0
        if std > 0:
            hi = series.mean() + 3 * std
            lo = series.mean() - 3 * std
            outliers = int(((series > hi) | (series < lo)).sum())
            if outliers:
                col_stat["outliers"] = outliers
        # Trend: first half vs second half of the column in row order — a
        # cheap, deterministic direction signal for time-ordered data.
        if series.count() >= 8:
            half = series.count() // 2
            first = float(series.iloc[:half].mean())
            second = float(series.iloc[half:].mean())
            base = abs(first) if abs(first) > 1e-9 else 1.0
            delta = (second - first) / base
            if delta > 0.10:
                col_stat["trend"] = "rising"
            elif delta < -0.10:
                col_stat["trend"] = "falling"
        stats[str(col)] = col_stat
    return stats


def _quality_findings(name: str, df_raw, df_filled) -> Dict[str, Any]:
    """Deterministic data-quality scan for one sheet (on the un-filled frame)."""
    q: Dict[str, Any] = {}
    with syrix_obs.swallow("docintel.sheet.quality_missing", sheet=name):
        missing = {str(c): int(df_raw[c].isna().sum()) for c in df_raw.columns
                   if int(df_raw[c].isna().sum()) > 0}
        if missing:
            q["missing_cells"] = missing
    with syrix_obs.swallow("docintel.sheet.quality_dup", sheet=name):
        dup = int(df_raw.duplicated().sum())
        if dup:
            q["duplicate_rows"] = dup
    with syrix_obs.swallow("docintel.sheet.quality_errors", sheet=name):
        err_cells: List[str] = []
        arr = df_filled.astype(str).values
        cols = [str(c) for c in df_filled.columns]
        for ri, row in enumerate(arr):
            for ci, val in enumerate(row):
                if val in _FORMULA_ERRORS:
                    err_cells.append(f"{cols[ci]} row {ri + 2}: {val}")   # +2 = header + 1-index
                    if len(err_cells) >= 40:
                        break
            if len(err_cells) >= 40:
                break
        if err_cells:
            q["formula_errors"] = err_cells
    return q


def _quality_text(name: str, q: Dict[str, Any], stats: Dict[str, Any]) -> str:
    """Human-readable findings — becomes a retrieval chunk so questions about
    mistakes / missing data / duplicates / trends ground on real evidence."""
    lines: List[str] = []
    errs = q.get("formula_errors") or []
    if errs:
        lines.append(f"Broken formula results ({len(errs)} cell(s)): " + "; ".join(errs[:10])
                     + ("; …" if len(errs) > 10 else ""))
    dup = q.get("duplicate_rows") or 0
    if dup:
        lines.append(f"{dup} duplicate row(s) found.")
    missing = q.get("missing_cells") or {}
    if missing:
        seg = ", ".join(f"{c} ({n} empty)" for c, n in list(missing.items())[:8])
        lines.append("Columns with missing values: " + seg + ("…" if len(missing) > 8 else ""))
    trends = [(c, s["trend"]) for c, s in stats.items() if s.get("trend")]
    if trends:
        lines.append("Trends (first half vs second half): "
                     + ", ".join(f"{c} {t}" for c, t in trends[:8]))
    outliers = [(c, s["outliers"]) for c, s in stats.items() if s.get("outliers")]
    if outliers:
        lines.append("Unusual values (beyond 3 standard deviations): "
                     + ", ".join(f"{c} ({n})" for c, n in outliers[:8]))
    if not lines:
        return ""
    return f"Data quality findings for sheet '{name}':\n" + "\n".join("- " + l for l in lines)


def _xlsx_error_cells(blob: bytes, cap: int = 40) -> Dict[str, List[str]]:
    """Per-sheet list of cells whose CACHED VALUE is an Excel error (#REF! …).

    pandas converts error cells to NaN, so this pass reads the raw workbook
    (values only, ``data_only=True`` — formulas are never evaluated) to keep
    broken-formula evidence visible to the quality report.
    """
    out: Dict[str, List[str]] = {}
    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception:
        return out
    found = 0
    with syrix_obs.swallow("docintel.xlsx.error_cells"):
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v in _FORMULA_ERRORS:
                        out.setdefault(ws.title, []).append(f"{cell.coordinate}: {v}")
                        found += 1
                        if found >= cap:
                            with syrix_obs.swallow("docintel.xlsx.close"):
                                wb.close()
                            return out
    with syrix_obs.swallow("docintel.xlsx.close"):
        wb.close()
    return out


def _xlsx_formulas(blob: bytes) -> Dict[str, str]:
    """Map 'Sheet!A1' -> formula string for cells that hold a formula."""
    out: Dict[str, str] = {}
    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=False)
    except Exception:
        return out
    with syrix_obs.swallow("docintel.xlsx.formulas"):
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    v = cell.value
                    if isinstance(v, str) and v.startswith("="):
                        out[f"{ws.title}!{cell.coordinate}"] = v[:120]
                        if len(out) >= _MAX_FORMULAS:
                            with syrix_obs.swallow("docintel.xlsx.close"):
                                wb.close()
                            return out
    with syrix_obs.swallow("docintel.xlsx.close"):
        wb.close()
    return out


def _sheet_refs(formulas: Dict[str, str]) -> Dict[str, List[str]]:
    """Map each sheet to the OTHER sheets its formulas reference."""
    refs: Dict[str, set] = {}
    for cell, formula in formulas.items():
        own = cell.split("!", 1)[0]
        for m in _SHEET_REF_RE.finditer(formula):
            target = (m.group(1) or m.group(2) or "").strip()
            if target and target != own:
                refs.setdefault(own, set()).add(target)
    return {k: sorted(v) for k, v in refs.items()}


def _has_macros(blob: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            return any(n.lower().endswith("vbaproject.bin") for n in zf.namelist())
    except Exception:
        return False


def _sheet_summary_text(name: str, df, stats: Dict[str, Any]) -> str:
    lines = [f"Sheet '{name}': {len(df)} rows × {len(df.columns)} columns.",
             "Columns: " + ", ".join(str(c) for c in df.columns)]
    for col, s in stats.items():
        seg = f"- {col}: min={s['min']:g}, max={s['max']:g}, mean={s['mean']:g}, sum={s['sum']:g}"
        if s.get("outliers"):
            seg += f", outliers={s['outliers']}"
        if s.get("trend"):
            seg += f", trend={s['trend']}"
        lines.append(seg)
    return "\n".join(lines)


def extract(blob: bytes, doc: DocumentObject, deadline: Deadline) -> None:
    try:
        sheets = _read_sheets(blob, doc.subtype)
    except Exception as exc:
        raise ProcessingError(f"Could not read spreadsheet ({exc}).")

    limits = load_limits()
    doc.sheets = len(sheets)
    doc.metadata["sheet_names"] = list(sheets.keys())
    doc.extraction_method = f"pandas:{doc.subtype}"
    all_stats: Dict[str, Any] = {}
    all_quality: Dict[str, Any] = {}

    err_map: Dict[str, List[str]] = {}
    if doc.subtype in ("xlsx", "xlsm"):
        if _has_macros(blob):
            doc.metadata["has_macros"] = True
            doc.add_note("Workbook contains VBA macros — they were NOT executed; only cell values were read.")
        err_map = _xlsx_error_cells(blob)

    from ..chunk import add_page
    idx = 0
    for name, df in sheets.items():
        idx += 1
        try:
            deadline.check("spreadsheet parsing")
        except ProcessingError:
            doc.status = T.STATUS_PARTIAL
            doc.add_note(f"Time budget reached; stopped after sheet '{name}'.")
            break
        try:
            df_raw = df
            df = df.fillna("")
            cols = [str(c) for c in df.columns]
            n_rows = int(len(df))
            preview = df.head(_PREVIEW_ROWS).astype(str).values.tolist()
            stats = _column_stats(df)
            quality = _quality_findings(str(name), df_raw, df)
            sheet_errs = err_map.get(str(name)) or []
            if sheet_errs:
                quality["formula_errors"] = sheet_errs + list(quality.get("formula_errors") or [])
        except Exception as exc:
            doc.add_note(f"Sheet '{name}' analysis failed ({type(exc).__name__}).")
            continue
        all_stats[str(name)] = stats
        if quality:
            all_quality[str(name)] = quality
        doc.tables.append(TableObject(
            sheet=str(name), title=str(name), columns=cols, rows=preview,
            n_rows=n_rows, n_cols=len(cols),
        ))
        # Retrieval chunk: human-readable summary + a small markdown preview.
        summary = _sheet_summary_text(str(name), df, stats)
        preview_md = doc.tables[-1].preview_md(max_rows=15)
        add_page(doc, idx, str(name), summary + "\n\n" + preview_md, kind="table")
        qtext = _quality_text(str(name), quality, stats)
        if qtext:
            add_page(doc, idx, str(name), qtext, kind="table")

    if doc.subtype in ("xlsx", "xlsm"):
        formulas = _xlsx_formulas(blob)
        if formulas:
            doc.metadata["formulas"] = formulas
            refs = _sheet_refs(formulas)
            if refs:
                doc.metadata["sheet_refs"] = refs

    doc.statistics = {"sheets": all_stats}
    if all_quality:
        doc.statistics["quality"] = all_quality
    doc.confidence = 1.0
    finalize(doc)
