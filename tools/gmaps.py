#!/usr/bin/env python3
"""
Google Maps CLI tool for PKA.
Usage: discord-bridge/venv/bin/python3 tools/gmaps.py <command> [options]

Commands:
  search      Search for places (restaurants, stores, etc.)
  directions  Get directions between two locations
  geocode     Convert address to lat/lng coordinates
  reverse     Convert lat/lng to address
  nearby      Find nearby places by type
  distance    Get distance/travel time between locations

Output: JSON to stdout. Errors to stderr.

Note: Requires a Google Maps API key in data/gmaps/api_key.txt
(Unlike other tools, Maps uses an API key, not OAuth)
"""

import argparse
import json
import sys
import urllib.request
import urllib.parse
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
API_KEY_FILE = PKA_ROOT / "data" / "gmaps" / "api_key.txt"


def get_api_key():
    """Load the Google Maps API key."""
    if not API_KEY_FILE.exists():
        print(json.dumps({
            "error": "Missing API key",
            "help": "Get a Maps API key from Google Cloud Console and save it to data/gmaps/api_key.txt"
        }), file=sys.stderr)
        sys.exit(1)
    return API_KEY_FILE.read_text().strip()


def maps_request(endpoint, params):
    """Make a request to the Google Maps API."""
    key = get_api_key()
    params["key"] = key
    url = f"https://maps.googleapis.com/maps/api/{endpoint}/json?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


# ── Commands ─────────────────────────────────────────────────────────

def cmd_search(args):
    """Search for places."""
    params = {"query": args.query}
    if args.location:
        params["location"] = args.location
        params["radius"] = args.radius or 5000

    result = maps_request("place/textsearch", params)

    places = []
    for p in result.get("results", [])[:args.limit or 10]:
        places.append({
            "name": p.get("name", ""),
            "address": p.get("formatted_address", ""),
            "rating": p.get("rating", None),
            "total_ratings": p.get("user_ratings_total", 0),
            "place_id": p.get("place_id", ""),
            "types": p.get("types", []),
            "location": p.get("geometry", {}).get("location", {}),
            "open_now": p.get("opening_hours", {}).get("open_now", None),
        })

    print(json.dumps({"query": args.query, "places": places, "count": len(places)}, indent=2))


def cmd_directions(args):
    """Get directions between two locations."""
    params = {
        "origin": args.origin,
        "destination": args.destination,
        "mode": args.mode or "driving",
    }
    if args.avoid:
        params["avoid"] = args.avoid

    result = maps_request("directions", params)

    routes = []
    for route in result.get("routes", []):
        legs = route.get("legs", [])
        if legs:
            leg = legs[0]
            steps = []
            for step in leg.get("steps", []):
                # Strip HTML tags from instructions
                instructions = step.get("html_instructions", "")
                import re
                clean = re.sub(r'<[^>]+>', ' ', instructions).strip()
                steps.append({
                    "instruction": clean,
                    "distance": step.get("distance", {}).get("text", ""),
                    "duration": step.get("duration", {}).get("text", ""),
                })

            routes.append({
                "summary": route.get("summary", ""),
                "distance": leg.get("distance", {}).get("text", ""),
                "duration": leg.get("duration", {}).get("text", ""),
                "start_address": leg.get("start_address", ""),
                "end_address": leg.get("end_address", ""),
                "steps": steps,
            })

    print(json.dumps({
        "origin": args.origin,
        "destination": args.destination,
        "mode": args.mode or "driving",
        "routes": routes,
    }, indent=2))


def cmd_geocode(args):
    """Convert address to coordinates."""
    result = maps_request("geocode", {"address": args.address})

    results = []
    for r in result.get("results", []):
        loc = r.get("geometry", {}).get("location", {})
        results.append({
            "address": r.get("formatted_address", ""),
            "lat": loc.get("lat"),
            "lng": loc.get("lng"),
            "place_id": r.get("place_id", ""),
        })

    print(json.dumps({"query": args.address, "results": results}, indent=2))


