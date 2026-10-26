"""Monthly condition report: one self-contained HTML file that can be emailed or printed."""
from __future__ import annotations

import base64
import html
import io
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import config  # noqa: E402
from .analyse import STATUS_ORDER, Analysed  # noqa: E402
from .clean import Cleaned  # noqa: E402

INK, INK_2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES, SERIES_2 = "#2a78d6", "#86b6ef"
STATUS_COLOR = {"Normal": "#0ca30c", "Watch": "#fab219", "Caution": "#ec835a", "Critical": "#d03b3b"}
STATUS_ICON = {"Normal": "&#10003;", "Watch": "&#9679;", "Caution": "&#9650;", "Critical": "&#9632;"}


def _chip(status: str) -> str:
    return (f'<span class="chip"><span style="color:{STATUS_COLOR[status]}">{STATUS_ICON[status]}</span> '
            f"{status}</span>")


def _esc(x) -> str:
    return html.escape("" if pd.isna(x) else str(x))


def _table(df: pd.DataFrame, raw_cols: tuple[str, ...] = ()) -> str:
    head = "".join(f"<th>{_esc(c)}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(f"<td>{v if c in raw_cols else _esc(v)}</td>" for c, v in zip(df.columns, row))
                   + "</tr>" for row in df.itertuples(index=False))
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _unit_chart(unit_id: str, tests: list[str], a: Analysed, limits: pd.DataFrame,
                start: pd.Timestamp, end: pd.Timestamp) -> str:
    res = a.results[a.results["unit_id"] == unit_id]
    fig, axes = plt.subplots(1, len(tests), figsize=(3.4 * len(tests), 2.5), squeeze=False)
    for ax, test in zip(axes[0], tests):
        r = res[res["test"] == test].sort_values("sample_date")
        ax.axvspan(start, end, color="#f0efec", zorder=0)
        flagged = r["anomaly"].notna() | r["limit_status"].isin(["Caution", "Critical"])
        if test in config.ACCUMULATIVE:
            n = r.dropna(subset=["value_per_250h"])
            ax.plot(n["sample_date"], n["value_per_250h"], color=SERIES, lw=2, zorder=2, label="per 250 oil h")
            ax.scatter(r["sample_date"], r["value"], s=14, color=SERIES_2, zorder=3, label="reported")
        else:
            ax.plot(r["sample_date"], r["value"], color=SERIES, lw=2, marker="o", ms=3.5, zorder=2, label="reported")
        ax.scatter(r.loc[flagged, "sample_date"], r.loc[flagged, "value"], s=60, facecolors="none",
                   edgecolors=STATUS_COLOR["Critical"], linewidths=1.5, zorder=4, label="flagged")
        for level in ("caution", "critical"):
            for side in ("high", "low"):
                limit = limits.loc[test, f"{level}_{side}"] if test in limits.index else float("nan")
                if pd.isna(limit):
                    continue
                color = STATUS_COLOR["Caution" if level == "caution" else "Critical"]
                ax.axhline(limit, color=color, lw=1, ls="--", zorder=1)
                # High limits labelled above the line, low limits below, so pairs never collide.
                ax.annotate(level, (1, limit), xycoords=("axes fraction", "data"),
                            xytext=(-2, 2 if side == "high" else -2), textcoords="offset points", ha="right",
                            va="bottom" if side == "high" else "top", fontsize=7, color=INK_2)
        name, unit = config.TESTS[test]
        ax.set_title(f"{name} ({unit})", fontsize=9, color=INK, loc="left")
        ax.tick_params(labelsize=7, colors=MUTED)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b"))
        ax.grid(axis="y", color=GRID, lw=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(AXIS)
        if test != "V100":  # viscosity is read around its nominal value, not from zero
            ax.set_ylim(bottom=0)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), fontsize=7, frameon=False,
               bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, facecolor="white")
    plt.close(fig)
    return f'<img alt="{unit_id} trend chart" src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}">'


def _alert_since(unit_samples: pd.DataFrame) -> pd.Timestamp:
    """Start of the current run of non-normal samples."""
    since = unit_samples["sample_date"].iloc[-1]
    for status, d in zip(unit_samples["status"][::-1], unit_samples["sample_date"][::-1]):
        if status == "Normal":
            break
        since = d
    return since


