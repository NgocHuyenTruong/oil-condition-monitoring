"""Run the whole process: ingest -> clean -> analyse -> Power BI tables -> monthly report.

    py -m oilmon.pipeline                  # incremental pull, report for latest month
    py -m oilmon.pipeline --full           # re-pull everything from the API
    py -m oilmon.pipeline --month 2026-08  # report for a given month
    py -m oilmon.pipeline --offline        # skip the API, re-process saved raw pulls
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import analyse, clean, config, ingest, report


def write_powerbi_tables(c: clean.Cleaned, a: analyse.Analysed, register: pd.DataFrame,
                         limits: pd.DataFrame) -> None:
    """Star schema for Power BI: two fact tables plus unit, test and data-quality tables."""
    out = config.POWERBI_DIR
    out.mkdir(parents=True, exist_ok=True)
    sample_cols = ["sample_id", "unit_id", "sample_date", "sample_date_quality", "received_at", "reported_at",
                   "unit_hours", "oil_hours", "oil_changed", "lab_status", "status", "attention_tests",
                   "diagnosis", "recommendation", "isolated_spike"]
    a.samples[sample_cols].to_csv(out / "fact_samples.csv", index=False, date_format="%Y-%m-%d")
    (a.results.round({"value_per_250h": 2, "unit_baseline": 2})
     .to_csv(out / "fact_results.csv", index=False, date_format="%Y-%m-%d"))
    a.alerts.to_csv(out / "alerts.csv", index=False, date_format="%Y-%m-%d")
    register.to_csv(out / "dim_unit.csv", index=False)
    tests = pd.DataFrame([{"test": t, "description": d, "unit": u, "sort_order": i,
                           "normalised_to_250h": t in config.ACCUMULATIVE}
                          for i, (t, (d, u)) in enumerate(config.TESTS.items())])
    tests.merge(limits.reset_index(), on="test", how="left").to_csv(out / "dim_test.csv", index=False)
    c.issues.to_csv(out / "dq_issues.csv", index=False)
    c.quarantine.to_csv(out / "quarantine.csv", index=False)


def run(month: str | None = None, full: bool = False, offline: bool = False) -> tuple[clean.Cleaned, analyse.Analysed]:
    if not offline:
        ingest.pull(full=full)
    register = pd.read_csv(config.DATA_DIR / "asset_register.csv")
    limits = analyse.load_limits()
    c = clean.clean(ingest.load_raw_store(), register)
    print(f"Clean: {len(c.samples)} samples kept, {len(c.quarantine)} quarantined, {len(c.issues)} issues logged")
    a = analyse.analyse(c, limits)
    print("Status:", a.samples["status"].value_counts().to_dict())
    write_powerbi_tables(c, a, register, limits)
    path = report.build_monthly_report(c, a, limits, month)
    print(f"Power BI tables -> {config.POWERBI_DIR}\nMonthly report -> {path}")
    return c, a


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--month", help="report month as YYYY-MM (default: latest month with samples)")
    p.add_argument("--full", action="store_true", help="ignore the watermark and re-pull everything")
    p.add_argument("--offline", action="store_true", help="do not call the API")
    args = p.parse_args()
    run(args.month, args.full, args.offline)
