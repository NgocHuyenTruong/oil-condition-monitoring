"""Generate a synthetic engine oil analysis feed that behaves like a lab API.

* 13 heavy-fleet units sampled every ~250 oil hours, Jan-Sep 2026.
* Four units develop a seeded fault and one sample is contaminated, so the
  anomaly detection can be scored against known answers.
* A share of records get the problems a real lab feed has: duplicates,
  amended re-issues, missing fields, unit mix-ups and bad sample dates.

Writes data/lab_feed.json (served by the mock API), data/asset_register.csv,
and ground truth in data/truth_faults.csv and data/truth_dq.csv.
"""
from __future__ import annotations

import copy
import json
import random
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import config

SEED = 946
START, END = date(2026, 1, 1), date(2026, 9, 30)

FLEET = (
    [(f"HT-{i:02d}", "Haul truck", "CAT 793F", "CAT 3516C") for i in range(1, 9)]
    + [(f"EX-{i:02d}", "Excavator", "Komatsu PC4000", "Komatsu SDA16V160") for i in range(1, 4)]
    + [(f"DZ-{i:02d}", "Dozer", "CAT D11T", "CAT C32") for i in range(1, 3)]
)

# Healthy engine at 250 oil hours: (mean, sd). Accumulative tests scale with oil hours.
BASELINE = {
    "Fe": (14, 3), "Cu": (3, 1), "Pb": (2, 0.6), "Cr": (1.0, 0.4), "Al": (2.2, 0.6),
    "Sn": (1.0, 0.4), "Si": (4.5, 1.0), "Na": (4, 1.2), "K": (2, 0.6), "SOOT": (0.6, 0.12),
    "V100": (14.6, 0.25), "WATER": (0.02, 0.01), "FUEL": (0.6, 0.25), "TBN": (8.4, 0.4),
}
DECIMALS = {"V100": 2, "SOOT": 2, "WATER": 3, "FUEL": 2, "TBN": 1}

FAULTS = [
    {"unit_id": "HT-07", "fault": "Progressive bearing wear", "onset": date(2026, 3, 2),
     "ramp_days": 200, "effect": {"Pb": 28, "Cu": 16, "Sn": 5}, "signature": "Pb,Cu,Sn"},
    {"unit_id": "EX-02", "fault": "Dust ingestion (air intake breach)", "onset": date(2026, 4, 20),
     "resolved": date(2026, 6, 20), "ramp_days": 1,
     "effect": {"Si": 20, "Al": 6, "Fe": 18, "Cr": 3}, "signature": "Si,Al"},
    {"unit_id": "HT-04", "fault": "Coolant leak (oil cooler)", "onset": date(2026, 6, 8),
     "ramp_days": 90, "effect": {"Na": 45, "K": 40, "WATER": 0.06, "V100": 0.8}, "signature": "Na,K"},
    {"unit_id": "DZ-01", "fault": "Fuel dilution (injector)", "onset": date(2026, 7, 14),
     "ramp_days": 40, "effect": {"FUEL": 4.5, "V100": -2.8}, "signature": "FUEL,V100"},
]
# One dirty sample bottle: a single-sample spike that should NOT be treated as a fault.
SPIKE = {"unit_id": "HT-02", "near": date(2026, 5, 15), "multiply": {"Fe": 5.0, "Si": 4.0, "Al": 3.0}}


def _fault_effect(unit_id: str, day: date) -> dict[str, float]:
    effect: dict[str, float] = {}
    for f in FAULTS:
        if f["unit_id"] != unit_id or day < f["onset"] or day >= f.get("resolved", date.max):
            continue
        severity = min(1.0, ((day - f["onset"]).days + 1) / f["ramp_days"])
        for test, size in f["effect"].items():
            effect[test] = effect.get(test, 0.0) + size * severity
    return effect


def _results(rng, unit_id, day, oil_hours, wear_factor) -> dict[str, float]:
    hf = oil_hours / config.NORMALISE_TO_HOURS
    effect = _fault_effect(unit_id, day)
    values = {}
    for test, (mean, sd) in BASELINE.items():
        if test in config.ACCUMULATIVE:
            factor = wear_factor if test in ("Fe", "Cu", "Pb", "Cr", "Al", "Sn") else 1.0
            v = rng.normal(mean * factor, sd) * hf + effect.get(test, 0.0) * hf
        elif test == "TBN":
            v = 10.0 - 1.6 * hf + rng.normal(0, sd)
        elif test == "V100":
            v = rng.normal(mean, sd) + 0.15 * hf + effect.get(test, 0.0)
        else:
            v = rng.normal(mean, sd) + effect.get(test, 0.0)
        values[test] = round(max(v, 0.0), DECIMALS.get(test, 0))
    return values