def build_monthly_report(c: Cleaned, a: Analysed, limits: pd.DataFrame, month: str | None = None) -> str:
    samples = a.samples.sort_values("sample_date")
    period = pd.Period(month, "M") if month else samples["sample_date"].max().to_period("M")
    start, end = period.start_time, period.end_time.normalize()
    upto = samples[samples["sample_date"] <= end]
    in_month = upto[upto["sample_date"] >= start]
    register = pd.read_csv(config.DATA_DIR / "asset_register.csv")

    # Latest sample per unit as at month end -> fleet status and action list.
    latest = upto.groupby("unit_id").tail(1).set_index("unit_id").reindex(register["unit_id"])
    actions, charts = [], []
    for unit_id, s in latest.iterrows():
        if pd.isna(s["status"]) or s["status"] == "Normal" or s["sample_date"] < start:
            continue
        actions.append({
            "Unit": unit_id, "Status": _chip(s["status"]), "Last sample": s["sample_date"].date(),
            "In alert since": _alert_since(upto[upto["unit_id"] == unit_id]).date(),
            "Tests": s["attention_tests"], "Likely cause": s["diagnosis"], "Recommended action": s["recommendation"],
            "_rank": STATUS_ORDER.index(s["status"]),
        })
    actions.sort(key=lambda r: -r["_rank"])
    for r in actions:
        tests = [t.strip() for t in r["Tests"].split(",") if t.strip()][:3]
        charts.append(f'<h3>{r["Unit"]} &middot; {_esc(r["Likely cause"])}</h3>'
                      + _unit_chart(r["Unit"], tests, a, limits, start, end))
    action_df = pd.DataFrame(actions).drop(columns="_rank") if actions else pd.DataFrame()

    fleet = pd.DataFrame({
        "Unit": latest.index, "Type": register.set_index("unit_id").loc[latest.index, "unit_type"].to_numpy(),
        "Last sample": [d.date() if not pd.isna(d) else "none" for d in latest["sample_date"]],
        "Days since sample": [(end - d).days if not pd.isna(d) else "" for d in latest["sample_date"]],
        "Samples this month": [int((in_month["unit_id"] == u).sum()) for u in latest.index],
        "Status": [_chip(s) if not pd.isna(s) else "" for s in latest["status"]],
    })
    overdue = fleet[pd.to_numeric(fleet["Days since sample"], errors="coerce") > config.OVERDUE_DAYS]

    issues = c.issues[c.issues["sample_id"].isin(in_month["sample_id"])]
    dq = (issues.groupby(["issue", "action"]).size().rename("count").reset_index()
          .sort_values("count", ascending=False))
    dq.columns = ["Issue", "Action taken", "Count"]
    fixed = int(issues["action"].isin(["corrected", "imputed", "dropped"]).sum())

    n_units = len(register)
    needing_action = sum(1 for r in actions if "Watch" not in r["Status"])
    tiles = [
        ("Samples this month", len(in_month)),
        ("Units sampled", f"{in_month['unit_id'].nunique()} / {n_units}"),
        ("Units needing action", needing_action),
        ("Units on watch", len(actions) - needing_action),
        ("Data issues fixed automatically", fixed),
        ("Open items for the lab", len(c.quarantine)),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="v">{v}</div><div class="k">{k}</div></div>' for k, v in tiles)

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Oil Condition Report {period}</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: {INK}; background: #ffffff;
         max-width: 1080px; margin: 0 auto; padding: 24px 16px 48px; line-height: 1.45; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }} h2 {{ font-size: 18px; margin: 32px 0 8px; }}
  h3 {{ font-size: 14px; margin: 20px 0 4px; color: {INK_2}; }}
  .sub {{ color: {INK_2}; margin: 0 0 20px; }}
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
  .tile {{ border: 1px solid {GRID}; border-radius: 8px; padding: 12px; }}
  .tile .v {{ font-size: 26px; font-weight: 600; }} .tile .k {{ color: {INK_2}; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  .scroll {{ overflow-x: auto; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid {GRID}; vertical-align: top; }}
  th {{ color: {INK_2}; font-weight: 600; }} td {{ font-variant-numeric: tabular-nums; }}
  .chip {{ white-space: nowrap; }} img {{ max-width: 100%; height: auto; }}
  .note {{ color: {INK_2}; font-size: 13px; }}
</style></head><body>
<h1>Engine oil condition report &middot; {period.strftime('%B %Y')}</h1>
<p class="sub">Heavy fleet engine oil analysis &middot; {n_units} units &middot; samples to {end.date()}
 &middot; generated {date.today()} &middot; synthetic demo data</p>
<div class="tiles">{tiles_html}</div>

<h2>Units needing attention</h2>
<p class="note">Latest sample per unit at month end. <b>Critical/Caution</b> = fixed limit exceeded.
<b>Watch</b> = unusual for this unit (jump or steady rise against its own history) but still inside limits.</p>
<div class="scroll">{_table(action_df, raw_cols=("Status",)) if actions else "<p>No units need attention this month.</p>"}</div>

<h2>Trends</h2>
<p class="note">Shaded band = report month. Dots = value reported by the lab; line = value per 250 oil hours
(removes the effect of oil age, so samples are comparable). Circled = flagged sample.</p>
{"".join(charts)}

<h2>Fleet sampling status</h2>
<div class="scroll">{_table(fleet, raw_cols=("Status",))}</div>
<p class="note">{"Overdue (no sample in " + str(config.OVERDUE_DAYS) + " days): " + ", ".join(overdue["Unit"]) if len(overdue) else "No units overdue for sampling."}</p>

<h2>Data quality</h2>
<p class="note">Problems found in this month's lab data and what the pipeline did. Every change is logged in
dq_issues.csv; nothing is altered silently.</p>
<div class="scroll">{_table(dq) if len(dq) else "<p>No issues.</p>"}</div>
<h3>Records held back (need the lab to confirm)</h3>
<div class="scroll">{_table(c.quarantine.rename(columns={"sample_id": "Sample", "reason": "Reason", "record": "Detail"})) if len(c.quarantine) else "<p>None.</p>"}</div>

<h2>Method</h2>
<p class="note">Limits: indicative diesel engine guidelines in config/limits.csv (to be replaced by the customer's/OEM limits).
Anomaly detection: robust z-score (median/MAD) of each test against the unit's own last {config.ANOMALY_WINDOW} normal samples,
threshold {config.ANOMALY_Z}; trend = linear fit over the last {config.TREND_POINTS} samples with R&sup2; &ge; {config.TREND_MIN_R2}.
Likely causes come from standard oil analysis signatures (e.g. Na + K = coolant, Si + Al = dirt, Pb/Cu/Sn = bearings)
and are prompts for inspection, not diagnoses.</p>
</body></html>"""
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORT_DIR / f"monthly_report_{period}.html"
    path.write_text(page, encoding="utf-8")
    return str(path)
