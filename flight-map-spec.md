# Personal Flight Path Map — Project Spec (adsb.lol edition)

## Overview

A personal hobby project that takes my Flighty flight log and draws the **actual flown ground tracks** of my past flights on a nice-looking map. Think of it as a small enhancement to Flighty: instead of just a list of flights, I get the real ADS-B trajectories (not great-circle approximations) overlaid on a map.

Single-user, "for fun", non-commercial. Static hosting (e.g. GitHub Pages) should work for the frontend.

**Scope decision: I only care about flights from 2024 onward.** This is important because it makes the free adsb.lol data source viable (its open archive only covers ~2024+).

## Data source decision (read this — it shaped the whole design)

After evaluating options, the data source is **adsb.lol's open historical archive**. Quick rationale so the design makes sense:

- **FlightAware AeroAPI**: historical track data is **not available on the Personal tier**; it requires Standard ($100/month minimum). Too expensive for a hobby backfill. (Kept only as an optional fallback for pre-2024 flights, which are out of scope here.)
- **OpenSky Trino historical DB**: free but **not available for personal use** — access is restricted to university researchers, governments, and aviation authorities, and effectively requires an institutional email. Not an option for an individual hobbyist.
- **ADSBExchange**: cheap tier ($10/mo) is live-position only; historical trajectory is Enterprise-only with annual commitments and no one-time/project access.
- **adsb.lol open archive**: free, openly licensed (ODbL 1.0), no approval, no institutional email. Covers ~2024+. **This is the chosen source.** Trade-off: it's bulk file downloads, not a clean API, so there's real data-engineering work.

## Architecture

Two fully decoupled parts, connected only by a static GeoJSON file. There is **no runtime backend / server** — the "backend" is an offline build script I run manually.

```
Flighty CSV -> [Python offline build script] -> flights.geojson -> [JS static frontend] -> map
                - reads CSV                                          - Leaflet or MapLibre
                - tail# -> icao24                                    - loads static GeoJSON
                - downloads adsb.lol daily archives                  - no keys, no server
                - extracts the aircraft's trace
                - slices my leg, thins, splits anti-meridian
                - caches everything locally
```

### Part 1 — Python offline build script
- Run manually (e.g. `python build.py`), batch/ETL style. Not a daemon.
- No API keys needed (adsb.lol archive is open). Nothing secret ends up in the frontend.
- Heavy data work (downloads, decompression, JSON parsing, geometry, time math) -> Python's strengths.

### Part 2 — JS static frontend
- Pure static site. Loads `flights.geojson` and renders it. No backend calls.
- Start with **Leaflet** for simplicity; **MapLibre GL JS preferred for looks** (vector basemaps, dark theme, altitude-based coloring).
- Hostable as static files (GitHub Pages friendly).

## Input: Flighty CSV

Exported via Flighty -> Profile -> Settings -> Account Data -> Export Your Flights (CSV by email). Tab-separated.

### Exact column headers
```
Date	Airline	Flight	From	To	Dep Terminal	Dep Gate	Arr Terminal	Arr Gate	Canceled	Diverted To	Gate Departure (Scheduled)	Gate Departure (Actual)	Take off (Scheduled)	Take off (Actual)	Landing (Scheduled)	Landing (Actual)	Gate Arrival (Scheduled)	Gate Arrival (Actual)	Aircraft Type Name	Tail Number
```

### Fields that matter
| Field | Use |
|---|---|
| `Tail Number` | **Primary key.** Registration (e.g. N12345 / D-AIUP). Convert to `icao24` to locate the trace file. |
| `Take off (Actual)` / `Landing (Actual)` | Define the UTC time window used to slice my leg out of the aircraft's full-day trace. |
| `Date` | Which daily archive(s) to download. |
| `From` / `To` | Sanity-check the sliced track's endpoints. |
| `Diverted To` | Real landing airport differs from `To`; include when validating endpoints. |
| `Canceled` | If true -> **skip entirely** (no track, no download). |
| `Aircraft Type Name`, `Airline`, `Flight` | Carry into GeoJSON `properties` for display/filtering. |

