from shapely import LineString


def recall_path_geometry(element, tolerance=0):
    """Select existing samples for a bounded-error read, without editing the native path."""
    points = element.get('points', [])
    pressures = element.get('pressures', [])
    indices = list(range(len(points)))
    if tolerance == 0:
        return points, pressures, indices
    if tolerance > 0 and len(points) > 2:
        reduced = list(LineString(points).simplify(tolerance, preserve_topology=True).coords)
        cursor, indices = 0, []
        for position, point in enumerate(reduced):
            if position == len(reduced) - 1:
                cursor = len(points) - 1
            else:
                while cursor < len(points) and tuple(points[cursor]) != point:
                    cursor += 1
            if cursor >= len(points):
                raise ValueError('Path simplification did not preserve the source point mapping')
            indices.append(cursor)
            cursor += 1
        if pressures and len(pressures) != len(points):
            raise ValueError('Cannot simplify pressure data that does not match the original path points')
    return ([points[index] for index in indices],
            [pressures[index] for index in indices] if pressures else [], indices)
