
import asyncio
import sys
from collections import Counter

from app.services.discovery.geocoder import NominatimGeocoder
from app.services.discovery.normalizer import normalize_many
from app.services.discovery.overpass import OverpassClient


async def main(address: str, radius_m: int) -> None:
    geocoder = NominatimGeocoder()
    overpass = OverpassClient()

    print(f"Geocoding: {address!r} ...")
    point = await geocoder.geocode(address)
    print(f"  -> {point.lat}, {point.lng}  ({point.display_name})\n")

    print(f"Querying Overpass within {radius_m}m ...")
    elements = await overpass.find_businesses(point.lat, point.lng, radius_m)
    print(f"  -> {len(elements)} raw OSM elements\n")

    businesses = normalize_many(elements)
    keys = {b["dedup_key"] for b in businesses}
    with_phone = sum(1 for b in businesses if b["phone"])

    print(f"Normalized + named businesses: {len(businesses)}")
    print(f"Unique after dedup:           {len(keys)}")
    print(f"With a phone number:          {with_phone}  "
          f"({(with_phone/len(businesses)*100 if businesses else 0):.0f}%)")
    print(f"By category: {dict(Counter(b['category'] for b in businesses))}\n")

    print("Sample (first 15):")
    for b in businesses[:15]:
        phone = b["phone"] or "-"
        print(f"  - {b['name'][:38]:<38} [{b['category']:<8}] tel: {phone}")


if __name__ == "__main__":
    addr = sys.argv[1] if len(sys.argv) > 1 else "Barasat, 700125"
    radius = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    asyncio.run(main(addr, radius))
