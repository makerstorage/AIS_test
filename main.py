import asyncio
import json
import httpx

API_LOCATIONS = "https://meri.digitraffic.fi/api/ais/v1/locations"
API_VESSELS = "https://meri.digitraffic.fi/api/ais/v1/vessels"

# Bounding box: (min_lat, min_lon, max_lat, max_lon)
# Set to None to show all vessels
# Examples:
#   Helsinki area:      (59.8, 24.5, 60.3, 25.5)
#   Gulf of Finland:    (59.0, 22.0, 61.0, 30.0)
#   Full Baltic:        (53.0, 9.0, 66.0, 31.0)
#   Strait of Hormuz:   (25.5, 54.0, 27.5, 57.5)
BOUNDING_BOX = None

# Set True to print raw JSON for each vessel
DEBUG = False

NAV_STATUS = {
    0: "Under way",
    1: "Anchored",
    2: "Not under cmd",
    3: "Restricted",
    5: "Moored",
    8: "Sailing",
}


async def fetch_vessel_names(client):
    """Fetch MMSI → ship name mapping."""
    resp = await client.get(API_VESSELS, timeout=30)
    resp.raise_for_status()
    names = {}
    for v in resp.json():
        names[v["mmsi"]] = v.get("name", "Unknown")
    return names


async def fetch_locations(client):
    """Fetch current vessel positions."""
    resp = await client.get(API_LOCATIONS, timeout=15)
    resp.raise_for_status()
    return resp.json()["features"]


async def main():
    async with httpx.AsyncClient() as client:
        print("Loading vessel names...")
        names = await fetch_vessel_names(client)
        print(f"Loaded {len(names)} vessels\n")

        while True:
            features = await fetch_locations(client)

            # Filter by bounding box if set
            if BOUNDING_BOX:
                min_lat, min_lon, max_lat, max_lon = BOUNDING_BOX
                features = [
                    f for f in features
                    if min_lat <= f["geometry"]["coordinates"][1] <= max_lat
                    and min_lon <= f["geometry"]["coordinates"][0] <= max_lon
                ]

            # Only show moving vessels (speed > 0.5 knots)
            moving = [
                f for f in features
                if f["properties"].get("sog", 0) > 0.5
            ]
            moving.sort(key=lambda f: f["properties"]["sog"], reverse=True)

            print(f"{'SHIP NAME':25s} {'MMSI':>10s}  {'LAT':>9s}  {'LON':>9s}  {'SOG':>5s}  {'COG':>5s}  STATUS")
            print("-" * 95)

            for f in moving[:20]:
                if DEBUG:
                    print(json.dumps(f, indent=2))
                p = f["properties"]
                mmsi = p["mmsi"]
                lon, lat = f["geometry"]["coordinates"]
                name = names.get(mmsi, "Unknown")
                status = NAV_STATUS.get(p.get("navStat"), "?")
                print(
                    f"{name:25s} {mmsi:>10d}  {lat:>9.4f}  {lon:>9.4f}  "
                    f"{p.get('sog', 0):>5.1f}  {p.get('cog', 0):>5.1f}  {status}"
                )

            print(f"\n  Showing top 20 of {len(moving)} moving vessels "
                  f"({len(features)} total). Refreshing in 30s...\n")
            await asyncio.sleep(30)


asyncio.run(main())
