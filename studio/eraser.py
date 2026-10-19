import copy
import math
import secrets
import time
import uuid
from bisect import bisect_right
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely import GeometryCollection, LineString, Point, Polygon, box, make_valid
from shapely.affinity import rotate, scale


class EraseStrokes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[tuple[float, float]] = Field(min_length=1)
    radius: float = Field(default=8, gt=0, le=10000, allow_inf_nan=False)
    mode: Literal["segment", "stroke"] = "segment"
    ids: list[str] = Field(default_factory=list)
    visible_only: bool = False

    @model_validator(mode="after")
    def check_coordinates(self):
        if not all(math.isfinite(value) and abs(value) <= 100000 for point in self.points for value in point):
            raise ValueError("Eraser coordinates must be finite and within the canvas")
        return self


def fill_color_visible(color) -> bool:
    if not isinstance(color, str) or not color or color == 'transparent':
        return False
    if color.startswith('#') and (len(color) == 5 and color[-1] == '0' or len(color) == 9 and color[-2:] == '00'):
        return False
    return True


def filled_path(element: dict) -> bool:
    if element.get('type') not in {'freedraw', 'line'} or not fill_color_visible(element.get('backgroundColor')):
        return False
    points = element.get('points') or []
    # Excalidraw 0.18.1 uses LINE_CONFIRM_THRESHOLD=8 at zoom 1 for a closed path.
    return len({tuple(point) for point in points}) >= 3 and math.dist(points[0], points[-1]) <= 8


def opaque_fill(element: dict) -> bool:
    color = element.get('backgroundColor')
    if (element.get('opacity', 100) != 100 or element.get('fillStyle', 'solid') != 'solid'
            or not fill_color_visible(color) or not color.startswith('#')):
        return False
    if len(color) == 5 and color[-1].lower() != 'f' or len(color) == 9 and color[-2:].lower() != 'ff':
        return False
    return element.get('type') in {'rectangle', 'ellipse', 'diamond'} or filled_path(element)


def element_geometry(element: dict):
    x, y = element["x"], element["y"]
    width, height = element["width"], element["height"]
    center = (x + width / 2, y + height / 2)
    kind = element["type"]
    if kind in {"freedraw", "line", "arrow"}:
        points = [(x + point[0], y + point[1]) for point in element["points"]]
        geometry = (make_valid(Polygon(points)) if filled_path(element) else
                    LineString(points) if len(set(points)) > 1 else Point(points[0]))
    elif kind == "ellipse":
        geometry = scale(Point(center).buffer(1, quad_segs=32), width / 2, height / 2, origin=center)
    elif kind == "diamond":
        geometry = Polygon([(center[0], y), (x + width, center[1]),
                            (center[0], y + height), (x, center[1])])
    else:
        geometry = box(x, y, x + width, y + height)
    if kind in {"rectangle", "ellipse", "diamond"} and not fill_color_visible(element.get('backgroundColor')):
        geometry = geometry.boundary
    return rotate(geometry, element.get("angle", 0), origin=center, use_radians=True)


def _revision(element: dict) -> dict:
    return {**element, "version": int(element.get("version", 1)) + 1,
            "versionNonce": secrets.randbelow(2**31), "updated": int(time.time() * 1000)}


def _fragment(element: dict, line: LineString, part: LineString, fragment_id: str) -> dict:
    points = list(part.coords)
    min_x, min_y, max_x, max_y = part.bounds
    distances = [0.0]
    original_points = list(line.coords)
    for first, second in zip(original_points, original_points[1:]):
        distances.append(distances[-1] + math.dist(first, second))
    original_pressure = element.get("pressures") or [0.5] * len(original_points)
    if len(original_pressure) != len(original_points):
        raise ValueError("The original stroke pressure data does not match its points")
    pressures = []
    for point in points:
        distance = line.project(Point(point))
        index = max(0, min(len(distances) - 2, bisect_right(distances, distance) - 1))
        span = distances[index + 1] - distances[index]
        fraction = (distance - distances[index]) / span if span else 0
        pressures.append(original_pressure[index] + fraction * (original_pressure[index + 1] - original_pressure[index]))
    fragment = _revision(copy.deepcopy(element))
    fragment.update(id=fragment_id, x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y,
                    angle=0, points=[[px - min_x, py - min_y] for px, py in points],
                    lastCommittedPoint=None)
    if element["type"] == "freedraw":
        fragment["pressures"] = pressures
    if fragment_id != element["id"]:
        fragment["customData"] = {**element.get("customData", {}), "sourceElementId": element["id"]}
        fragment["boundElements"] = []
    return fragment


