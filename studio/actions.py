import math
import hashlib
import json
import re
import secrets
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Number = float
Point = tuple[Number, Number]


class CreateElement(BaseModel):
    type: Literal["rectangle", "ellipse", "diamond", "text", "line", "arrow", "freedraw"]
    x: float = 0
    y: float = 0
    width: float = 120
    height: float = 90
    points: list[Point] | None = None
    pointSpace: Literal["absolute", "relative"] | None = None
    text: str | None = None
    name: str = Field(default="", max_length=100)
    strokeColor: str = Field(default="#1e293b", max_length=32)
    backgroundColor: str = Field(default="transparent", max_length=32)
    strokeWidth: float = Field(default=2, gt=0, le=32)
    fontSize: float = Field(default=24, gt=0, le=200)
    roughness: int = Field(default=0, ge=0, le=2)
    opacity: int = Field(default=100, ge=0, le=100)

    @model_validator(mode="after")
    def check_geometry(self):
        values = [self.x, self.y, self.width, self.height, self.strokeWidth, self.fontSize]
        if self.points:
            values.extend(value for point in self.points for value in point)
        if not all(math.isfinite(value) and abs(value) <= 100000 for value in values):
            raise ValueError("Coordinates must be finite and within the canvas limit")
        if self.type in {"freedraw", "line", "arrow"} and (not self.points or len(self.points) < 2):
            raise ValueError("Line and pen elements require at least two points")
        if self.type not in {"freedraw", "line", "arrow"} and self.pointSpace is not None:
            raise ValueError("Point coordinates are only supported for line and pen elements")
        if self.points and len(self.points) > 500:
            raise ValueError("A single element supports at most 500 points")
        if self.type == "text" and not self.text:
            raise ValueError("Text elements require text")
        if self.text and len(self.text) > 3000:
            raise ValueError("Text is too long")
        if not COLOR_PATTERN.fullmatch(self.strokeColor) or not COLOR_PATTERN.fullmatch(self.backgroundColor):
            raise ValueError("Invalid element color")
        if self.type not in {"freedraw", "line", "arrow", "text"} and (self.width <= 0 or self.height <= 0):
            raise ValueError("Shape width and height must be positive")
        return self


class SelectElements(BaseModel):
    ids: list[str] = Field(max_length=100)


