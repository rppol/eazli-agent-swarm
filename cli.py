"""Command-line access to the eazli tool service.

A third caller onto the same FastAPI endpoints, alongside the MCP shim and the
test suite. It exists for two reasons: it is what you demo on camera, and it
lets the agents work in a session where the MCP server has not been mounted
(`.mcp.json` is read at Claude Code startup).

Like the MCP shim, it holds no logic of its own. Every answer comes from the
service, so all three callers cannot disagree.

    uv run python cli.py access unit01 bedroom 218 95 84
    uv run python cli.py fit unit01 living_dining 185 88 80 --x 120 --y 300
    uv run python cli.py products "warm minimal sofa" --max-price 2000
    uv run python cli.py kb "what does the designer agent do"
    uv run python cli.py room unit01 living_dining
    uv run python cli.py quota explorer --layouts-used 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

API = os.environ.get("EAZLI_API", "http://127.0.0.1:8000")


def call(method: str, path: str, payload: dict | None = None) -> dict:
    try:
        with httpx.Client(base_url=API, timeout=30) as client:
            r = client.request(method, path, json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        sys.exit(
            f"Cannot reach the eazli tool service at {API}.\n"
            f"Start it with: uv run uvicorn app.main:app --port 8000"
        )
    except httpx.HTTPStatusError as exc:
        sys.exit(f"{exc.response.status_code}: {exc.response.text}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="eazli", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("access", help="can the item reach the room?")
    p.add_argument("unit"); p.add_argument("room")
    p.add_argument("w", type=float); p.add_argument("d", type=float); p.add_argument("h", type=float)
    p.add_argument("--carton", nargs=3, type=float, metavar=("W", "D", "H"))

    p = sub.add_parser("fit", help="does the item work in the room?")
    p.add_argument("unit"); p.add_argument("room")
    p.add_argument("w", type=float); p.add_argument("d", type=float); p.add_argument("h", type=float)
    p.add_argument("--x", type=float, default=0); p.add_argument("--y", type=float, default=0)
    p.add_argument("--confidence", default="stated")
    p.add_argument("--facing", default="S", choices=["N", "S", "E", "W"])
    p.add_argument(
        "--role",
        help="sofa, coffee_table, dining_table, floor_lamp, tv_console, bookshelf, "
             "wardrobe, bed, rug... Decides how much clear space the item needs in "
             "front. Without it the result is 'unverified' rather than a guess.",
    )

    p = sub.add_parser("products", help="search the amazon.sa catalogue")
    p.add_argument("query"); p.add_argument("--room"); p.add_argument("--max-price", type=float)
    p.add_argument("--max-width", type=float); p.add_argument("-k", type=int, default=6)

    p = sub.add_parser("kb", help="search what eazli says about itself")
    p.add_argument("query"); p.add_argument("--doc-type"); p.add_argument("-k", type=int, default=4)

    p = sub.add_parser("room", help="surveyed dimensions for one room")
    p.add_argument("unit"); p.add_argument("room")

    sub.add_parser("units", help="list surveyed units")

    p = sub.add_parser("quota", help="check Explorer / Active limits")
    p.add_argument("plan", choices=["explorer", "active"])
    p.add_argument("--rooms-used", type=int, default=0)
    p.add_argument("--layouts-used", type=int, default=0)
    p.add_argument("--redesigns-used", type=int, default=0)

    p = sub.add_parser("layout", help="validate a whole layout (JSON placements)")
    p.add_argument("unit"); p.add_argument("room"); p.add_argument("placements")

    args = parser.parse_args()

    if args.cmd == "access":
        payload = {"unit": args.unit, "room": args.room,
                   "item": {"id": "item", "w": args.w, "d": args.d, "h": args.h}}
        if args.carton:
            payload["carton"] = {"id": "carton", "w": args.carton[0],
                                 "d": args.carton[1], "h": args.carton[2]}
        out = call("POST", "/access/check", payload)

    elif args.cmd == "fit":
        out = call("POST", "/fit/check", {
            "unit": args.unit, "room": args.room, "x": args.x, "y": args.y,
            "facing": args.facing, "role": args.role,
            "item": {"id": args.role or "item", "w": args.w, "d": args.d, "h": args.h,
                     "confidence": args.confidence},
        })

    elif args.cmd == "products":
        out = call("POST", "/products/search", {
            "query": args.query, "k": args.k, "room": args.room,
            "max_price_sar": args.max_price, "max_width_cm": args.max_width,
        })

    elif args.cmd == "kb":
        out = call("POST", "/kb/search", {
            "query": args.query, "k": args.k, "doc_type": args.doc_type,
        })

    elif args.cmd == "room":
        out = call("GET", f"/home/{args.unit}/room/{args.room}")

    elif args.cmd == "units":
        out = call("GET", "/home/units")

    elif args.cmd == "quota":
        out = call("POST", "/plan/quota", {
            "plan": args.plan, "rooms_used": args.rooms_used,
            "layouts_used": args.layouts_used, "redesigns_used": args.redesigns_used,
        })

    elif args.cmd == "layout":
        out = call("POST", "/layout/validate", {
            "unit": args.unit, "room": args.room,
            "placements": json.loads(args.placements),
        })

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
