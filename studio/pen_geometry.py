"""Native freehand samples that survive Excalidraw restore/export smoothing."""

import math


def native_pen_samples(points):
    """Retain every anchor and its polyline; add gesture samples and settle its end.

    Excalidraw restores finished strokes without lastCommittedPoint. Its brush
    streamlines sparse points, so long final segments otherwise end short of the
    stored endpoint. Extra collinear samples keep corners close to their anchors;
    repeated endpoint samples settle the brush without changing the polyline.
    """
    result = [tuple(points[0])]
    for first, second in zip(points, points[1:]):
        steps = max(1, math.ceil(math.dist(first, second) / 2))
        for step in range(1, steps):
            result.append(tuple(first[axis] + (second[axis] - first[axis]) * step / steps for axis in (0, 1)))
        result.append(tuple(second))
    result.extend([tuple(points[-1])] * 6)
    return result
