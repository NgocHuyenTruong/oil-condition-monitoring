"""Limit checks, anomaly detection and a first-pass diagnosis for each sample.

Three layers, from simplest to most sensitive:
 1. Fixed limits     raw value vs caution / critical limits in config/limits.csv
 2. Jump             robust z-score of the hours-normalised value against the unit's
                     own recent history (median / MAD, so one bad sample does not
                     distort the baseline). Flagged samples are kept out of the baseline.
 3. Trend            steady rise across the last few samples (linear fit, high R^2),
                     for slow faults such as bearing wear that never "jump".

A unit's own history is the reference because engines differ: 25 ppm Fe can be
normal for one unit and a warning sign for another.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config
from .clean import Cleaned

STATUS_ORDER = ["Normal", "Watch", "Caution", "Critical"]
DIRECTION = {**{t: "high" for t in config.ACCUMULATIVE}, "WATER": "high", "FUEL": "high", "V100": "both"}

# (name, recommendation) for the diagnosis rules in diagnose().
FINDINGS = {
    "coolant": ("Possible coolant ingress (Na/K)", "Check coolant level; pressure-test cooling system and oil cooler"),
    "dirt": ("Possible dirt ingestion (Si)", "Inspect air intake, ducting, filters and breather"),
    "bearing": ("Possible bearing wear (Pb/Cu/Sn)", "Halve the sampling interval; plan a bearing inspection"),
    "fuel": ("Possible fuel dilution", "Check injectors and fuel system; confirm viscosity on resample"),
    "soot": ("High soot", "Check combustion, air filter restriction and injectors"),
    "wear": ("Elevated wear metals", "Resample and watch the Fe/Cr/Al trend"),
    "water": ("Water contamination", "Check for condensation or seal ingress"),
    "tbn": ("Low TBN - oil near end of life", "Review oil drain interval"),
    "spike": ("Isolated spike - possible sample contamination", "Resample to confirm before any maintenance action"),
}


@dataclass
class Analysed:
    samples: pd.DataFrame  # one row per sample with status, diagnosis, recommendation
    results: pd.DataFrame  # long results with limit status and anomaly flags
    alerts: pd.DataFrame   # one row per jump / trend detection


def load_limits(path=config.CONFIG_DIR / "limits.csv") -> pd.DataFrame:
    return pd.read_csv(path).set_index("test")


def limit_status(test: str, value: float, limits: pd.DataFrame) -> str | None:
    if pd.isna(value) or test not in limits.index:
        return None
    row = limits.loc[test]
    for level in ("critical", "caution"):
        high, low = row[f"{level}_high"], row[f"{level}_low"]
        if (not pd.isna(high) and value >= high) or (not pd.isna(low) and value <= low):
            return level.title()
    return "Normal"


def add_normalised(samples: pd.DataFrame) -> pd.DataFrame:
    """Scale accumulative tests to a 250 oil-hour basis so samples are comparable."""
    samples = samples.copy()
    lo, hi = config.NORMALISE_VALID_HOURS
    hours = samples["oil_hours"].where(samples["oil_hours"].between(lo, hi))
    for test in config.ACCUMULATIVE:
        samples[f"{test}_n"] = samples[test] * config.NORMALISE_TO_HOURS / hours
    return samples


def _trend(points: list[tuple[pd.Timestamp, float]]) -> tuple[float, float, float]:
    days = np.array([(d - points[0][0]).days for d, _ in points], dtype=float)
    y = np.array([v for _, v in points], dtype=float)
    if np.ptp(days) == 0 or np.ptp(y) == 0:
        return 0.0, 0.0, 0.0
    slope = np.polyfit(days, y, 1)[0]
    r2 = np.corrcoef(days, y)[0, 1] ** 2
    return slope, r2, slope * np.ptp(days)


def detect_anomalies(samples: pd.DataFrame) -> pd.DataFrame:
    cols = ["sample_id", "unit_id", "sample_date", "test", "kind", "value", "baseline", "z", "detail"]
    rows = []
    for unit_id, g in samples.sort_values("sample_date").groupby("unit_id"):
        for test, direction in DIRECTION.items():
            col = f"{test}_n" if test in config.ACCUMULATIVE else test
            floor = config.ANOMALY_SCALE_FLOOR[test]
            # Wear metals vary roughly in proportion to their level; viscosity does not.
            relative = 0.1 if test in config.ACCUMULATIVE else 0.0
            history: list[float] = []  # values judged normal -> the unit's baseline
            points: list[tuple] = []   # every value, for trend fitting
            for sid, day, x in g[["sample_id", "sample_date", col]].dropna().itertuples(index=False):
                points.append((day, x))
                if len(history) >= config.ANOMALY_MIN_HISTORY:
                    ref = np.asarray(history[-config.ANOMALY_WINDOW:])
                    med = float(np.median(ref))
                    scale = max(1.4826 * float(np.median(np.abs(ref - med))), floor, relative * abs(med))
                    z = (x - med) / scale
                    if z >= config.ANOMALY_Z or (direction == "both" and z <= -config.ANOMALY_Z):
                        rows.append([sid, unit_id, day, test, "jump", x, med, round(z, 1),
                                     f"{x:.3g} vs usual {med:.3g} (z={z:.1f})"])
                        continue
                history.append(x)
                if direction == "high" and len(points) >= config.TREND_POINTS:
                    slope, r2, rise = _trend(points[-config.TREND_POINTS:])
                    if slope > 0 and r2 >= config.TREND_MIN_R2 and rise >= config.TREND_MIN_RISE_FLOORS * floor:
                        rows.append([sid, unit_id, day, test, "trend", x, np.nan, np.nan,
                                     f"rising {slope * 30:.2g}/month over last {config.TREND_POINTS} samples (R2={r2:.2f})"])
    return pd.DataFrame(rows, columns=cols)


def diagnose(tests: set[str], low: set[str]) -> list[str]:
    """Map the tests that need attention to likely causes (classic oil analysis signatures)."""
    found = []
    if "K" in tests or {"Na", "WATER"} <= tests:
        found.append("coolant")
    if "Si" in tests:
        found.append("dirt")
    if "Pb" in tests or len(tests & {"Pb", "Cu", "Sn"}) >= 2:
        found.append("bearing")
    if "FUEL" in tests or "V100" in low:
        found.append("fuel")
    if "SOOT" in tests:
        found.append("soot")
    if tests & {"Fe", "Cr", "Al"} and "dirt" not in found:
        found.append("wear")
    if "WATER" in tests and "coolant" not in found:
        found.append("water")
    if "TBN" in tests:
        found.append("tbn")
    return found


def _summarise(results: pd.DataFrame, limits: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sid, g in results.groupby("sample_id"):
        at_limit = g[g["limit_status"].isin(["Caution", "Critical"])]
        flagged = g[g["anomaly"].notna()]
        tests = set(at_limit["test"]) | set(flagged["test"])
        low = {t for t, v in zip(at_limit["test"], at_limit["value"])
               if not pd.isna(limits.loc[t, "caution_low"]) and v <= limits.loc[t, "caution_low"]}
        low |= {t for t, z in zip(flagged["test"], flagged["z"]) if z < 0}
        if not at_limit.empty:
            status = "Critical" if (at_limit["limit_status"] == "Critical").any() else "Caution"
        else:
            status = "Watch" if tests else "Normal"
        rows.append({"sample_id": sid, "status": status,
                     "attention_tests": ", ".join(t for t in config.TESTS if t in tests),
                     "findings": diagnose(tests, low) if tests else []})
    return pd.DataFrame(rows)


def _mark_isolated_spikes(samples: pd.DataFrame) -> pd.DataFrame:
    """A flagged sample with a normal sample either side is more likely a bad sample than a fault."""
    samples = samples.copy()
    samples["isolated_spike"] = False
    for _, g in samples.sort_values("sample_date").groupby("unit_id"):
        normal = (g["status"] == "Normal").to_numpy()
        for k in range(1, len(g) - 1):
            if not normal[k] and normal[k - 1] and normal[k + 1]:
                samples.loc[g.index[k], "isolated_spike"] = True
    return samples


def analyse(cleaned: Cleaned, limits: pd.DataFrame) -> Analysed:
    samples = add_normalised(cleaned.samples)
    results = cleaned.results.copy()
    results["limit_status"] = [limit_status(t, v, limits) for t, v in zip(results["test"], results["value"])]
    norm = samples.melt(id_vars="sample_id", value_vars=[f"{t}_n" for t in config.ACCUMULATIVE],
                        var_name="test", value_name="value_per_250h")
    norm["test"] = norm["test"].str.removesuffix("_n")
    results = results.merge(norm, on=["sample_id", "test"], how="left")

    alerts = detect_anomalies(samples)
    results = results.merge(alerts[["sample_id", "test", "kind", "z", "baseline"]]
                            .rename(columns={"kind": "anomaly", "baseline": "unit_baseline"}),
                            on=["sample_id", "test"], how="left")

    samples = samples.merge(_summarise(results, limits), on="sample_id", how="left")
    samples = _mark_isolated_spikes(samples)
    keys = [["spike"] if spike else f for f, spike in zip(samples["findings"], samples["isolated_spike"])]
    samples["diagnosis"] = ["; ".join(FINDINGS[k][0] for k in ks) for ks in keys]
    samples["recommendation"] = ["; ".join(FINDINGS[k][1] for k in ks) for ks in keys]
    unexplained = (samples["status"] != "Normal") & (samples["diagnosis"] == "")
    samples.loc[unexplained, "diagnosis"] = "Review: " + samples.loc[unexplained, "attention_tests"]
    samples = samples.drop(columns=["findings"] + [f"{t}_n" for t in config.ACCUMULATIVE])
    return Analysed(samples, results, alerts)