def cmd_reverse(args):
    """Convert coordinates to address."""
    result = maps_request("geocode", {"latlng": args.latlng})

    results = []
    for r in result.get("results", [])[:5]:
        results.append({
            "address": r.get("formatted_address", ""),
            "types": r.get("types", []),
            "place_id": r.get("place_id", ""),
        })

    print(json.dumps({"coordinates": args.latlng, "results": results}, indent=2))


def cmd_nearby(args):
    """Find nearby places by type."""
    params = {
        "location": args.location,
        "radius": args.radius or 2000,
        "type": args.type,
    }
    if args.keyword:
        params["keyword"] = args.keyword

    result = maps_request("place/nearbysearch", params)

    places = []
    for p in result.get("results", [])[:args.limit or 10]:
        places.append({
            "name": p.get("name", ""),
            "address": p.get("vicinity", ""),
            "rating": p.get("rating", None),
            "total_ratings": p.get("user_ratings_total", 0),
            "place_id": p.get("place_id", ""),
            "open_now": p.get("opening_hours", {}).get("open_now", None),
            "location": p.get("geometry", {}).get("location", {}),
        })

    print(json.dumps({"type": args.type, "places": places, "count": len(places)}, indent=2))


def cmd_distance(args):
    """Get distance and travel time between locations."""
    origins = args.origins
    destinations = args.destinations

    params = {
        "origins": origins,
        "destinations": destinations,
        "mode": args.mode or "driving",
    }

    result = maps_request("distancematrix", params)

    rows = []
    origin_addrs = result.get("origin_addresses", [])
    dest_addrs = result.get("destination_addresses", [])

    for i, row in enumerate(result.get("rows", [])):
        for j, elem in enumerate(row.get("elements", [])):
            if elem.get("status") == "OK":
                rows.append({
                    "origin": origin_addrs[i] if i < len(origin_addrs) else origins,
                    "destination": dest_addrs[j] if j < len(dest_addrs) else destinations,
                    "distance": elem.get("distance", {}).get("text", ""),
                    "duration": elem.get("duration", {}).get("text", ""),
                })

    print(json.dumps({"results": rows}, indent=2))


# ── CLI Setup ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Google Maps CLI for PKA")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # search
    p_search = sub.add_parser("search", help="Search for places")
    p_search.add_argument("query", help="Search query (e.g., 'coffee shops in Palo Alto')")
    p_search.add_argument("--location", help="Center point as 'lat,lng'")
    p_search.add_argument("--radius", type=int, help="Search radius in meters (default: 5000)")
    p_search.add_argument("--limit", type=int, help="Max results (default: 10)")

    # directions
    p_dir = sub.add_parser("directions", help="Get directions")
    p_dir.add_argument("origin", help="Starting location")
    p_dir.add_argument("destination", help="Destination")
    p_dir.add_argument("--mode", choices=["driving", "walking", "bicycling", "transit"], help="Travel mode")
    p_dir.add_argument("--avoid", help="Avoid: tolls, highways, ferries")

    # geocode
    p_geo = sub.add_parser("geocode", help="Address to coordinates")
    p_geo.add_argument("address", help="Address to geocode")

    # reverse
    p_rev = sub.add_parser("reverse", help="Coordinates to address")
    p_rev.add_argument("latlng", help="Coordinates as 'lat,lng'")

    # nearby
    p_near = sub.add_parser("nearby", help="Find nearby places")
    p_near.add_argument("--location", required=True, help="Center point as 'lat,lng'")
    p_near.add_argument("--type", required=True, help="Place type: restaurant, cafe, gas_station, etc.")
    p_near.add_argument("--radius", type=int, help="Radius in meters (default: 2000)")
    p_near.add_argument("--keyword", help="Additional keyword filter")
    p_near.add_argument("--limit", type=int, help="Max results (default: 10)")

    # distance
    p_dist = sub.add_parser("distance", help="Distance between locations")
    p_dist.add_argument("origins", help="Origin(s) — address or lat,lng")
    p_dist.add_argument("destinations", help="Destination(s) — address or lat,lng")
    p_dist.add_argument("--mode", choices=["driving", "walking", "bicycling", "transit"], help="Travel mode")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "search": cmd_search,
        "directions": cmd_directions,
        "geocode": cmd_geocode,
        "reverse": cmd_reverse,
        "nearby": cmd_nearby,
        "distance": cmd_distance,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
