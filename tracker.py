"""
Turn a GE / Takkion tracker attachment into CSV text the app's
`extract-tracker.ts` can read.

The GE "Commissioning POD" workbook is a grid of feeder blocks: a row of
turbine numbers under an "F 35 A" label, milestone names (IIP, MCC, ...) down
that column, dates across. This flattens that to one row per turbine:

    WTG,Feeder,IIP,MCC,Gen Align,PCAT,FCAT,CCT,TRT,TCC Submitted,TCC Accepted

If the sheet isn't that shape (already one row per turbine, or a Takkion
progress tracker), it's dumped to CSV as-is and the JS parser's header-row
path handles it.
"""
from __future__ import annotations

import csv
import datetime
import io
import re

import openpyxl

MCOLS = ["IIP", "MCC", "Gen Align", "PCAT", "FCAT", "CCT", "TRT", "TCC Submitted", "TCC Accepted"]
_FEEDER = re.compile(r"^F\d{2}[AB]$", re.I)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def _milestone(name: str):
    n = re.sub(r"\s+", " ", name.strip()).lower()
    for m in MCOLS:
        if n == m.lower():
            return m
    return None


def _grid(ws) -> list[list[str]]:
    return [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]


def _flatten_feeder_blocks(grid: list[list[str]]) -> str | None:
    out: dict[int, tuple[str, dict]] = {}
    for r, row in enumerate(grid):
        for col, raw in enumerate(row):
            if not _FEEDER.match(re.sub(r"\s+", "", raw)):
                continue
            feeder = re.sub(r"\s+", "", raw).upper()
            id_cols = []
            for k in range(col + 1, len(row)):
                v = row[k].strip()
                if _FEEDER.match(re.sub(r"\s+", "", v)):
                    break
                if re.fullmatch(r"\d{3}", v):
                    id_cols.append((k, int(v)))
            if not id_cols:
                continue
            for _, tid in id_cols:
                out.setdefault(tid, (feeder, {}))
            for r2 in range(r + 1, min(r + 15, len(grid))):
                name = grid[r2][col] if col < len(grid[r2]) else ""
                if _FEEDER.match(re.sub(r"\s+", "", name)):
                    break
                ms = _milestone(name)
                if not ms:
                    continue
                for k, tid in id_cols:
                    val = grid[r2][k] if k < len(grid[r2]) else ""
                    if re.match(r"\d{4}-\d{2}-\d{2}", val):
                        out[tid][1][ms] = val
    if not out:
        return None
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["WTG", "Feeder"] + MCOLS)
    for tid in sorted(out):
        feeder, dates = out[tid]
        w.writerow([tid, feeder] + [dates.get(m, "") for m in MCOLS])
    return buf.getvalue()


def _raw_csv(grid: list[list[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerows(grid)
    return buf.getvalue()


def xlsx_to_csv(data: bytes) -> str:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    grid = _grid(wb.active)
    return _flatten_feeder_blocks(grid) or _raw_csv(grid)


def attachment_to_text(name: str, data: bytes) -> str:
    lname = (name or "").lower()
    if lname.endswith((".xlsx", ".xlsm")):
        return xlsx_to_csv(data)
    if lname.endswith((".csv", ".tsv", ".txt")):
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported attachment type: {name!r} (need .xlsx, .csv, or .tsv)")