def _simulate_fleet(rng) -> tuple[list[dict], pd.DataFrame]:
    samples, register = [], []
    for unit_id, unit_type, model, engine in FLEET:
        hours = float(rng.integers(15_000, 60_000))
        register.append({"unit_id": unit_id, "unit_type": unit_type, "model": model,
                         "engine_model": engine, "site": "Pit A" if unit_id < "HT-05" else "Pit B",
                         "hours_at_start_2026": int(hours)})
        oil_hours = float(rng.uniform(0, 240))
        next_sample = 250 + rng.normal(0, 12)
        wear_factor = rng.uniform(0.85, 1.15)
        day = START
        while day <= END:
            run = 0.0 if rng.random() < 0.04 else max(0.0, rng.normal(19, 1.5))
            hours += run
            oil_hours += run
            if oil_hours >= next_sample:
                oil_change = next_sample > 400
                samples.append({
                    "unit_id": unit_id, "sampled": day, "unit_hours": round(hours),
                    "oil_hours": round(oil_hours), "oil_changed": oil_change,
                    "results": _results(rng, unit_id, day, oil_hours, wear_factor),
                })
                oil_hours, next_sample = (0.0, 250 + rng.normal(0, 12)) if oil_change \
                    else (oil_hours, 500 + rng.normal(0, 15))
            day += timedelta(days=1)

    spike = min((s for s in samples if s["unit_id"] == SPIKE["unit_id"]),
                key=lambda s: abs((s["sampled"] - SPIKE["near"]).days))
    for test, k in SPIKE["multiply"].items():
        spike["results"][test] = round(spike["results"][test] * k)
    spike["is_spike"] = True
    return samples, pd.DataFrame(register)


def _to_record(i: int, s: dict, rng) -> dict:
    received = s["sampled"] + timedelta(days=int(rng.integers(2, 6)))
    reported = received + timedelta(days=int(rng.integers(1, 4)))
    updated = datetime.combine(reported, time(9)) + timedelta(minutes=int(rng.integers(0, 480)))
    return {
        "sample_id": f"LAB-26-{i:05d}",
        "status": "final",
        "customer_unit_id": s["unit_id"],
        "component": "ENGINE",
        "sampled_at": s["sampled"].isoformat(),
        "received_at": received.isoformat(),
        "reported_at": reported.isoformat(),
        "updated_at": updated.isoformat(timespec="seconds"),
        "meter": {"unit_hours": s["unit_hours"], "oil_hours": s["oil_hours"]},
        "oil": {"grade": "SAE 15W-40", "changed_at_sample": s["oil_changed"]},
        "results": [{"test": t, "value": v, "unit": config.TESTS[t][1]} for t, v in s["results"].items()],
    }


def _result(rec: dict, test: str) -> dict | None:
    return next((r for r in rec["results"] if r["test"] == test), None)