## Step 1 — Tail Number -> icao24

The archive organizes traces by the 24-bit ICAO hex address, so a registration->hex mapping is required.
- US registrations (N-numbers) are algorithmic and can be computed directly.
- For everything else, use a lookup table: adsb.lol's standing data (e.g. the `adsblol/vrs-standing-data` / aircraft-data repos) or the OpenSky aircraft database CSV (downloadable, reg<->icao24).
- Keep this mapping cached locally.
- After locating a trace file, **verify** the embedded `r` field matches the expected registration to confirm the right aircraft.

## Step 2 — Download the adsb.lol daily archive (AUTOMATED — no manual downloads)

The daily archives are GitHub Release assets, so the script fetches them programmatically. **The user never downloads anything by hand.**

- Source repos, one per year: `adsblol/globe_history_2024`, `_2025`, `_2026` (GitHub Releases).
- **Each release = one UTC day** of global data. Release tags look like `v2025.05.18-planes-readsb-prod-0`. Assets are split tar parts, e.g. `...prod-0.tar.aa`, `...prod-0.tar.ab`.
- **Automated discovery + download flow:**
  1. From the flight's date, pick the year repo.
  2. Call the GitHub Releases REST API (`https://api.github.com/repos/adsblol/globe_history_<year>/releases`, paginated) to list releases and their assets. Match the release for the target date.
  3. Prefer the `prod` release; fall back to `staging` only if prod for that day is missing or much smaller.
  4. Download all split-part assets for that release (`browser_download_url`) and reassemble.
- **GitHub API rate limits:** unauthenticated is 60 requests/hour; with a free personal access token it's 5,000/hour. Usage here is light (a handful of date lookups), so anonymous is usually fine. Support an **optional** `GITHUB_TOKEN` env var; if present, send it as a Bearer header to raise the limit. Do not require it.
- **Size reality:** a full year repo is ~1.4 TB; per-day download is on the order of a few GB, split into parts. See "Storage strategy" below — we do NOT keep these around.
- Reassemble + untar, e.g.:
  ```
  cat v2025.05.18-planes-readsb-prod-0.tar.aa v2025.05.18-planes-readsb-prod-0.tar.ab | tar -xf - -C 2025.05.18
  ```
- **Dedupe downloads by date:** multiple flights on the same calendar day need that day's archive downloaded only once. Group CSV rows by date first.
- Watch UTC vs local date: a late-evening local departure can fall on the next/previous UTC day. May need to consider the adjacent day's archive near midnight.
- adsb.lol notes some known data-loss gaps; tolerate missing days/aircraft gracefully.

### Storage strategy (the daily files are big — minimize footprint)
The 3-ish GB/day is "the whole world for that day"; we only want one aircraft's trace out of it. Two levels of mitigation, in order of preference:
1. **Process-and-delete (baseline, required):** download a day -> extract only my aircraft's trace -> **immediately delete the raw archive and the extracted directory**. Disk peak is just one day's archive transiently; long-term we keep only the tiny derived data. Make this the default behavior.
2. **Streaming extraction (preferred optimization):** stream the download through tar (e.g. `curl ... | tar -x` targeting only `traces/{xx}/trace_full_{icao24}.json`) so the full multi-GB archive never fully lands on disk. Handle the split `.aa/.ab` parts by concatenating the streams. This drops disk peak to roughly the size of the single trace file.

Either way, the only thing that persists on disk is the per-flight derived coordinates (a few KB each) and the final `flights.geojson`.

## Step 3 — Extract the aircraft's trace

- After extraction, traces live under a readsb `globe_history` layout: `traces/{last 2 hex chars of icao24}/trace_full_{icao24}.json` (gzip-compressed JSON).
- Load the `trace_full_{icao24}.json` for my aircraft for that day.

### Trace JSON format (verified against readsb README-json.md)
Top level:
```json
{ "icao": "3c66b0", "r": "D-AIUP", "t": "A320", "timestamp": 1663259853.016, "trace": [ ... ] }
```
- `timestamp` = base Unix time (seconds, UTC) for the whole trace.
- `r`, `t` = registration and type (use `r` to verify the match).

