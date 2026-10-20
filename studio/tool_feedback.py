DRAWING_GUIDANCE = {
    'image_normalized': {'axis_range': [0, 1000], 'midpoint': [500, 500], 'bottom_right': [1000, 1000],
        'coordinate_source': 'Use one current observation_id, or your own reference_view_id with its reference_layout_id.'},
    'requested_erasing': {'tool': 'erase_strokes', 'whole_objects': 'mode=stroke', 'partial_paths': 'mode=segment',
        'point_format': '[[x,y]] for one dab, or [[x1,y1],[x2,y2]] for a sweep',
        'targeting': 'Limit ids to intended objects and choose gesture points/radius that touch them. '
            'User-requested eraser operations use this tool.'},
}


def image_coordinate_feedback(name, arguments):
    if (name not in {'draw_paths', 'reshape_path', 'erase_strokes'}
            or arguments.get('coordinate_space') != 'image_normalized'
            or not (arguments.get('observation_id') or arguments.get('reference_view_id'))):
        return {}
    paths = arguments['paths'] if name == 'draw_paths' else [arguments]
    points = [point for path in paths for point in path['points']]
    x_bounds = [min(point[0] for point in points), max(point[0] for point in points)]
    y_bounds = [min(point[1] for point in points), max(point[1] for point in points)]
    bounds = {'coordinate_space': 'image_normalized', 'axis_range': [0, 1000],
        'x': x_bounds, 'y': y_bounds, 'point_count': len(points)}
    if 0 <= min(*x_bounds, *y_bounds) and max(*x_bounds, *y_bounds) <= 1:
        bounds['instruction'] = (
            'These supplied anchors occupy the first 0.1% of each image axis. '
            'This tool uses 0..1000 on EACH axis: the image midpoint is (500,500), '
            'and its bottom-right is (1000,1000). Retain this tiny placement if intended; '
            'otherwise correct the anchors in the intended image frame. '
            'The tool used the coordinates as supplied; inspect the actual result before further edits.')
    return {'input_coordinates': bounds}
