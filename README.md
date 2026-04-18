# VesselFinder Scraper

Python client that pulls live maritime data from [vesselfinder.com](https://www.vesselfinder.com) — vessel search, port activity (arrivals / departures / in-port / expected), full vessel details, and live ship positions inside any bounding box.

All data comes from VesselFinder's public web endpoints; no API key or login is required.

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (the project is managed with uv)
- Dependency: `requests` (already declared in `pyproject.toml`)

## Setup

```bash
uv sync
```

## Running the CLI

The entry point is `vesselfinder_api.py`. It accepts one argument:

| Command | What it does |
|---|---|
| `uv run vesselfinder_api.py` | Default — scrapes every port around the Strait of Hormuz |
| `uv run vesselfinder_api.py hormuz` | Same as default |
| `uv run vesselfinder_api.py positions` | Live positions of all ships inside the Strait of Hormuz bounding box |
| `uv run vesselfinder_api.py IRBND001` | Arrivals / departures / in-port / expected for a single port (any 8-char LOCODE) |

### Example — live positions in the Strait of Hormuz

```bash
uv run vesselfinder_api.py positions
```

Output:

```
Strait of Hormuz — 601 ships currently reporting position

      MMSI       LAT      LON  NAME
------------------------------------------------------------
 209258000   25.4854  55.3802  DUBAI FORTUNE
 228410600   26.5180  56.0243  CMA CGM EVERGLADE
 241437000   25.3789  56.5617  MARAN ARETE
 ...
```

Full results are also written to `hormuz_positions.json`.

### Example — port activity (Bandar Abbas)

```bash
uv run vesselfinder_api.py IRBND001
```

Gives four tables: **expected**, **arrivals**, **departures**, **in_port**, each with MMSI/IMO, flag, country, name, and ship type.

### Built-in Hormuz port list

| LOCODE | Port |
|---|---|
| IRBND001 | Bandar Abbas (Iran) |
| IRBIK001 | Bandar Imam Khomeini (Iran) |
| IRKHK001 | Kharg Island (Iran) |
| AEJEA001 | Jebel Ali (UAE) |
| AEAUH001 | Abu Dhabi (UAE) |
| AEKLF001 | Khor Fakkan (UAE) |
| AEFJR001 | Fujairah (UAE) |
| OMSOH001 | Sohar (Oman) |
| OMOPQ002 | Muscat (Oman) |

> VesselFinder uses its own LOCODE variants — the UN/LOCODE you'd find in other
> datasets (`IRBKM`, `IRKHG`, `OMMCT`) return 404. The codes above are the ones
> the site actually serves.

## Using it as a library

All functions can be imported and composed:

```python
from vesselfinder_api import (
    search_vessels,
    vessel_details,
    vessel_track,
    port_activity,
    ships_in_bbox,
)

# 1) Search vessels by name (HTML scrape of /vessels?name=...)
results = search_vessels("queen")
# -> [{"mmsi": "247237800", "flag": "it", "country": "Italy",
#      "name": "QUEEN", "type": "Unknown"}, ...]

# 2) Full details for an MMSI (JSON from /api/pub/click/<mmsi>)
details = vessel_details("247237800")
# -> {"ss": 0.0, "country": "Italy", "cu": 72.4, "draught": 24, "name": "QUEEN", ...}

# 3) Raw AIS track bytes (if the vessel has public track data)
track_bytes = vessel_track("247237800")

# 4) Port activity — expected / arrivals / departures / in_port
activity = port_activity("IRBND001")
for vessel in activity["in_port"]:
    print(vessel["name"], vessel["type"])

# 5) Live positions inside any bounding box (worldwide)
ships = ships_in_bbox(
    lat_min=24.5, lat_max=27.5,
    lon_min=54.5, lon_max=58.5,
    zoom=8,
)
for s in ships:
    print(s["mmsi"], s["name"], s["lat"], s["lon"])
```

## Function reference

### `search_vessels(name: str) -> list[dict]`
Scrapes the vessel search page (`/vessels?name=...`) and returns up to 20 hits with `mmsi`, `flag`, `country`, `name`, `type`.

### `vessel_details(mmsi) -> dict`
Calls the JSON endpoint `/api/pub/click/<mmsi>`. Notable fields:

| Key | Meaning |
|---|---|
| `name`, `country`, `type`, `a2` | identity + ISO-2 flag |
| `ss` | speed over ground (knots) |
| `cu` | course over ground (degrees) |
| `draught` | current draught |
| `al`, `aw` | length / width (meters) |
| `dest` | destination (free text) |
| `etaTS`, `ts` | ETA and last-report Unix timestamps |
| `pic` | photo asset id |

### `vessel_track(mmsi) -> bytes`
Raw AIS track binary from `/api/pub/track/<mmsi>`. Returns `b""` / a few bytes when no track is published for that vessel.

### `port_activity(locode: str) -> dict[str, list[dict]]`
Fetches `/ports/<LOCODE>` and parses the four tables into structured data:

```python
{
    "expected":   [...],  # vessels with ETA
    "arrivals":   [...],  # recent arrivals
    "departures": [...],  # recent departures
    "in_port":    [...],  # currently berthed
}
```

Each vessel dict has `id` (MMSI or IMO), `flag`, `country`, `name`, `type`.

### `ships_in_bbox(lat_min, lat_max, lon_min, lon_max, zoom=8) -> list[dict]`
Hits `/api/pub/mp2?bbox=...&zoom=...` with the same scaled-Int32 bbox format VesselFinder's map uses internally, then parses the big-endian binary payload into a list of dicts:

```python
{
    "mmsi": 228410600,
    "name": "CMA CGM EVERGLADE",
    "lat": 26.5180,
    "lon": 56.0243,
    "draught": 22,
    "icon": 32,        # ship type icon index
    "color": 6,        # map color bucket
    "is_old": False,   # position stale (> ~1h)
    "is_sar": False,   # search-and-rescue vessel
}
```

The binary format is reverse-engineered from VesselFinder's map worker script — coordinates are stored as `int32 / 600000` (standard AIS convention).

### `parse_mp2(buf: bytes, zoom: int) -> list[dict]`
The low-level binary decoder used by `ships_in_bbox`. Exposed so you can feed it cached bytes.

## Output files

Running the CLI writes to the current directory:

| File | Written by |
|---|---|
| `hormuz.json` | `run_hormuz` — full activity dump for all 9 Hormuz ports |
| `hormuz_positions.json` | `positions` — every ship currently in the Strait bbox |
| `.vf_cache/` | cached HTTP responses (safe to delete anytime) |

## Being polite to VesselFinder

The site is not an official API, so the client is built to behave:

- **Browser headers** — `User-Agent` and `Referer` are set so requests aren't blocked outright.
- **On-disk cache** — every response is cached under `.vf_cache/` (keyed by URL + params) for `VF_CACHE_TTL` seconds (default **300s**; live positions use 60s). Re-running within the TTL hits disk, not the network.
- **Rate limit** — a minimum `VF_MIN_INTERVAL` (default **1.0s**) is enforced between outgoing requests.

Tunable via env vars:

```bash
VF_CACHE_DIR=.vf_cache VF_CACHE_TTL=300 VF_MIN_INTERVAL=1.0 uv run vesselfinder_api.py hormuz
```

Set `VF_CACHE_TTL=0` to bypass the cache entirely (not recommended for bulk scrapes).

## Other caveats

- Free-tier coverage only. Satellite-AIS vessels in mid-ocean may not appear; some distance/ETA cells on port pages are gated behind a paid plan and show `-` in the scrape.

## Files in the repo

| File | Purpose |
|---|---|
| `vesselfinder_api.py` | Main script — CLI + library functions |
| `main.py` | Original scratch file (unrelated) |
| `pyproject.toml`, `uv.lock` | uv project metadata |
