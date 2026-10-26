# Power BI setup

Two ways to connect. **Option A is recommended**: Python does the cleaning and anomaly
detection, and Power BI only shows the curated tables.

| | Option A: curated CSVs (recommended) | Option B: API straight into Power Query |
|---|---|---|
| Source | `output/powerbi/*.csv` written by the pipeline | the lab API, via `lab_api.pq` |
| Cleaning | all checks in `oilmon/clean.py`, fully logged | simple checks only (latest version, unit IDs, date formats, units) |
| Anomaly detection | yes | no (hard to maintain in M/DAX) |
| Refresh | run the pipeline, then refresh Power BI | Power BI refresh calls the API directly |

## Option A: load the curated tables

1. Run the pipeline: `py run_demo.py` (or `py -m oilmon.pipeline` against the real API).
2. Power BI Desktop > Get data > Text/CSV, load each file from `output/powerbi/`:
   `fact_samples`, `fact_results`, `dim_unit`, `dim_test`, `dq_issues`.
   Check `sample_date` is typed Date, and `value`, `value_per_250h` are Decimal.
   (For a shared report, put the folder on SharePoint/OneDrive and use the SharePoint Folder connector,
   so the Service can refresh without a gateway.)
3. Modeling > New table: paste the `Date` table from `measures.dax`; mark it as a date table.
4. Relationships (all single direction, one-to-many):

   ```
   dim_unit[unit_id]        -> fact_samples[unit_id]
   dim_unit[unit_id]        -> fact_results[unit_id]
   dim_test[test]           -> fact_results[test]
   Date[Date]               -> fact_samples[sample_date]
   Date[Date]               -> fact_results[sample_date]
   fact_samples[sample_id]  -> dq_issues[sample_id]
   ```

   Do not relate `fact_samples` to `fact_results` directly: with the links above that would create
   two filter paths from `dim_unit`/`Date` to `fact_results` (ambiguous model).
5. Add the measures from `measures.dax` (one New measure each).

For a polished three-page design (theme, page backgrounds, exact positions and colour rules), follow
**[DASHBOARD_DESIGN.md](DASHBOARD_DESIGN.md)**; it also needs `quarantine.csv` loaded.

### Suggested pages (simple version)

1. **Fleet overview**: slicer on `Date[Month]`; cards for Samples, Units Needing Action, Overdue Units,
   DQ Auto-fixed; table of `dim_unit[unit_id]` with Latest Status, Latest Diagnosis, Days Since Last Sample;
   matrix of unit x test showing Latest Value, conditionally formatted on Latest vs Caution %.
2. **Unit trends**: slicers for unit and test; line chart of Avg Value per 250h and Avg Value by
   `sample_date`, with Caution Limit and Critical Limit as extra lines.
3. **Data quality**: bar chart of DQ Issues by `dq_issues[issue]`, split by `action`; table of the
   quarantined records to send back to the lab.

## Option B: call the API from Power Query

1. In a terminal: `py -m oilmon.mock_api` (leave it running).
2. Power BI Desktop > Transform data > Manage parameters > New: `LabApiBaseUrl` = `http://127.0.0.1:8765`,
   `LabApiKey` = `demo-key-not-a-secret` (both Text).
3. New source > Blank query > Advanced editor, paste `lab_api.pq`. When asked for credentials choose Anonymous
   (the key is sent in the `X-Api-Key` header).

Notes for a real lab API:
- Keep the base URL static and pass the path and query through `RelativePath` / `Query` (as in `lab_api.pq`);
  otherwise the Power BI Service treats the source as dynamic and will not schedule refresh.
- Keep the API key in a parameter, never typed into the query, and do not commit it to the repo.
- For large histories, filter by `modified_since` and use incremental refresh rather than re-pulling everything.
