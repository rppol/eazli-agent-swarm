"""Loads the surveyed floor plan and composes per-room delivery routes.

The floor plan is the single source of truth about the space. It carries its
own provenance: dimensions printed on the drawing are `stated`, anything we
had to assume (ceiling height, door leaf widths, lift car height) is marked
`assumed` so the auditor can say so out loud rather than quietly trusting it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.geometry import Door, PathSegment, Room


@dataclass
class RoomSpec:
    name: str
    imperial: str
    width_cm: float
    depth_cm: float
    height_cm: float
    doors: list[Door] = field(default_factory=list)

    def to_room(self, doors: list[Door] | None = None) -> Room:
        """Convert to the geometry engine's Room.

        Rooms used to be built with `doors=[]` unconditionally, so the door
        swing rule iterated an empty list and never executed once — while the
        API advertised it as enforced. Advertised-but-dead is worse than
        absent, because it reports `pass`.
        """
        return Room(
            name=self.name,
            width_cm=self.width_cm,
            depth_cm=self.depth_cm,
            height_cm=self.height_cm,
            doors=doors if doors is not None else self.doors,
        )


@dataclass
class Unit:
    id: str
    label: str
    config: str
    rooms: list[RoomSpec]

    def room(self, name: str) -> RoomSpec:
        for r in self.rooms:
            if r.name == name:
                return r
        raise KeyError(f"{self.id} has no room {name!r}. Known: {[r.name for r in self.rooms]}")


@dataclass
class Home:
    raw: dict
    units: list[Unit]

    def unit(self, unit_id: str) -> Unit:
        for u in self.units:
            if u.id == unit_id:
                return u
        raise KeyError(f"unknown unit {unit_id!r}. Known: {[u.id for u in self.units]}")

    def route_to(self, unit_id: str, room_name: str) -> list[PathSegment]:
        """The delivery route from the lift to a specific room.

        Every route crosses the shared leg (lift, lobby, corridor turn, flat
        entrance). Rooms that open straight off the living area stop there;
        anything behind a door picks up the narrow internal passage and a
        75cm room door as well.
        """
        self.unit(unit_id).room(room_name)  # raises if the room is unknown

        route_cfg = self.raw["delivery_route"]
        segments = [_segment(s) for s in route_cfg["to_flat_door"]]

        open_plan = room_name in route_cfg.get("open_plan_rooms", [])
        key = "open_plan" if open_plan else "behind_a_door"
        segments += [_segment(s) for s in route_cfg["within_flat"][key]]
        return segments

    @property
    def assumptions(self) -> list[str]:
        """Everything the plan did not actually dimension. Surfaced in reports."""
        d = self.raw["defaults"]
        return [
            f"Ceiling height assumed {d['ceiling_height_cm']}cm — not dimensioned on the plan.",
            f"Main door leaf assumed {d['main_door_width_cm']}cm, internal doors "
            f"{d['internal_door_width_cm']}cm — not dimensioned on the plan.",
            "Lift car heights assumed 220cm — the plan gives car floor area only.",
            "Door POSITIONS are a convention, not a survey: one inward-swinging "
            "door per room on the north wall, 30cm from the corner. The plan "
            "does not locate doors. Any door-swing verdict inherits this.",
        ]


def _default_door(room_name: str, width_cm: float, defaults: dict) -> Door:
    """Every room has a door; the floor plan just does not dimension one.

    Rather than leave the swing rule dead, we place one door per room using the
    assumed leaf widths already declared in `defaults`, offset a little from the
    corner. The position is a convention, not a survey — `Home.assumptions`
    says so, and any verdict that turns on it must repeat that.
    """
    leaf = (
        defaults["main_door_width_cm"]
        if room_name == "living_dining"
        else defaults["internal_door_width_cm"]
    )
    offset = min(30.0, max(0.0, width_cm - leaf))
    return Door(wall="N", offset_cm=offset, width_cm=leaf, swing="in")


def _segment(spec: dict) -> PathSegment:
    return PathSegment(
        name=spec["name"],
        kind=spec["kind"],
        width_cm=spec["width_cm"],
        height_cm=spec["height_cm"],
        depth_cm=spec.get("depth_cm"),
        turn_into_cm=spec.get("turn_into_cm"),
    )


@lru_cache(maxsize=4)
def load_home(path: str = "data/home.json") -> Home:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    units = [
        Unit(
            id=u["id"],
            label=u["label"],
            config=u["config"],
            rooms=[
                RoomSpec(
                    name=r["name"],
                    imperial=r["imperial"],
                    width_cm=r["width_cm"],
                    depth_cm=r["depth_cm"],
                    height_cm=r["height_cm"],
                    doors=[_default_door(r["name"], r["width_cm"], raw["defaults"])],
                )
                for r in u["rooms"]
            ],
        )
        for u in raw["units"]
    ]
    return Home(raw=raw, units=units)
