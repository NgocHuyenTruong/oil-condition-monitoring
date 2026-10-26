"""Score the pipeline against the ground truth written by generate_data.py.

Only possible because the data is synthetic: we know which faults were seeded
and which data errors were injected, so detection can be measured, not assumed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .analyse import Analysed
from .clean import Cleaned

# Injected error type -> issue types in the log that count as catching it.
EXPECTED_ISSUE = {
    "exact_duplicate": {"exact_duplicate"},
    "amended_reissue": {"superseded_version"},
    "missing_unit_id": {"missing_unit_id"},
    "unit_id_format": {"unit_id_format"},
    "missing_sample_date": {"missing_sample_date"},
    "day_month_swapped": {"day_month_swapped"},
    "wrong_year": {"wrong_year", "invalid_sample_date"},
    "dd_mm_format": {"date_format_standardised"},
    "water_in_ppm": {"unit_conversion"},
    "unit_label_variant": {"unit_label_standardised"},
    "missing_result": {"missing_result"},
    "missing_oil_hours": {"missing_oil_hours"},
    "decimal_slip": {"implausible_value"},
}
# First test in a fault's signature -> word the diagnosis should contain.
DIAGNOSIS_KEYWORD = {"Pb": "bearing", "Cu": "bearing", "Si": "dirt", "Na": "coolant", "K": "coolant",
                     "FUEL": "fuel", "V100": "fuel", "SOOT": "soot", "Fe": "wear"}
GRACE_DAYS_AFTER_REPAIR = 30


def markdown_table(df: pd.DataFrame) -> str:
    cells = [[("" if pd.isna(v) else str(v)) for v in row] for row in df.itertuples(index=False)]
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in cells])


def _first(df: pd.DataFrame, mask) -> pd.Timestamp | None:
    hit = df.loc[mask, "sample_date"]
    return hit.min() if not hit.empty else None


def score_faults(a: Analysed, truth: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for f in truth[truth["sample_id"].isna()].itertuples():
        onset = pd.Timestamp(f.onset)
        sig = set(f.signature.split(","))
        res = a.results[(a.results["unit_id"] == f.unit_id) & (a.results["sample_date"] >= onset)
                        & a.results["test"].isin(sig)]
        first_anomaly = _first(res, res["anomaly"].notna())
        first_limit = _first(res, res["limit_status"].isin(["Caution", "Critical"]))
        first_alert = min(d for d in (first_anomaly, first_limit) if d is not None) if (first_anomaly or first_limit) else None
        diag = ""
        if first_alert is not None:
            s = a.samples[(a.samples["unit_id"] == f.unit_id) & (a.samples["sample_date"] == first_alert)]
            diag = s["diagnosis"].iloc[0]
        rows.append({
            "unit": f.unit_id, "seeded fault": f.fault, "onset": onset.date(),
            "first alert": first_alert.date() if first_alert is not None else "missed",
            "days to alert": (first_alert - onset).days if first_alert is not None else None,
            "first anomaly flag": first_anomaly.date() if first_anomaly is not None else "-",
            "first limit breach": first_limit.date() if first_limit is not None else "-",
            "anomaly lead over limits (days)": (first_limit - first_anomaly).days
            if first_anomaly is not None and first_limit is not None else None,
            "diagnosis correct": DIAGNOSIS_KEYWORD.get(f.signature.split(",")[0], "?") in diag.lower(),
        })

    # Anything flagged outside a fault window is a false alarm.
    flagged = a.samples[a.samples["status"] != "Normal"].copy()
    expected = pd.Series(False, index=flagged.index)
    for f in truth.itertuples():
        unit = flagged["unit_id"] == f.unit_id
        if isinstance(f.sample_id, str):
            expected |= flagged["sample_id"] == f.sample_id
            continue
        end = pd.Timestamp(f.resolved) + pd.Timedelta(days=GRACE_DAYS_AFTER_REPAIR) \
            if isinstance(f.resolved, str) else pd.Timestamp.max
        expected |= unit & flagged["sample_date"].between(pd.Timestamp(f.onset), end)
    return pd.DataFrame(rows), flagged[~expected]


def score_dq(c: Cleaned, truth: pd.DataFrame) -> pd.DataFrame:
    logged = set(map(tuple, c.issues[["sample_id", "issue"]].to_numpy()))
    rows = []
    for err, g in truth.groupby("error_type", sort=False):
        caught = sum(any((sid, i) in logged for i in EXPECTED_ISSUE[err]) for sid in g["sample_id"])
        row = {"injected error": err, "injected": len(g), "caught": caught, "value restored": ""}
        if err in ("day_month_swapped", "wrong_year", "dd_mm_format"):
            fixed = c.samples.set_index("sample_id")["sample_date"].reindex(g["sample_id"]).dt.strftime("%Y-%m-%d")
            row["value restored"] = f"{(fixed.to_numpy() == g['true_value'].to_numpy()).sum()}/{len(g)} exact"
        elif err == "amended_reissue":
            fe = c.results[c.results["test"] == "Fe"].set_index("sample_id")["value"].reindex(g["sample_id"])
            row["value restored"] = f"{(fe.to_numpy() == g['true_value'].astype(float).to_numpy()).sum()}/{len(g)} exact"
        elif err == "water_in_ppm":
            w = c.results[c.results["test"] == "WATER"].set_index("sample_id")["value"].reindex(g["sample_id"])
            ok = np.isclose(w.to_numpy(), g["true_value"].astype(float).to_numpy())
            row["value restored"] = f"{ok.sum()}/{len(g)} exact"
        rows.append(row)
    return pd.DataFrame(rows)


def evaluate(c: Cleaned, a: Analysed) -> str:
    truth_faults = pd.read_csv(config.DATA_DIR / "truth_faults.csv")
    truth_dq = pd.read_csv(config.DATA_DIR / "truth_dq.csv")
    faults, false_alarms = score_faults(a, truth_faults)
    dq = score_dq(c, truth_dq)
    spike = truth_faults.dropna(subset=["sample_id"]).iloc[0]
    spike_row = a.samples[a.samples["sample_id"] == spike["sample_id"]]
    spike_ok = bool(spike_row["isolated_spike"].iloc[0]) if not spike_row.empty else False
    n_samples = len(a.samples)

    md = [
        "# Pipeline evaluation (synthetic ground truth)",
        "",
        f"Samples analysed: {n_samples}. Flagged samples outside any seeded fault: "
        f"{len(false_alarms)} ({len(false_alarms) / n_samples:.1%}).",
        "",
        "## Seeded faults",
        "",
        markdown_table(faults),
        "",
        f"Contaminated-sample spike ({spike['unit_id']}, {spike['sample_id']}) recognised as an isolated "
        f"spike rather than a fault: **{'yes' if spike_ok else 'no'}**.",
        "",
        "## Injected data-quality errors",
        "",
        markdown_table(dq),
        "",
        f"Total caught: {dq['caught'].sum()}/{dq['injected'].sum()}.",
        "",
        "## Flags outside seeded faults (false alarms)",
        "",
        markdown_table(false_alarms[["sample_id", "unit_id", "sample_date", "status", "attention_tests", "diagnosis"]]
                       .assign(sample_date=lambda d: d["sample_date"].dt.date))
        if len(false_alarms) else "None.",
    ]
    text = "\n".join(md) + "\n"
    (config.OUTPUT_DIR / "evaluation.md").write_text(text, encoding="utf-8")
    return text
