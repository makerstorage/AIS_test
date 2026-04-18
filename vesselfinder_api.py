import hashlib
import json
import os
import random
import re
import struct
import sys
import time

import requests

BASE = "https://www.vesselfinder.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.vesselfinder.com/",
    "Accept": "*/*",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

CACHE_DIR = os.environ.get("VF_CACHE_DIR", ".vf_cache")
CACHE_TTL = int(os.environ.get("VF_CACHE_TTL", "300"))  # seconds
MIN_INTERVAL = float(os.environ.get("VF_MIN_INTERVAL", "1.0"))  # seconds between hits
_last_request_ts = 0.0


def _cache_path(url: str, params: dict | None) -> str:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    digest = hashlib.sha1(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, digest)


def fetch(url: str, params: dict | None = None, ttl: int | None = None) -> bytes:
    """GET with file cache + polite rate limit. Set ttl=0 to bypass cache."""
    global _last_request_ts
    ttl = CACHE_TTL if ttl is None else ttl
    path = _cache_path(url, params)
    if ttl > 0 and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        with open(path, "rb") as f:
            return f.read()

    wait = MIN_INTERVAL - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    r = SESSION.get(url, params=params, timeout=15)
    _last_request_ts = time.time()
    r.raise_for_status()

    if ttl > 0:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(r.content)
    return r.content


def search_vessels(name: str) -> list[dict]:
    """Search vessels by name. Returns a list of dicts with mmsi/name/type/flag."""
    html = fetch(f"{BASE}/vessels", params={"name": name}).decode("utf-8", "replace")

    row_re = re.compile(
        r'href="/vessels/details/(?P<mmsi>\d+)".*?'
        r'flags/4x3/(?P<flag>[a-z]{2})\.svg\).*?title="(?P<country>[^"]*)".*?'
        r'<div class="slna">(?P<name>[^<]+)</div>.*?'
        r'<div class="slty">(?P<type>[^<]+)</div>',
        re.DOTALL,
    )
    return [m.groupdict() for m in row_re.finditer(html)]


def vessel_details(mmsi: str | int) -> dict:
    """Get detailed vessel data by MMSI. Returns JSON as a dict."""
    return json.loads(fetch(f"{BASE}/api/pub/click/{mmsi}"))


def vessel_track(mmsi: str | int) -> bytes:
    """Get raw AIS track bytes for a vessel."""
    return fetch(f"{BASE}/api/pub/track/{mmsi}")


COORD_SCALE = 600000  # Int32 coordinates are degrees * 600000 (AIS convention)


def ships_in_bbox(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float, zoom: int = 8
) -> list[dict]:
    """Fetch live ship positions inside a bounding box via /api/pub/mp2.

    Returns list of {mmsi, name, lat, lon, icon, color, is_old, is_sar}.
    Reverse-engineered from vesselfinder.com's map worker (big-endian binary).
    """
    bbox = ",".join(
        str(int(v * COORD_SCALE)) for v in (lon_min, lat_min, lon_max, lat_max)
    )
    params = {
        "bbox": bbox,
        "zoom": zoom,
        "mmsi": 0,
        "ref": int(random.random() * 99999),
        "mcbe": 1,
    }
    # Live positions are volatile — use a short 60s cache.
    return parse_mp2(fetch(f"{BASE}/api/pub/mp2", params=params, ttl=60), zoom)


def parse_mp2(buf: bytes, zoom: int) -> list[dict]:
    """Parse the big-endian binary payload returned by /api/pub/mp2."""
    n = len(buf)
    if n < 4:
        return []
    # Variable header length Y (uint16 at offset 1) when buffer is at least 12 bytes.
    header_len = struct.unpack_from(">H", buf, 1)[0] if n >= 12 else 0
    i = 4 + header_len
    include_size = zoom > 13  # size fields only present when zoomed in

    ships: list[dict] = []
    while i < n:
        if i + 2 > n:
            break
        (w,) = struct.unpack_from(">h", buf, i)
        i += 2
        color = (w & 0x00F0) >> 4
        icon = (w & 0x3F00) >> 8
        is_old = bool(w & 1)
        is_sar = bool(w & 2)

        if i + 12 > n:
            break
        mmsi, lat_i, lon_i = struct.unpack_from(">iii", buf, i)
        i += 12
        lat = lat_i / COORD_SCALE
        lon = lon_i / COORD_SCALE

        if i + 2 > n:
            break
        draught = buf[i]
        name_len = buf[i + 1]
        i += 2
        if i + name_len > n:
            break
        name = buf[i : i + name_len].decode("utf-8", errors="replace")
        i += name_len

        if include_size:
            if i + 10 > n:
                break
            i += 10  # skip 5 × int16 dimension/heading fields
        if is_sar and not include_size:
            if i + 2 > n:
                break
            i += 2

        ships.append(
            {
                "mmsi": mmsi,
                "name": name or str(mmsi),
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "draught": draught,
                "icon": icon,
                "color": color,
                "is_old": is_old,
                "is_sar": is_sar,
            }
        )
    return ships


