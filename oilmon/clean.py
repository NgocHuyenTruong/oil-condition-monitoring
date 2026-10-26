"""Clean and validate raw lab records.

Every change or problem is written to an issue log (output/powerbi/dq_issues.csv)
so nothing is silently altered and the lab can be asked about anything odd.

Order of checks:
 1. Duplicates     exact repeats dropped; amended re-issues replace the earlier version
 2. Unit IDs       "HT04" / "ht-04 " -> "HT-04"; missing or unknown units quarantined
 3. Sample dates   mixed formats, day/month swaps, wrong year, missing dates
 4. Units          equivalent labels (mg/kg = ppm) and conversions (water ppm -> %)
 5. Values         missing results / oil hours flagged; implausible values removed
 6. Sequence       sample dates must increase with the unit's engine hours
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import config

ISSUE_COLUMNS = ["sample_id", "issue", "field", "original", "action", "detail"]


class IssueLog:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, sample_id, issue, action, field="", original="", detail=""):
        self.rows.append({"sample_id": sample_id, "issue": issue, "field": field,
                          "original": "" if original is None else str(original),
                          "action": action, "detail": detail})

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=ISSUE_COLUMNS)


@dataclass
class Cleaned:
    samples: pd.DataFrame     # one row per sample, one column per test
    results: pd.DataFrame     # long: one row per sample x test (best shape for Power BI)
    issues: pd.DataFrame
    quarantine: pd.DataFrame  # records that could not be used, kept for follow-up with the lab


# ---------------------------------------------------------------- 1. duplicates
def deduplicate(records: list[dict], log: IssueLog, quarantine: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for rec in records:
        key = json.dumps(rec, sort_keys=True)
        if key in seen:
            log.add(rec.get("sample_id"), "exact_duplicate", "dropped",
                    detail="identical record received more than once")
            continue
        seen.add(key)
        if not rec.get("sample_id"):
            log.add(None, "missing_sample_id", "quarantined")
            quarantine.append({"sample_id": None, "reason": "missing_sample_id", "record": key})
            continue
        unique.append(rec)

    latest: dict[str, dict] = {}
    for rec in unique:
        sid, current = rec["sample_id"], latest.get(rec["sample_id"])
        if current is None:
            latest[sid] = rec
            continue
        older, newer = (rec, current) if rec["updated_at"] <= current["updated_at"] else (current, rec)
        latest[sid] = newer
        log.add(sid, "superseded_version", "dropped", "updated_at", older["updated_at"],
                f"replaced by {newer.get('status')} version updated {newer['updated_at']}")
    return list(latest.values())


def flatten(records: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Nested lab JSON -> (one row per sample, one row per result)."""
    header, results = [], []
    for rec in records:
        meter, oil = rec.get("meter") or {}, rec.get("oil") or {}
        header.append({
            "sample_id": rec["sample_id"], "lab_status": rec.get("status"),
            "unit_id_raw": rec.get("customer_unit_id"), "component": rec.get("component"),
            "sampled_at_raw": rec.get("sampled_at"), "received_at": rec.get("received_at"),
            "reported_at": rec.get("reported_at"), "updated_at": rec.get("updated_at"),
            "unit_hours": meter.get("unit_hours"), "oil_hours": meter.get("oil_hours"),
            "oil_changed": oil.get("changed_at_sample"),
        })
        for r in rec.get("results") or []:
            results.append({"sample_id": rec["sample_id"], "test": r.get("test"),
                            "value": r.get("value"), "unit_raw": r.get("unit")})
    header = pd.DataFrame(header)
    for col in ("unit_hours", "oil_hours"):
        header[col] = pd.to_numeric(header[col], errors="coerce")
    results = pd.DataFrame(results, columns=["sample_id", "test", "value", "unit_raw"])
    results["value"] = pd.to_numeric(results["value"], errors="coerce").astype(float)
    return header, results


# ---------------------------------------------------------------- 2. unit IDs
def normalise_unit_id(raw) -> str | None:
    if raw is None or pd.isna(raw) or not str(raw).strip():
        return None
    compact = re.sub(r"[\s_\-]", "", str(raw).upper())
    m = re.fullmatch(r"([A-Z]{2})(\d{1,3})", compact)
    return f"{m[1]}-{int(m[2]):02d}" if m else str(raw).strip().upper()


