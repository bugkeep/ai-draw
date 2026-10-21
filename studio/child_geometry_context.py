from shapely import LineString, Point, STRtree, get_parts
from shapely.ops import nearest_points

from .coordinates import map_observed_arguments
from .eraser import EraseStrokes, element_geometry, filled_path


def no_effect_erase_context(elements, arguments, result, metadata=None):
    if result.get('ok') is not True or result.get('action_count') != 0:
        raise ValueError('Eraser targeting observations require an actual successful no-change receipt')
    if arguments.get('observation_id'):
        if metadata is None:
            raise ValueError('Image eraser targeting requires the original observation metadata')
        arguments = map_observed_arguments('erase_strokes', arguments, metadata)
    spec = EraseStrokes.model_validate(arguments)
    trail = LineString(spec.points) if len(set(spec.points)) > 1 else Point(spec.points[0])
    brush = trail.buffer(spec.radius)
    live = [item for item in elements if not item.get('isDeleted')]
    identities = {item['id']: index for index, item in enumerate(live)}
    if set(spec.ids) - identities.keys():
        raise ValueError('Eraser targeting observations require current live selected IDs')
    geometry, eligible = {}, []
    for index, element in enumerate(live):
        allowed = (not element.get('locked') and (spec.mode == 'stroke'
            or element['type'] in {'freedraw', 'line'} and not filled_path(element)))
        if allowed or element['id'] in spec.ids:
            geometry[index] = element_geometry(element)
            if allowed and not geometry[index].is_empty:
                eligible.append(index)
    eligible_indices = set(eligible)

    def describe(index):
        element = live[index]
        shape = geometry[index]
        hit = nearest_points(shape, trail)[0]
        nearby, dense = [], 0
        if shape.geom_type == 'LineString':
            for part in get_parts(shape.intersection(brush)):
                if part.geom_type != 'LineString' or part.length == 0:
                    continue
                points = list(part.simplify(1).coords)
                if 2 <= len(points) <= 8:
                    nearby.append({'points': points, 'max_centerline_error': 1})
                else:
                    dense += 1
        return {'id': element['id'], 'name': element.get('customData', {}).get('name', ''),
            'type': element['type'], 'native_array_position': index,
            'geometry_kind': shape.geom_type, 'nearest_native_point': [hit.x, hit.y],
            'nearby_path_parts': nearby, 'dense_path_parts_omitted': dense,
            'distance_to_attempted_gesture': shape.distance(trail),
            'strokeWidth': element.get('strokeWidth'), 'opacity': element.get('opacity', 100),
            'backgroundColor': element.get('backgroundColor'), 'locked': element.get('locked', False),
            'eligible_for_requested_mode': index in eligible_indices}

    tree = STRtree([geometry[index] for index in eligible])
    nearest = [eligible[int(index)] for index in tree.query_nearest(trail, all_matches=True)] if eligible else []
    return {'coordinate_space': 'canvas', 'mode': spec.mode, 'radius_canvas': spec.radius,
        'attempted_gesture_bounds': list(trail.bounds), 'requested_ids': spec.ids,
        'eligible_object_count': len(eligible),
        'selected_geometry': [describe(identities[key]) for key in spec.ids],
        'nearest_geometry': [describe(index) for index in sorted(nearest, reverse=True)],
        'instruction': 'The recorded erase call changed ZERO native objects. The rows describe the CURRENT '
            'native geometry and exact nearest canvas points to that attempted gesture, including every '
            'equally nearest object. Inspect the actual mark, names, fills and layer order to identify its '
            'source within the assignment. Use a live target ID and a relevant current point for a native '
            'gesture; omit observation_id/coordinate_space for these canvas coordinates. Reuse a correct '
            'existing contour. Keep the gesture inside the requested region; a selected object\'s nearest '
            'point can lie outside that region. Use the actual mark to choose its source. Preserve '
            'unrequested objects. Segment mode supports unfilled pen/line '
            'objects; stroke mode removes a whole target. Fills and visible_only affect what can be erased. '
            'nearby_path_parts are current centerline pieces inside the attempted brush region. Use their '
            'canvas points for a local sweep of the unwanted fragment. Retrieve a dense omitted path through '
            'recall_memory when its full geometry is needed. '
            'These geometric rows locate objects; the current picture establishes visibility and which '
            'part is unwanted. Repeating an unchanged gesture at the same selected empty location cannot '
            'repair the mark. Full original arguments and receipt remain available through memory_ref.'}
