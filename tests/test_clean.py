import unittest
from datetime import date

import pandas as pd

from oilmon import clean


def record(sample_id="LAB-1", unit="HT-01", sampled="2026-03-05", received="2026-03-08",
           updated="2026-03-10T09:00:00", results=None, oil_hours=250):
    return {
        "sample_id": sample_id, "status": "final", "customer_unit_id": unit, "sampled_at": sampled,
        "received_at": received, "reported_at": received, "updated_at": updated,
        "meter": {"unit_hours": 1000, "oil_hours": oil_hours},
        "results": results if results is not None else [{"test": "Fe", "value": 10, "unit": "ppm"}],
    }


class UnitIdTests(unittest.TestCase):
    def test_formats_are_normalised(self):
        for raw in ("HT04", "ht-04 ", "HT 04", "HT-4", "HT_04"):
            self.assertEqual(clean.normalise_unit_id(raw), "HT-04", raw)

    def test_missing(self):
        for raw in (None, "", "  ", float("nan")):
            self.assertIsNone(clean.normalise_unit_id(raw))


class DateTests(unittest.TestCase):
    def test_iso_and_australian_formats(self):
        self.assertEqual(clean.parse_date("2026-03-05"), (date(2026, 3, 5), "iso"))
        self.assertEqual(clean.parse_date("05/03/2026"), (date(2026, 3, 5), "dd/mm/yyyy"))
        self.assertEqual(clean.parse_date(None), (None, None))

    def test_day_month_swap_is_repaired(self):
        # Taken 3 May, written as 5 March: transit would be 64 days, swap gives 3 days.
        fixed, issue = clean._repair_date(date(2026, 3, 5), date(2026, 5, 6))
        self.assertEqual((fixed, issue), (date(2026, 5, 3), "day_month_swapped"))

    def test_wrong_year_is_repaired(self):
        fixed, issue = clean._repair_date(date(2027, 3, 5), date(2026, 3, 8))
        self.assertEqual((fixed, issue), (date(2026, 3, 5), "wrong_year"))

    def test_unrepairable_date_is_imputed_and_logged(self):
        log = clean.IssueLog()
        header, _ = clean.flatten([record(sampled="2026-01-20", received="2026-06-25")])
        out = clean.fix_sample_dates(header, log)
        self.assertEqual(out.loc[0, "sample_date_quality"], "imputed")
        self.assertEqual(log.frame().loc[0, "issue"], "invalid_sample_date")


class DuplicateTests(unittest.TestCase):
    def test_exact_duplicate_dropped_and_amendment_wins(self):
        original = record(results=[{"test": "Fe", "value": 40, "unit": "ppm"}])
        amended = record(updated="2026-03-15T09:00:00", results=[{"test": "Fe", "value": 12, "unit": "ppm"}])
        amended["status"] = "amended"
        log = clean.IssueLog()
        kept = clean.deduplicate([original, dict(original), amended], log, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["results"][0]["value"], 12)
        self.assertEqual(sorted(log.frame()["issue"]), ["exact_duplicate", "superseded_version"])

    def test_amendment_received_before_original_still_wins(self):
        older = record(updated="2026-03-10T09:00:00", results=[{"test": "Fe", "value": 40, "unit": "ppm"}])
        newer = record(updated="2026-03-15T09:00:00", results=[{"test": "Fe", "value": 12, "unit": "ppm"}])
        kept = clean.deduplicate([newer, older], clean.IssueLog(), [])
        self.assertEqual(kept[0]["results"][0]["value"], 12)


class UnitAndValueTests(unittest.TestCase):
    def test_water_ppm_converted_and_labels_standardised(self):
        _, results = clean.flatten([record(results=[
            {"test": "WATER", "value": 300, "unit": "ppm"},
            {"test": "Fe", "value": 10, "unit": "mg/kg"},
        ])])
        log = clean.IssueLog()
        out = clean.standardise_units(results, log).set_index("test")
        self.assertAlmostEqual(out.loc["WATER", "value"], 0.03)
        self.assertEqual(out.loc["WATER", "unit"], "%")
        self.assertEqual(out.loc["Fe", "unit"], "ppm")
        self.assertEqual(set(log.frame()["issue"]), {"unit_conversion", "unit_label_standardised"})

    def test_implausible_value_removed(self):
        header, results = clean.flatten([record(results=[{"test": "V100", "value": 145.0, "unit": "cSt"}])])
        results["unit"] = "cSt"
        log = clean.IssueLog()
        out = clean.check_values(results.drop(columns="unit_raw"), header, log)
        self.assertTrue(pd.isna(out.loc[0, "value"]))
        self.assertIn("implausible_value", set(log.frame()["issue"]))

    def test_unknown_unit_is_quarantined(self):
        register = pd.DataFrame({"unit_id": ["HT-01"], "unit_type": ["Haul truck"], "model": ["x"]})
        result = clean.clean([record(unit="ZZ-99")], register)
        self.assertEqual(len(result.samples), 0)
        self.assertEqual(result.quarantine.loc[0, "reason"], "unknown_unit_id")


if __name__ == "__main__":
    unittest.main()