def standardise_unit_ids(header: pd.DataFrame, known_units: set[str], log: IssueLog,
                         quarantine: list[dict]) -> pd.DataFrame:
    header = header.copy()
    header["unit_id"] = header["unit_id_raw"].map(normalise_unit_id)
    keep = []
    for sid, raw, unit, sampled, hours in zip(header["sample_id"], header["unit_id_raw"], header["unit_id"],
                                              header["sampled_at_raw"], header["unit_hours"]):
        if unit is None or pd.isna(unit):
            reason = "missing_unit_id"
        elif unit not in known_units:
            reason = "unknown_unit_id"
        else:
            if unit != raw:
                log.add(sid, "unit_id_format", "corrected", "customer_unit_id", raw, f"-> {unit}")
            keep.append(True)
            continue
        log.add(sid, reason, "quarantined", "customer_unit_id", raw,
                "cannot link the result to a machine; ask the lab to confirm")
        quarantine.append({"sample_id": sid, "reason": reason,
                           "record": f"unit id {'missing' if pd.isna(raw) else repr(raw)}; "
                                     f"sampled {sampled}; meter {hours:g} h"})
        keep.append(False)
    return header[keep].reset_index(drop=True)


# ---------------------------------------------------------------- 3. sample dates
def parse_date(text) -> tuple[date | None, str | None]:
    """Return (date, how_it_was_read). Labs mix ISO and Australian DD/MM/YYYY."""
    if text is None or pd.isna(text) or not str(text).strip():
        return None, None
    text = str(text).strip()
    for fmt, how in (("%Y-%m-%d", "iso"), ("%d/%m/%Y", "dd/mm/yyyy"), ("%Y-%d-%m", "swapped")):
        try:
            return datetime.strptime(text, fmt).date(), how
        except ValueError:
            continue
    return None, "unparseable"


def _plausible(sampled: date, received: date) -> bool:
    return sampled <= received and (received - sampled).days <= config.MAX_TRANSIT_DAYS


def _repair_date(sampled: date, received: date) -> tuple[date | None, str]:
    """Try the common typos, keep the first that gives a believable transit time."""
    candidates = []
    if sampled.day <= 12:
        candidates.append((sampled.replace(day=sampled.month, month=sampled.day), "day_month_swapped"))
    if sampled.year != received.year:
        try:
            candidates.append((sampled.replace(year=received.year), "wrong_year"))
        except ValueError:  # 29 Feb
            pass
    for fixed, issue in candidates:
        if _plausible(fixed, received):
            return fixed, issue
    return None, "invalid_sample_date"


def fix_sample_dates(header: pd.DataFrame, log: IssueLog) -> pd.DataFrame:
    header = header.copy()
    received = [d.date() if not pd.isna(d) else None
                for d in pd.to_datetime(header["received_at"], errors="coerce")]
    parsed = [parse_date(x) for x in header["sampled_at_raw"]]
    transit = [(r - d).days for (d, _), r in zip(parsed, received) if d and r and _plausible(d, r)]
    typical = timedelta(days=int(np.median(transit)) if transit else 3)

    dates, quality = [], []
    for sid, raw, (sampled, how), recv in zip(header["sample_id"], header["sampled_at_raw"], parsed, received):
        q = "as reported"
        if how == "dd/mm/yyyy":
            log.add(sid, "date_format_standardised", "corrected", "sampled_at", raw, "DD/MM/YYYY read day-first")
        elif how == "swapped":
            log.add(sid, "day_month_swapped", "corrected", "sampled_at", raw, f"-> {sampled}")
            q = "corrected"
        if sampled is None:
            if recv is None:
                log.add(sid, "missing_sample_date", "flagged", "sampled_at", raw, "no received date to estimate from")
            else:
                sampled, q = recv - typical, "imputed"
                log.add(sid, "missing_sample_date", "imputed", "sampled_at", raw,
                        f"received date minus typical transit ({typical.days} d) = {sampled}")
        elif recv is not None and not _plausible(sampled, recv):
            fixed, issue = _repair_date(sampled, recv)
            if fixed:
                log.add(sid, issue, "corrected", "sampled_at", raw, f"{sampled} -> {fixed} (received {recv})")
                sampled, q = fixed, "corrected"
            else:
                log.add(sid, issue, "imputed", "sampled_at", raw,
                        f"{sampled} not possible for received {recv}; using {recv - typical}")
                sampled, q = recv - typical, "imputed"
        dates.append(sampled)
        quality.append(q)

    header["sample_date"] = pd.to_datetime(pd.Series(dates, dtype="object"))
    header["sample_date_quality"] = quality
    for col in ("received_at", "reported_at"):
        header[col] = pd.to_datetime(header[col], errors="coerce")
    header["updated_at"] = pd.to_datetime(header["updated_at"], errors="coerce")
    return header


