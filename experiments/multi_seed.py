"""Is the result luck? Re-run the whole demo on several random datasets and summarise.

    py experiments/multi_seed.py              # seeds 1-8 plus the default 946
    py experiments/multi_seed.py 10 11 12     # your own seeds

Takes about 5 seconds per seed. Leaves the outputs of the LAST seed in output/,
so run `py run_demo.py` afterwards to get back to the default dataset.
"""
import contextlib
import io
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from oilmon import evaluate, generate_data, pipeline  # noqa: E402
from oilmon.mock_api import MockLabServer  # noqa: E402

seeds = [int(s) for s in sys.argv[1:]] or [generate_data.SEED, *range(1, 9)]
rows = []
for seed in seeds:
    with contextlib.redirect_stdout(io.StringIO()):  # keep the console readable
        generate_data.generate(seed)
        server = MockLabServer(flaky=True)
        server.start_background()
        try:
            cleaned, analysed = pipeline.run(full=True)
        finally:
            server.shutdown()
            server.server_close()
        truth_faults = pd.read_csv(generate_data.config.DATA_DIR / "truth_faults.csv")
        truth_dq = pd.read_csv(generate_data.config.DATA_DIR / "truth_dq.csv")
        faults, false_alarms = evaluate.score_faults(analysed, truth_faults)
        dq = evaluate.score_dq(cleaned, truth_dq)
    row = {"seed": seed,
           "dq caught": f"{dq['caught'].sum()}/{dq['injected'].sum()}",
           "faults found": f"{(faults['first alert'] != 'missed').sum()}/{len(faults)}",
           "right cause": f"{faults['diagnosis correct'].sum()}/{len(faults)}",
           "false alarms": f"{len(false_alarms) / len(analysed.samples):.1%}"}
    for _, f in faults.iterrows():
        row[f"{f['unit']} lead (d)"] = f["anomaly lead over limits (days)"]
    rows.append(row)
    print(f"seed {seed}: done")

table = pd.DataFrame(rows)
print("\n'lead (d)' = days the anomaly flag came before the first fixed-limit breach\n")
print(table.to_string(index=False))