def _inject_errors(records: list[dict], seed: int) -> tuple[list[dict], list[dict]]:
    """Corrupt a share of records in place; return (records + extra copies, truth rows)."""
    pick = random.Random(seed)
    pool = list(range(len(records)))
    pick.shuffle(pool)
    truth: list[dict] = []

    def take(n, ok=lambda r: True):
        chosen = [i for i in pool if ok(records[i])][:n]
        for i in chosen:
            pool.remove(i)
        return [records[i] for i in chosen]

    def log(rec, error, **extra):
        truth.append({"sample_id": rec["sample_id"], "error_type": error, **extra})

    def swappable(r):
        d = date.fromisoformat(r["sampled_at"])
        return d.day <= 12 and d.day != d.month

    for rec in take(4, swappable):
        d = date.fromisoformat(rec["sampled_at"])
        log(rec, "day_month_swapped", true_value=rec["sampled_at"])
        rec["sampled_at"] = f"{d.year}-{d.day:02d}-{d.month:02d}"
    for rec in take(10):
        d = date.fromisoformat(rec["sampled_at"])
        log(rec, "dd_mm_format", true_value=rec["sampled_at"])
        rec["sampled_at"] = d.strftime("%d/%m/%Y")
    for rec in take(2):
        log(rec, "wrong_year", true_value=rec["sampled_at"])
        rec["sampled_at"] = "2027" + rec["sampled_at"][4:]
    for rec in take(3):
        log(rec, "missing_sample_date", true_value=rec["sampled_at"])
        rec["sampled_at"] = None
    for rec, fmt in zip(take(6), ["{p}{n}", "{p_lower}-{n} ", "{p} {n}"] * 2):
        p, n = rec["customer_unit_id"].split("-")
        log(rec, "unit_id_format", true_value=rec["customer_unit_id"])
        rec["customer_unit_id"] = fmt.format(p=p, n=n, p_lower=p.lower())
    for rec in take(3):
        log(rec, "missing_unit_id", true_value=rec["customer_unit_id"])
        rec["customer_unit_id"] = None
    for rec in take(4):
        log(rec, "missing_oil_hours", true_value=rec["meter"]["oil_hours"])
        rec["meter"]["oil_hours"] = None
    for k, rec in enumerate(take(5)):
        test = ["V100", "TBN", "Fe", "SOOT", "Si"][k]
        log(rec, "missing_result", field=test)
        if k % 2:
            _result(rec, test)["value"] = None
        else:
            rec["results"].remove(_result(rec, test))
    for rec in take(5):
        r = _result(rec, "WATER")
        log(rec, "water_in_ppm", true_value=r["value"])
        r["value"], r["unit"] = round(r["value"] * 10_000), "ppm"
    for rec in take(8):
        log(rec, "unit_label_variant")
        for r in rec["results"]:
            r["unit"] = {"ppm": "mg/kg", "cSt": "mm2/s"}.get(r["unit"], r["unit"])
    for rec in take(2):
        r = _result(rec, "V100")
        log(rec, "decimal_slip", field="V100", true_value=r["value"])
        r["value"] = round(r["value"] * 10, 1)

    extra = []
    for rec in take(8):
        log(rec, "exact_duplicate")
        extra.append(copy.deepcopy(rec))
    for rec in take(4):
        # The first issue had a wrong Fe value; the lab re-issued a corrected version.
        amended = copy.deepcopy(rec)
        amended["status"] = "amended"
        amended["updated_at"] = (datetime.fromisoformat(rec["updated_at"]) + timedelta(days=5)).isoformat()
        fe = _result(rec, "Fe")
        log(rec, "amended_reissue", field="Fe", true_value=fe["value"])
        fe["value"] = round(fe["value"] * 1.6 + 10)
        extra.append(amended)
    return records + extra, truth


def generate(seed: int = SEED) -> None:
    rng = np.random.default_rng(seed)
    samples, register = _simulate_fleet(rng)
    samples.sort(key=lambda s: (s["sampled"], s["unit_id"]))
    records = [_to_record(i, s, rng) for i, s in enumerate(samples, start=1)]

    spike = next(i for i, s in enumerate(samples) if s.get("is_spike"))
    truth_faults = pd.DataFrame(
        [{k: f.get(k) for k in ("unit_id", "fault", "onset", "resolved", "signature")} for f in FAULTS]
        + [{"unit_id": SPIKE["unit_id"], "fault": "Contaminated sample (not a real fault)",
            "onset": samples[spike]["sampled"], "resolved": None, "signature": "Fe,Si,Al",
            "sample_id": records[spike]["sample_id"]}]
    )

    feed, truth_dq = _inject_errors(records, seed)
    feed.sort(key=lambda r: r["updated_at"])

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "lab_feed.json").write_text(json.dumps(feed, indent=1), encoding="utf-8")
    register.to_csv(config.DATA_DIR / "asset_register.csv", index=False)
    truth_faults.to_csv(config.DATA_DIR / "truth_faults.csv", index=False)
    pd.DataFrame(truth_dq).to_csv(config.DATA_DIR / "truth_dq.csv", index=False)
    print(f"Generated {len(records)} samples for {len(register)} units "
          f"({len(feed)} feed records incl. duplicates/re-issues, {len(truth_dq)} injected issues).")


if __name__ == "__main__":
    generate()
