// Lab API -> Power BI directly (Power Query M), as an alternative to the Python pipeline.
//
// Setup: Home > Transform data > New parameter, create two Text parameters:
//   LabApiBaseUrl = "http://127.0.0.1:8765"   (run `py -m oilmon.mock_api` first)
//   LabApiKey     = "demo-key-not-a-secret"
// Then New source > Blank query > Advanced editor, paste this.
//
// The base URL stays fixed and the path/query go in RelativePath/Query: a URL built by
// string concatenation is treated as "dynamic" and blocks scheduled refresh in the Service.
//
// This query does the simple cleaning (latest version, unit IDs, date formats, units).
// The harder checks (swapped day/month, wrong year, plausibility, anomaly detection)
// live in the Python pipeline, which is why the recommended setup loads its CSVs instead.
let
    PageSize = 200,
    GetPage = (page as number) as record =>
        Json.Document(
            Web.Contents(
                LabApiBaseUrl,
                [
                    RelativePath = "v1/samples",
                    Query = [page = Text.From(page), page_size = Text.From(PageSize)],
                    Headers = [#"X-Api-Key" = LabApiKey]
                ]
            )
        ),
    // Follow meta.next_page until the API reports no more pages
    Pages = List.Generate(
        () => GetPage(1),
        each _ <> null,
        each if _[meta][next_page] = null then null else GetPage(_[meta][next_page]),
        each _[data]
    ),
    Records = List.Combine(Pages),
    AsTable = Table.FromList(Records, Splitter.SplitByNothing(), {"r"}),
    Expanded = Table.ExpandRecordColumn(
        AsTable, "r",
        {"sample_id", "status", "customer_unit_id", "sampled_at", "received_at", "updated_at", "meter", "results"}
    ),
    // Labs re-issue amended results under the same sample_id: keep the newest version only
    Sorted = Table.Buffer(Table.Sort(Expanded, {{"sample_id", Order.Ascending}, {"updated_at", Order.Descending}})),
    Latest = Table.Distinct(Sorted, {"sample_id"}),
    Meter = Table.ExpandRecordColumn(Latest, "meter", {"unit_hours", "oil_hours"}),
    // "HT04", "ht-04 ", "HT 04" -> "HT-04"
    UnitId = Table.TransformColumns(
        Meter,
        {{"customer_unit_id", each
            if _ = null or Text.Trim(_) = "" then null
            else let c = Text.Upper(Text.Remove(_, {" ", "-", "_"}))
                 in Text.Start(c, 2) & "-" & Text.PadStart(Text.Middle(c, 2), 2, "0"),
          type nullable text}}
    ),
    // Missing unit IDs cannot be linked to a machine: Python quarantines them, here they are dropped
    KnownUnit = Table.SelectRows(UnitId, each [customer_unit_id] <> null),
    // Sample dates arrive as yyyy-MM-dd or as Australian dd/MM/yyyy
    SampleDate = Table.AddColumn(
        KnownUnit, "sample_date",
        each if [sampled_at] = null then null
             else try Date.FromText([sampled_at], [Format = "yyyy-MM-dd"])
             otherwise try Date.FromText([sampled_at], [Format = "dd/MM/yyyy"])
             otherwise null,
        type nullable date
    ),
    Received = Table.TransformColumns(SampleDate, {{"received_at", each Date.FromText(_, [Format = "yyyy-MM-dd"]), type date}}),
    Results = Table.ExpandRecordColumn(Table.ExpandListColumn(Received, "results"), "results", {"test", "value", "unit"}),
    // Water sometimes comes in ppm instead of %; some unit labels differ (mg/kg = ppm, mm2/s = cSt)
    Value = Table.AddColumn(
        Results, "value_std",
        each if [test] = "WATER" and [unit] = "ppm" then [value] / 10000 else [value],
        type nullable number
    ),
    Unit = Table.AddColumn(
        Value, "unit_std",
        each if [test] = "WATER" then "%"
             else if [unit] = null then null
             else Record.FieldOrDefault([#"mg/kg" = "ppm", #"mm2/s" = "cSt"], [unit], [unit]),
        type nullable text
    ),
    Selected = Table.SelectColumns(
        Unit,
        {"sample_id", "customer_unit_id", "sample_date", "received_at", "unit_hours", "oil_hours",
         "test", "value_std", "unit_std", "status"}
    ),
    Renamed = Table.RenameColumns(
        Selected,
        {{"customer_unit_id", "unit_id"}, {"value_std", "value"}, {"unit_std", "unit"}, {"status", "lab_status"}}
    ),
    Typed = Table.TransformColumnTypes(Renamed, {{"unit_hours", Int64.Type}, {"oil_hours", Int64.Type}, {"test", type text}})
in
    Typed