# ---------------------------------------------------------------- 4. units
def standardise_units(results: pd.DataFrame, log: IssueLog) -> pd.DataFrame:
    results = results.copy()
    expected = results["test"].map(lambda t: config.TESTS[t][1] if t in config.TESTS else None)
    unit = results["unit_raw"].map(lambda u: config.UNIT_ALIASES.get(u, u))

    relabelled = results["unit_raw"].notna() & (unit != results["unit_raw"])
    for sid, g in results[relabelled].groupby("sample_id"):
        log.add(sid, "unit_label_standardised", "corrected", ",".join(g["test"]),
                ",".join(sorted(set(g["unit_raw"]))), "equivalent label, value unchanged")

    factor = pd.Series([config.UNIT_CONVERSIONS.get((t, u), np.nan) for t, u in zip(results["test"], unit)],
                       index=results.index)
    for i in results.index[factor.notna()]:
        new = results.at[i, "value"] * factor[i]
        log.add(results.at[i, "sample_id"], "unit_conversion", "corrected", results.at[i, "test"],
                f"{results.at[i, 'value']} {unit[i]}", f"-> {new:g} {expected[i]}")
    converted = factor.notna()
    results.loc[converted, "value"] = results.loc[converted, "value"] * factor[converted]
    unit[converted] = expected[converted]

    unknown = expected.isna()
    for i in results.index[unknown]:
        log.add(results.at[i, "sample_id"], "unknown_test", "dropped", results.at[i, "test"])
    wrong_unit = ~unknown & (unit != expected)
    for i in results.index[wrong_unit]:
        log.add(results.at[i, "sample_id"], "unexpected_unit", "removed value", results.at[i, "test"],
                f"{results.at[i, 'value']} {results.at[i, 'unit_raw']}", f"expected {expected[i]}")
    results.loc[wrong_unit, "value"] = np.nan
    results["unit"] = expected
    return results[~unknown].drop(columns="unit_raw")


# ---------------------------------------------------------------- 5. values
def check_values(results: pd.DataFrame, header: pd.DataFrame, log: IssueLog) -> pd.DataFrame:
    results = results.copy()
    present = set(map(tuple, results.dropna(subset=["value"])[["sample_id", "test"]].to_numpy()))
    for sid in header["sample_id"]:
        for test in config.TESTS:
            if (sid, test) not in present:
                log.add(sid, "missing_result", "flagged", test, detail="test not reported by lab")
    for sid in header.loc[header["oil_hours"].isna(), "sample_id"]:
        log.add(sid, "missing_oil_hours", "flagged", "oil_hours",
                detail="limits still applied; sample left out of hours-normalised trending")

    lo = results["test"].map(lambda t: config.PLAUSIBLE_RANGE.get(t, config.PLAUSIBLE_METAL_PPM)[0]).astype(float)
    hi = results["test"].map(lambda t: config.PLAUSIBLE_RANGE.get(t, config.PLAUSIBLE_METAL_PPM)[1]).astype(float)
    bad = (results["value"] < lo) | (results["value"] > hi)
    for i in results.index[bad]:
        log.add(results.at[i, "sample_id"], "implausible_value", "removed value", results.at[i, "test"],
                results.at[i, "value"], f"outside {lo[i]:g}-{hi[i]:g} {results.at[i, 'unit']}; confirm with lab")
    results.loc[bad, "value"] = np.nan
    return results


# ---------------------------------------------------------------- 6. sequence
def check_date_sequence(samples: pd.DataFrame, log: IssueLog) -> None:
    ordered = samples.dropna(subset=["unit_hours", "sample_date"]).sort_values(["unit_id", "unit_hours"])
    for _, g in ordered.groupby("unit_id"):
        later_seen = g["sample_date"].cummax().shift()
        for sid, d in g.loc[g["sample_date"] < later_seen, ["sample_id", "sample_date"]].itertuples(index=False):
            log.add(sid, "date_out_of_sequence", "flagged", "sampled_at", d.date(),
                    "earlier than a sample taken at fewer engine hours")


def clean(records: list[dict], register: pd.DataFrame) -> Cleaned:
    log, quarantine = IssueLog(), []
    records = deduplicate(records, log, quarantine)
    header, results = flatten(records)
    header = standardise_unit_ids(header, set(register["unit_id"]), log, quarantine)
    header = fix_sample_dates(header, log)
    results = results[results["sample_id"].isin(header["sample_id"])]
    results = standardise_units(results, log)
    results = check_values(results, header, log)

    wide = (results.pivot_table(index="sample_id", columns="test", values="value", aggfunc="first")
            .reindex(columns=list(config.TESTS)))
    samples = (header.merge(wide, left_on="sample_id", right_index=True, how="left")
               .merge(register[["unit_id", "unit_type", "model"]], on="unit_id", how="left")
               .sort_values(["sample_date", "unit_id"]).reset_index(drop=True))
    check_date_sequence(samples, log)
    long = (results.dropna(subset=["value"])
            .merge(samples[["sample_id", "unit_id", "sample_date"]], on="sample_id")
            .sort_values(["sample_date", "unit_id", "test"]).reset_index(drop=True))
    return Cleaned(samples, long, log.frame(),
                   pd.DataFrame(quarantine, columns=["sample_id", "reason", "record"]))
