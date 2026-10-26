"""Look at one test for one machine: the numbers and a chart.

    py experiments/explore_unit.py HT-07 Pb
    py experiments/explore_unit.py HT-04 K
    py experiments/explore_unit.py DZ-01 FUEL

Reads output/powerbi/fact_results.csv, so run `py run_demo.py` first.
The chart is saved to output/explore_<unit>_<test>.png.
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from oilmon import config  # noqa: E402

unit = sys.argv[1] if len(sys.argv) > 1 else "HT-07"
test = sys.argv[2] if len(sys.argv) > 2 else "Pb"

results = pd.read_csv(config.POWERBI_DIR / "fact_results.csv", parse_dates=["sample_date"])
samples = pd.read_csv(config.POWERBI_DIR / "fact_samples.csv")
rows = (results[(results["unit_id"] == unit) & (results["test"] == test)]
        .merge(samples[["sample_id", "oil_hours", "status"]], on="sample_id")
        .sort_values("sample_date"))
if rows.empty:
    sys.exit(f"No rows for unit {unit!r} and test {test!r}. Units look like HT-07; tests like Fe, Pb, K, FUEL, V100.")

pd.set_option("display.width", 200)
print(f"\n{unit} - {config.TESTS[test][0]} ({config.TESTS[test][1]})\n")
cols = ["sample_date", "oil_hours", "value", "value_per_250h", "limit_status", "anomaly", "z", "unit_baseline", "status"]
print(rows[cols].assign(sample_date=rows["sample_date"].dt.date).to_string(index=False))

limits = pd.read_csv(config.CONFIG_DIR / "limits.csv").set_index("test")
fig, ax = plt.subplots(figsize=(9, 3.5))
ax.plot(rows["sample_date"], rows["value"], marker="o", color="#86b6ef", label="reported by lab")
if rows["value_per_250h"].notna().any():
    n = rows.dropna(subset=["value_per_250h"])
    ax.plot(n["sample_date"], n["value_per_250h"], lw=2.2, color="#2a78d6", label="per 250 oil hours")
flagged = rows[rows["anomaly"].notna()]
ax.scatter(flagged["sample_date"], flagged["value"], s=90, facecolors="none", edgecolors="#d03b3b",
           linewidths=1.5, label="anomaly flag", zorder=5)
for col, colour in (("caution_high", "#ec835a"), ("critical_high", "#d03b3b"),
                    ("caution_low", "#ec835a"), ("critical_low", "#d03b3b")):
    if test in limits.index and not pd.isna(limits.loc[test, col]):
        ax.axhline(limits.loc[test, col], ls="--", lw=1, color=colour)
ax.set_title(f"{unit} {config.TESTS[test][0]} ({config.TESTS[test][1]}) - dashed lines are limits", loc="left")
ax.legend(frameon=False, fontsize=8)
ax.grid(axis="y", color="#e1e0d9")
fig.tight_layout()
out = config.OUTPUT_DIR / f"explore_{unit}_{test}.png"
fig.savefig(out, dpi=130)
print(f"\nChart saved: {out}")