Each element of `trace` is an **array** (not an object):
```
[ dt_seconds, lat, lon, alt, gs, track, flags, vrate, details_or_null, source, geom_alt, geom_vrate, ias, roll ]
```
- index 0 `dt_seconds`: seconds after top-level `timestamp`. **Absolute UTC of point = timestamp + dt_seconds.**
- index 1 `lat`, index 2 `lon`: WGS84 decimal degrees.
- index 3 `alt`: **altitude in FEET**, OR the string `"ground"`, OR null. (Must handle the `"ground"` string — don't treat as a number.)
- index 4 `gs`: ground speed in **KNOTS** or null.
- index 5 `track`: degrees (if alt=="ground", this is true heading instead).
- index 6 `flags`: bitfield. **`flags & 2` = start of a new leg** (separation between landing and takeoff). Useful for cutting my specific flight out of a multi-flight day.
- index 7 `vrate`: vertical rate in fpm or null.
- remaining indices: extra detail object, source, geometric altitude, etc. — mostly not needed for drawing.

**Units note:** adsb.lol/readsb uses **feet and knots** (imperial). This differs from OpenSky's metric — do not mix up field conventions.

## Step 4 — Slice my leg + clean geometry

- A `trace_full` file is the aircraft's **whole day**, which may contain several flights. Cut out **my** flight using the `Take off (Actual)` -> `Landing (Actual)` window (converted to UTC), optionally aided by the `flags & 2` new-leg markers.
- **Extract ONLY `[lon, lat, alt]` per point.** As soon as a trace point is read, keep just longitude (index 2), latitude (index 1), and altitude (index 3). **Discard every other field** — ground speed, track, flags, vertical rate, the detail object, geometric altitude, IAS, roll, source, etc. None of it reaches the cache or the output. (alt is kept only for altitude-based coloring in the frontend.)
  - Handle `alt` being the string `"ground"` or `null` — normalize to a sentinel (e.g. `0` or `null`) the frontend can interpret; don't feed the raw string into numeric coloring.
  - Drop points with null/missing lat or lon entirely.
- **Time zones:** Flighty `Take off (Actual)` / `Landing (Actual)` are likely **local time**; convert to UTC (Python `datetime` + `zoneinfo`) before comparing to the trace's absolute UTC times. Most common pitfall — get this right.
- **Anti-meridian (180° longitude) split:** any trans-Pacific leg must be split where adjacent points jump >180° in longitude, otherwise the frontend draws a straight line across the whole map. Emit `MultiLineString` or multiple features as needed.
- **Point thinning:** a leg can be hundreds–thousands of points. Apply Douglas–Peucker simplification (near-lossless visually) using `shapely`, on the lon/lat only. Keeps the map fast when many flights are layered.

## Caching

A completed historical flight's track never changes, so caching is permanent (no expiry).
- **Raw daily archives are NOT a long-term cache layer** — they're processed and deleted (see Step 2 "Storage strategy"). Don't accumulate multi-GB files.
- Persistent cache layers, both tiny: (a) the tail-number -> icao24 lookup, (b) the derived per-flight `[lon, lat, alt]` coordinates / per-flight GeoJSON.
- Cache key per flight: `tail_number + date + flight` (or `icao24 + takeoff_actual`).
- On each run: skip anything already cached; skip `Canceled` rows; never re-download a date already processed.
- Net effect: the whole backfill's bandwidth/CPU cost is paid once, and steady-state disk use is a few KB per flight plus the final `flights.geojson`.

## Output: GeoJSON

One Feature per flight (LineString, or MultiLineString if anti-meridian-split). Each coordinate is a **3-element `[lon, lat, alt]`** (GeoJSON allows a third elevation element; the frontend uses `alt` for coloring). `geometry` contains coordinates only — no ADS-B navigation/integrity fields anywhere. `properties` carry only the CSV-derived display/filter data:
```json
{
  "type": "Feature",
  "geometry": { "type": "LineString", "coordinates": [[lon, lat, alt], ...] },
  "properties": {
    "date": "2024-03-11",
    "airline": "UA",
    "flight": "UA123",
    "aircraft_type": "Boeing 737-900",
    "tail_number": "N12345",
    "from": "SFO",
    "to": "EWR",
    "diverted_to": null
  }
}
```
Note GeoJSON coordinate order is **[lon, lat, alt]** (trace gives lat, lon, alt — reorder on write). `alt` is in feet (or normalized 0/null for ground).

## Frontend look & feel

- Prefer **MapLibre GL JS** + a free-tier vector basemap (MapTiler / Stadia / Carto) over plain OSM raster for a more polished look. Dark basemap + glowing tracks suits a "flight footprint" map.
- Nice-to-have: color tracks by altitude (warm = low, cool = high) — altitude is per-point in the data.
- Click a track -> popup with `properties` info.
- Nice-to-have: filter by year / airline.
- If using Leaflet with many polylines, enable the `L.canvas` renderer.

## Build order / phasing

**Phase 1 (now): Python build script**
1. Parse each CSV row -> `{ date, airline, flight, from, to, divertedTo, tail_number, takeoff_actual, landing_actual, canceled }`.
2. Skip `Canceled`. Group remaining rows by UTC date.
3. Tail Number -> icao24 (algorithmic for N-numbers; lookup table otherwise; cache).
4. For each needed date: auto-fetch the adsb.lol archive via the GitHub Releases API (prod, fallback staging), reassemble parts, extract (prefer streaming). **Delete raw archive after extracting my trace.**
5. Open `traces/{xx}/trace_full_{icao24}.json`; verify `r`.
6. Slice my leg by UTC time window (+ leg flags); convert all times to UTC. **Keep only `[lon, lat, alt]` per point; discard all other fields.**
7. Anti-meridian split + Douglas–Peucker thinning (on lon/lat).
8. Write GeoJSON Feature: `[lon, lat, alt]` coordinates + CSV-only `properties`.
9. Cache the tiny derived data; tolerate not-found (missing day, aircraft not in archive, out-of-coverage) by logging and skipping.

**Phase 1 frontend:** static page, Leaflet or MapLibre, load `flights.geojson`, draw all tracks, click-for-popup.

**Phase 2 (optional, later):** incremental updates for newly flown flights via adsb.lol's live API (drop-in ADSBExchange-compatible) or OpenSky's free REST tracks endpoint (recent data only). Out of scope for now.

## Hard constraints / things not to get wrong
1. Data is **bulk file download**, not an API — design around multi-GB daily archives, reassembling split tars, and deduping downloads by date.
2. **Downloads are automated** via the GitHub Releases REST API (no manual downloading). Optional `GITHUB_TOKEN` raises the rate limit from 60/hr to 5,000/hr; don't require it.
3. **Never keep raw daily archives long-term** — process-and-delete (or stream-extract). Steady-state disk = a few KB per flight + final GeoJSON.
4. **Keep only `[lon, lat, alt]` per point.** Discard speed, track, flags, vertical rate, the detail object, and everything else. Nothing but coordinates reaches the cache/output.
5. Trace points are **arrays with positional fields**; altitude (index 3) in **feet** and can be the string `"ground"` or null — normalize it. Coordinates are at indices 1 (lat) and 2 (lon).
6. Point time = top-level `timestamp` + per-point `dt_seconds`, in **UTC**.
7. Convert Flighty local times to UTC before slicing.
8. A `trace_full` file is a **whole day / possibly multiple flights** — slice out only my leg.
9. Split lines at the anti-meridian.
10. Skip `Canceled` rows before downloading anything.
11. Account for `Diverted To` when validating endpoints.
12. Verify the trace file's `r` matches the expected registration.
13. Tolerate missing data (out-of-coverage, archive gaps) — log and continue.
14. GeoJSON coordinates are **[lon, lat, alt]**; trace gives [lat, lon, alt] — reorder on write.