class MoveElements(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    dx: float | None = None
    dy: float | None = None
    x: float | None = None
    y: float | None = None

    @model_validator(mode="after")
    def check_coordinates(self):
        relative = self.dx is not None and self.dy is not None
        absolute = self.x is not None and self.y is not None and len(self.ids) == 1
        if relative == absolute:
            raise ValueError("Provide dx and dy, or x and y for one element")
        return self


class UpdateElements(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    patch: dict[str, Any]


class DeleteElements(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


ACTION_MODELS = {
    "create_element": CreateElement,
    "select_elements": SelectElements,
    "move_elements": MoveElements,
    "update_elements": UpdateElements,
    "delete_elements": DeleteElements,
}
PATCH_FIELDS = {
    "x", "y", "width", "height", "angle", "strokeColor", "backgroundColor",
    "strokeWidth", "opacity", "roughness", "text", "fontSize", "name",
}
FINGERPRINT_IGNORED_FIELDS = {"version", "versionNonce", "updated", "index", "seed"}
COLOR_PATTERN = re.compile(r"(?:#[0-9a-fA-F]{3,8}|transparent)\Z")


def element_fingerprint(element: dict[str, Any]) -> str:
    content = {key: value for key, value in element.items() if key not in FINGERPRINT_IGNORED_FIELDS}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_action(action: dict[str, Any]) -> dict[str, Any]:
    kind = action.get("kind")
    if kind not in ACTION_MODELS:
        raise ValueError(f"Unsupported drawing action: {kind}")
    args = ACTION_MODELS[kind].model_validate(action.get("args", {})).model_dump(exclude_none=True)
    if kind == "update_elements":
        if not args["patch"] or set(args["patch"]) - PATCH_FIELDS:
            raise ValueError("Unsupported update fields")
        for field in ("x", "y", "width", "height", "angle", "strokeWidth", "fontSize"):
            if field in args["patch"]:
                value = args["patch"][field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 100000:
                    raise ValueError(f"Invalid {field}")
        for field in ("width", "height", "strokeWidth", "fontSize"):
            if field in args["patch"] and args["patch"][field] <= 0:
                raise ValueError(f"{field} must be positive")
        if args["patch"].get("strokeWidth", 0) > 32 or args["patch"].get("fontSize", 0) > 200:
            raise ValueError("Stroke or font size is too large")
        if "opacity" in args["patch"]:
            value = args["patch"]["opacity"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Invalid opacity")
        if "roughness" in args["patch"]:
            value = args["patch"]["roughness"]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2:
                raise ValueError("Invalid roughness")
        for field in ("strokeColor", "backgroundColor"):
            if field in args["patch"] and (not isinstance(args["patch"][field], str) or
                                            not COLOR_PATTERN.fullmatch(args["patch"][field])):
                raise ValueError(f"Invalid {field}")
        for field, maximum in (("text", 3000), ("name", 100)):
            if field in args["patch"] and (not isinstance(args["patch"][field], str) or
                                            len(args["patch"][field]) > maximum):
                raise ValueError(f"Invalid {field}")
    if kind == "move_elements" and not all(math.isfinite(value) for key, value in args.items() if key in {"dx", "dy", "x", "y"}):
        raise ValueError("Move distance must be finite")
    result = {"kind": kind, "args": args}
    if kind == "create_element" and "element_id" in action:
        element_id = action["element_id"]
        if not isinstance(element_id, str) or not element_id.isalnum() or len(element_id) > 64:
            raise ValueError("Invalid element ID")
        result["element_id"] = element_id
    return result


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def create_native_element(spec: dict[str, Any], element_id: str | None = None) -> dict[str, Any]:
    data = CreateElement.model_validate(spec)
    now = int(time.time() * 1000)
    element: dict[str, Any] = {
        "id": element_id or _new_id(),
        "type": data.type,
        "x": data.x,
        "y": data.y,
        "width": data.width,
        "height": data.height,
        "angle": 0,
        "strokeColor": data.strokeColor,
        "backgroundColor": data.backgroundColor,
        "fillStyle": "solid",
        "strokeWidth": data.strokeWidth,
        "strokeStyle": "solid",
        "roughness": data.roughness,
        "opacity": data.opacity,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": secrets.randbelow(2**31),
        "version": 1,
        "versionNonce": secrets.randbelow(2**31),
        "isDeleted": False,
        "boundElements": None,
        "updated": now,
        "link": None,
        "locked": False,
        "customData": {"name": data.name, "agentCreated": True},
    }
    if data.type in {"line", "arrow", "freedraw"}:
        assert data.points is not None
        relative = data.pointSpace == "relative" or (data.pointSpace is None and (data.x != 0 or data.y != 0))
        absolute_points = [(data.x + x, data.y + y) if relative else (x, y) for x, y in data.points]
        if any(abs(value) > 100000 for point in absolute_points for value in point):
            raise ValueError("Path coordinates exceed the canvas limit")
        min_x = min(point[0] for point in absolute_points)
        min_y = min(point[1] for point in absolute_points)
        points = [[x - min_x, y - min_y] for x, y in absolute_points]
        element.update({
            "x": min_x,
            "y": min_y,
            "width": max(point[0] for point in absolute_points) - min_x,
            "height": max(point[1] for point in absolute_points) - min_y,
            "points": points,
        })
        if data.type == "freedraw":
            element.update({
                "pressures": [0.5] * len(points),
                "simulatePressure": True,
                "lastCommittedPoint": None,
            })
        else:
            element.update({"startBinding": None, "endBinding": None, "lastCommittedPoint": None})
            if data.type == "arrow":
                element.update({"startArrowhead": None, "endArrowhead": "arrow"})
    if data.type == "text":
        text = data.text or ""
        element.update({
            "width": max(12, min(1600, len(max(text.splitlines(), key=len)) * data.fontSize * 0.62)),
            "height": max(data.fontSize * 1.25, len(text.splitlines()) * data.fontSize * 1.25),
            "fontSize": data.fontSize,
            "fontFamily": 1,
            "text": text,
            "originalText": text,
            "textAlign": "left",
            "verticalAlign": "top",
            "containerId": None,
            "autoResize": True,
            "lineHeight": 1.25,
        })
    return element


def apply_action(elements: list[dict[str, Any]], selection: list[str], action: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    action = validate_action(action)
    kind, args = action["kind"], action["args"]
    scene = [dict(element) for element in elements]
    selected = list(selection)
    changed: list[str] = []
    if kind == "create_element":
        element = create_native_element(args, action.get("element_id"))
        if any(existing["id"] == element["id"] for existing in scene):
            raise ValueError("Element ID already exists")
        scene.append(element)
        selected = [element["id"]]
        changed = selected
    else:
        ids = args["ids"]
        by_id = {element["id"]: element for element in scene if not element.get("isDeleted")}
        missing = set(ids) - set(by_id)
        if missing:
            raise ValueError(f"Elements not found: {', '.join(sorted(missing))}")
        if kind == "select_elements":
            selected = ids
        else:
            for element_id in ids:
                element = by_id[element_id]
                if kind == "move_elements":
                    if "dx" in args:
                        element["x"] += args["dx"]
                        element["y"] += args["dy"]
                    else:
                        element["x"] = args["x"]
                        element["y"] = args["y"]
                elif kind == "update_elements":
                    patch = dict(args["patch"])
                    if "name" in patch:
                        element["customData"] = {**element.get("customData", {}), "name": patch.pop("name")}
                    if "text" in patch:
                        element["originalText"] = patch["text"]
                    element.update(patch)
                elif kind == "delete_elements":
                    element["isDeleted"] = True
                element["version"] = int(element.get("version", 1)) + 1
                element["versionNonce"] = secrets.randbelow(2**31)
                element["updated"] = int(time.time() * 1000)
                changed.append(element_id)
            if kind == "delete_elements":
                selected = [element_id for element_id in selected if element_id not in ids]
            else:
                selected = ids
    return scene, selected, changed
