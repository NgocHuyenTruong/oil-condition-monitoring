# Pipeline evaluation (synthetic ground truth)

Samples analysed: 255. Flagged samples outside any seeded fault: 4 (1.6%).

## Seeded faults

| unit | seeded fault | onset | first alert | days to alert | first anomaly flag | first limit breach | anomaly lead over limits (days) | diagnosis correct |
|---|---|---|---|---|---|---|---|---|
| HT-07 | Progressive bearing wear | 2026-03-02 | 2026-04-14 | 43 | 2026-04-14 | 2026-04-14 | 0 | True |
| EX-02 | Dust ingestion (air intake breach) | 2026-04-20 | 2026-04-28 | 8 | 2026-04-28 | 2026-04-28 | 0 | True |
| HT-04 | Coolant leak (oil cooler) | 2026-06-08 | 2026-06-18 | 10 | 2026-06-18 | 2026-06-18 | 0 | True |
| DZ-01 | Fuel dilution (injector) | 2026-07-14 | 2026-07-23 | 9 | 2026-07-23 | 2026-08-05 | 13 | True |

Contaminated-sample spike (HT-02, LAB-26-00124) recognised as an isolated spike rather than a fault: **yes**.

## Injected data-quality errors

| injected error | injected | caught | value restored |
|---|---|---|---|
| day_month_swapped | 4 | 4 | 4/4 exact |
| dd_mm_format | 10 | 10 | 10/10 exact |
| wrong_year | 2 | 2 | 2/2 exact |
| missing_sample_date | 3 | 3 |  |
| unit_id_format | 6 | 6 |  |
| missing_unit_id | 3 | 3 |  |
| missing_oil_hours | 4 | 4 |  |
| missing_result | 5 | 5 |  |
| water_in_ppm | 5 | 5 | 5/5 exact |
| unit_label_variant | 8 | 8 |  |
| decimal_slip | 2 | 2 |  |
| exact_duplicate | 8 | 8 |  |
| amended_reissue | 4 | 4 | 4/4 exact |

Total caught: 64/64.

## Flags outside seeded faults (false alarms)

| sample_id | unit_id | sample_date | status | attention_tests | diagnosis |
|---|---|---|---|---|---|
| LAB-26-00111 | HT-06 | 2026-04-28 | Watch | Cu | Isolated spike - possible sample contamination |
| LAB-26-00155 | DZ-02 | 2026-06-12 | Watch | Si | Isolated spike - possible sample contamination |
| LAB-26-00173 | HT-06 | 2026-07-03 | Watch | Fe | Isolated spike - possible sample contamination |
| LAB-26-00230 | HT-02 | 2026-09-02 | Watch | Sn | Isolated spike - possible sample contamination |
