import unittest

import numpy as np
import pandas as pd

from oilmon import analyse


def unit_samples(values, test="Fe", unit_id="HT-01", oil_hours=250):
    n = len(values)
    df = pd.DataFrame({
        "sample_id": [f"S{i}" for i in range(n)],
        "unit_id": unit_id,
        "sample_date": pd.date_range("2026-01-01", periods=n, freq="14D"),
        "oil_hours": oil_hours,
    })
    for t in analyse.DIRECTION:
        df[t] = np.nan
    df[test] = values
    return analyse.add_normalised(df)


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.limits = analyse.load_limits()

    def test_high_limits(self):
        self.assertEqual(analyse.limit_status("Fe", 20, self.limits), "Normal")
        self.assertEqual(analyse.limit_status("Fe", 50, self.limits), "Caution")
        self.assertEqual(analyse.limit_status("Fe", 150, self.limits), "Critical")

    def test_low_limits(self):
        self.assertEqual(analyse.limit_status("V100", 14.5, self.limits), "Normal")
        self.assertEqual(analyse.limit_status("V100", 12.0, self.limits), "Caution")
        self.assertEqual(analyse.limit_status("TBN", 2.5, self.limits), "Critical")

    def test_missing_value(self):
        self.assertIsNone(analyse.limit_status("Fe", float("nan"), self.limits))


class AnomalyTests(unittest.TestCase):
    def test_step_change_flagged_and_noise_not(self):
        alerts = analyse.detect_anomalies(unit_samples([14, 15, 13, 14, 16, 14, 15, 35]))
        self.assertEqual(list(alerts["sample_id"]), ["S7"])
        self.assertEqual(alerts.iloc[0]["kind"], "jump")

    def test_flagged_sample_kept_out_of_baseline(self):
        alerts = analyse.detect_anomalies(unit_samples([14, 15, 13, 14, 35, 36, 37]))
        self.assertEqual(list(alerts["sample_id"]), ["S4", "S5", "S6"])

    def test_oil_hours_normalisation(self):
        # 28 ppm at 500 oil hours is the same wear rate as 14 ppm at 250 h: not an anomaly.
        df = unit_samples([14, 15, 13, 14, 15, 28])
        df.loc[5, "oil_hours"] = 500
        df = analyse.add_normalised(df)
        self.assertTrue(analyse.detect_anomalies(df).empty)

    def test_low_viscosity_flagged(self):
        alerts = analyse.detect_anomalies(unit_samples([14.6, 14.8, 14.5, 14.7, 14.6, 13.2], test="V100"))
        self.assertEqual(list(alerts["sample_id"]), ["S5"])
        self.assertLess(alerts.iloc[0]["z"], 0)


class DiagnosisTests(unittest.TestCase):
    def test_signatures(self):
        self.assertEqual(analyse.diagnose({"Na", "K"}, set()), ["coolant"])
        self.assertEqual(analyse.diagnose({"Si", "Al"}, set()), ["dirt"])
        self.assertEqual(analyse.diagnose({"Pb", "Cu"}, set()), ["bearing"])
        self.assertEqual(analyse.diagnose({"V100"}, {"V100"}), ["fuel"])


if __name__ == "__main__":
    unittest.main()