def erase_elements(elements: list[dict], selection: list[str], arguments: dict,
                   operation_id: str) -> tuple[list[dict], list[str], list[str]]:
    spec = EraseStrokes.model_validate(arguments)
    live = {item["id"]: item for item in elements if not item.get("isDeleted")}
    if set(spec.ids) - set(live):
        raise ValueError("An eraser target no longer exists")
    candidates = [live[element_id] for element_id in spec.ids] if spec.ids else list(live.values())
    if spec.mode == "segment" and spec.ids and any(
        item["type"] not in {"freedraw", "line"} or filled_path(item) for item in candidates
    ):
        raise ValueError("Segment erasing supports unfilled pen and line objects; use mode=stroke for filled shapes or arrows")
    trail = LineString(spec.points) if len(set(spec.points)) > 1 else Point(spec.points[0])
    brush = trail.buffer(spec.radius, quad_segs=16)
    covered = {}
    if (not spec.ids or spec.visible_only) and any(opaque_fill(element) for element in live.values()):
        occluders = GeometryCollection()
        for element in reversed(list(live.values())):
            covered[element['id']] = occluders
            if opaque_fill(element):
                geometry = element_geometry(element)
                if geometry.area:
                    occluders = occluders.union(geometry)
    replacements, changed, removed = {}, [], set()
    for element in candidates:
        if element.get("locked") or spec.mode == "segment" and (
            element["type"] not in {"freedraw", "line"} or filled_path(element)
        ):
            continue
        geometry = element_geometry(element)
        half_width = element.get("strokeWidth", 1) * (4.25 if element["type"] == "freedraw" else 1) / 2
        cut = brush.buffer(half_width)
        if element['id'] in covered and not covered[element['id']].is_empty:
            cut = cut.difference(covered[element['id']])
        if not geometry.intersects(cut):
            continue
        if spec.mode == "stroke" or geometry.geom_type != "LineString":
            replacements[element["id"]] = [{**_revision(element), "isDeleted": True}]
            removed.add(element["id"])
        else:
            remaining = geometry.difference(cut)
            parts = ([remaining] if remaining.geom_type == "LineString" else list(remaining.geoms)
                     if remaining.geom_type in {"MultiLineString", "GeometryCollection"} else [])
            parts = [part for part in parts if part.geom_type == "LineString" and part.length > 1e-7]
            parts.sort(key=lambda part: geometry.project(part.interpolate(0.5, normalized=True)))
            fragments = []
            for index, part in enumerate(parts):
                if geometry.project(Point(part.coords[0])) > geometry.project(Point(part.coords[-1])):
                    part = LineString(list(part.coords)[::-1])
                fragment_id = element["id"] if index == 0 else uuid.uuid5(
                    uuid.NAMESPACE_URL, f"erase/{operation_id}/{element['id']}/{index}").hex[:16]
                fragments.append(_fragment(element, geometry, part, fragment_id))
            replacements[element["id"]] = fragments or [{**_revision(element), "isDeleted": True}]
            if not fragments:
                removed.add(element["id"])
        changed.extend(item["id"] for item in replacements[element["id"]])
    if spec.mode == "stroke":
        for element in elements:
            if element.get("containerId") in removed or element.get("frameId") in removed:
                replacements[element["id"]] = [{**_revision(element), "isDeleted": True}]
                removed.add(element["id"])
                changed.append(element["id"])
    scene = []
    for element in elements:
        for item in replacements.get(element["id"], [element]):
            if removed and not item.get("isDeleted"):
                patch = {}
                for binding in ("startBinding", "endBinding"):
                    if item.get(binding) and item[binding].get("elementId") in removed:
                        patch[binding] = None
                bound = item.get("boundElements")
                if bound and any(reference["id"] in removed for reference in bound):
                    patch["boundElements"] = [reference for reference in bound if reference["id"] not in removed]
                if patch:
                    item = {**_revision(item), **patch}
                    changed.append(item["id"])
            scene.append(item)
    selected = [fragment["id"] for element_id in selection
                for fragment in replacements.get(element_id, [live[element_id]] if element_id in live else [])
                if not fragment.get("isDeleted")]
    return scene, selected, list(dict.fromkeys(changed))