PORT_ROW_RE = re.compile(
    r'href="/vessels/details/(?P<id>\d+)".*?'
    r'flags/4x3/(?P<flag>[a-z]{2})\.svg.*?title="(?P<country>[^"]*)".*?'
    r'<div class="named-title">(?P<name>[^<]+)</div>.*?'
    r'<div class="named-subtitle">(?P<type>[^<]+)</div>',
    re.DOTALL,
)


def port_activity(locode: str) -> dict[str, list[dict]]:
    """Fetch a VesselFinder port page and parse its 4 tables into structured data.

    locode examples: IRBND001 (Bandar Abbas, Strait of Hormuz), AEJEA001 (Jebel Ali).
    Returns {"expected": [...], "arrivals": [...], "departures": [...], "in_port": [...]}.
    """
    html = fetch(f"{BASE}/ports/{locode}").decode("utf-8", "replace")

    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL)
    labels = ["expected", "arrivals", "departures", "in_port"]
    result: dict[str, list[dict]] = {k: [] for k in labels}
    for label, tbl in zip(labels, tables):
        for m in PORT_ROW_RE.finditer(tbl):
            result[label].append(m.groupdict())
    return result


# Ports ringing the Strait of Hormuz. Scrape each to cover traffic through the strait.
HORMUZ_PORTS = {
    "IRBND001": "Bandar Abbas (Iran)",
    "IRBIK001": "Bandar Imam Khomeini (Iran)",
    "IRKHK001": "Kharg Island (Iran)",
    "AEJEA001": "Jebel Ali (UAE)",
    "AEAUH001": "Abu Dhabi (UAE)",
    "AEKLF001": "Khor Fakkan (UAE)",
    "AEFJR001": "Fujairah (UAE)",
    "OMSOH001": "Sohar (Oman)",
    "OMOPQ002": "Muscat (Oman)",
}


def print_port(locode: str, label: str) -> dict:
    print(f"\n=== {label} [{locode}] ===")
    try:
        activity = port_activity(locode)
    except requests.HTTPError as e:
        print(f"  HTTP {e.response.status_code} — skipped")
        return {}
    for section, vessels in activity.items():
        print(f"-- {section.upper()} ({len(vessels)}) --")
        for v in vessels[:5]:
            print(f"  {v['id']:>10} [{v['flag']}] {v['name']:<25} {v['type']}")
    return activity


def run_hormuz() -> None:
    all_data: dict[str, dict] = {}
    for locode, name in HORMUZ_PORTS.items():
        all_data[locode] = {"name": name, "activity": print_port(locode, name)}
    with open("hormuz.json", "w") as f:
        json.dump(all_data, f, indent=2)
    print("\nSaved hormuz.json")


# Bounding box covering the Strait of Hormuz and approaches.
HORMUZ_BBOX = {"lat_min": 24.5, "lat_max": 27.5, "lon_min": 54.5, "lon_max": 58.5}


def run_positions() -> None:
    ships = ships_in_bbox(**HORMUZ_BBOX, zoom=8)
    print(f"Strait of Hormuz — {len(ships)} ships currently reporting position\n")
    print(f"{'MMSI':>10}  {'LAT':>8} {'LON':>8}  NAME")
    print("-" * 60)
    for s in sorted(ships, key=lambda x: x["mmsi"]):
        flag = " (old)" if s["is_old"] else ""
        print(f"{s['mmsi']:>10}  {s['lat']:>8.4f} {s['lon']:>8.4f}  {s['name']}{flag}")
    with open("hormuz_positions.json", "w") as f:
        json.dump(ships, f, indent=2)
    print(f"\nSaved hormuz_positions.json ({len(ships)} ships)")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "hormuz"
    cmd = arg.lower()
    if cmd == "hormuz":
        run_hormuz()
    elif cmd == "positions":
        run_positions()
    elif re.fullmatch(r"[A-Z0-9]{8}", arg):
        print_port(arg, arg)
    else:
        print(f"Usage: {sys.argv[0]} [hormuz|positions|<LOCODE>]")
        print(f"  LOCODE examples: {', '.join(HORMUZ_PORTS)}")
        sys.exit(2)
