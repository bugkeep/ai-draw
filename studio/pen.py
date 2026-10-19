import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .actions import COLOR_PATTERN, Point
from .eraser import fill_color_visible


class PenPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    points: list[Point] = Field(min_length=2)
    smooth: bool = True
    closed: bool = False
    strokeWidth: float | None = Field(default=None, gt=0, le=32)
    strokeColor: str | None = None
    backgroundColor: str | None = None
    opacity: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def check_path(self):
        if not all(math.isfinite(value) and abs(value) <= 100000 for point in self.points for value in point):
            raise ValueError("Path coordinates must be finite and within the canvas limit")
        if self.strokeColor is not None and not COLOR_PATTERN.fullmatch(self.strokeColor):
            raise ValueError("Invalid path color")
        if self.backgroundColor is not None and not COLOR_PATTERN.fullmatch(self.backgroundColor):
            raise ValueError('Invalid path fill color')
        if self.closed and len(set(self.points)) < 3:
            raise ValueError("A closed path requires three distinct points")
        return self


class DrawPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[PenPath] = Field(min_length=1)
    origin: Point = (0, 0)
    scale: float = Field(default=1, gt=0, allow_inf_nan=False)
    strokeColor: str = "#242424"
    backgroundColor: str = 'transparent'
    strokeWidth: float = Field(default=0.4, gt=0, le=32)
    opacity: int = Field(default=100, ge=0, le=100)

    @model_validator(mode="after")
    def check_style(self):
        if not COLOR_PATTERN.fullmatch(self.strokeColor):
            raise ValueError("Invalid path color")
        if not COLOR_PATTERN.fullmatch(self.backgroundColor):
            raise ValueError('Invalid path fill color')
        if any(fill_color_visible(path.backgroundColor if path.backgroundColor is not None else self.backgroundColor)
               and not path.closed for path in self.paths):
            raise ValueError('A filled pen contour must be closed; use backgroundColor=transparent for open strokes')
        if not all(math.isfinite(value) and abs(value) <= 100000 for value in self.origin):
            raise ValueError("Invalid path origin")
        return self


class ReshapePath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(min_length=1)
    points: list[Point] = Field(min_length=2)
    smooth: bool = True
    closed: bool = False


def interpolate_path(points: list[Point], smooth: bool, closed: bool) -> list[Point]:
    knots = [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]
    if closed and knots[-1] == knots[0]:
        knots.pop()
    if len(knots) < 2:
        raise ValueError("A pen stroke requires two distinct points")
    if not smooth or len(knots) == 2:
        return knots + ([knots[0]] if closed else [])
    extended = [knots[-1] if closed else knots[0], *knots,
                knots[0] if closed else knots[-1], knots[1] if closed else knots[-1]]
    result = [knots[0]]
    for index in range(1, len(knots) + int(closed)):
        p0, p1, p2, p3 = extended[index - 1:index + 3]
        length = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
        steps = max(4, math.ceil(length / 8))
        for step in range(1, steps + 1):
            t = step / steps
            result.append(tuple(round(0.5 * (2 * p1[axis] + (-p0[axis] + p2[axis]) * t
                                + (2 * p0[axis] - 5 * p1[axis] + 4 * p2[axis] - p3[axis]) * t * t
                                + (-p0[axis] + 3 * p1[axis] - 3 * p2[axis] + p3[axis]) * t * t * t), 3)
                                for axis in range(2)))
    if closed:
        result[-1] = result[0]
    return result


def path_elements(arguments: dict) -> list[dict]:
    drawing = DrawPaths.model_validate(arguments)
    items = []
    for path in drawing.paths:
        points = [(drawing.origin[0] + point[0] * drawing.scale,
                   drawing.origin[1] + point[1] * drawing.scale) for point in path.points]
        if not all(math.isfinite(value) and abs(value) <= 100000 for point in points for value in point):
            raise ValueError("Transformed path coordinates exceed the canvas limit")
        items.append({"type": "freedraw", "name": path.name,
                      "points": interpolate_path(points, path.smooth, path.closed), "pointSpace": "absolute",
                      "strokeColor": path.strokeColor or drawing.strokeColor,
                      'backgroundColor': path.backgroundColor if path.backgroundColor is not None else drawing.backgroundColor,
                      "strokeWidth": path.strokeWidth if path.strokeWidth is not None else drawing.strokeWidth,
                      "opacity": path.opacity if path.opacity is not None else drawing.opacity})
    return items
