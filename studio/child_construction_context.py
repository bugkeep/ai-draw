import math


def vertical_axis(composition, subject_id):
    forms = {item['id'] for item in composition['forms'] if item['subject_id'] == subject_id}
    landmarks = [item for item in composition['landmarks'] if item['form_id'] in forms]
    candidates = [(top, base) for top in landmarks if top['role'] == 'apex'
        for base in landmarks if base['role'] == 'contact' and base['form_id'] == top['form_id']
        and math.isclose(top['x'], base['x'], abs_tol=1e-6) and top['y'] < base['y']]
    return candidates[0] if len(candidates) == 1 else None


def contains(outer, inner):
    return (outer['width'] * outer['height'] > inner['width'] * inner['height']
        and outer['x'] <= inner['x'] and outer['y'] <= inner['y']
        and outer['x'] + outer['width'] >= inner['x'] + inner['width']
        and outer['y'] + outer['height'] >= inner['y'] + inner['height'])


def planar_references(composition, scope, axes):
    perspective = composition.get('perspective')
    if not perspective or perspective['projection'] != 'two_point' or perspective['horizon_y'] is None:
        return []
    points = perspective['vanishing_points']
    if len(points) != 2 or any(not math.isclose(point[1], perspective['horizon_y'], abs_tol=1e-6) for point in points):
        return []
    forms = composition['forms']
    result = []
    for form in forms:
        axis = axes.get(form['subject_id'])
        if form['id'] not in scope or not axis or form['form'] not in {'box', 'plane'} or form['axis_degrees'] != 0:
            continue
        parents = [item for item in forms if item['subject_id'] == form['subject_id']
            and item['form'] in {'box', 'plane'} and item['axis_degrees'] == 0
            and contains(item['bounds'], form['bounds'])
            and (not form['parent_id'] or item['id'] == form['parent_id'])]
        if not parents:
            continue
        area = min(item['bounds']['width'] * item['bounds']['height'] for item in parents)
        parents = [item for item in parents if item['bounds']['width'] * item['bounds']['height'] == area]
        if len(parents) != 1:
            continue
        top, base = axis
        bounds = form['bounds']
        inset = min(bounds['width'], bounds['height']) * 0.01
        left, right = bounds['x'] + inset, bounds['x'] + bounds['width'] - inset
        if right <= top['x']:
            near, far = right, left
            choices = [(index, point) for index, point in enumerate(points) if point[0] < far]
        elif left >= top['x']:
            near, far = left, right
            choices = [(index, point) for index, point in enumerate(points) if point[0] > far]
        else:
            continue
        if len(choices) != 1:
            continue
        point_index, (vx, vy) = choices[0]
        scale = (far - vx) / (near - vx)
        plane_scale = (near - vx) / (top['x'] - vx)
        far_plane_scale = (far - vx) / (top['x'] - vx)
        ceiling = bounds['y'] + inset
        upper = max(ceiling, vy + (ceiling - vy) / scale,
                    vy + (top['y'] - vy) * plane_scale + inset,
                    vy + ((top['y'] - vy) * far_plane_scale + inset) / scale)
        bottom = bounds['y'] + bounds['height'] - inset
        lower = min(bottom, vy + (bottom - vy) / scale,
                    vy + (base['y'] - vy) * plane_scale - inset,
                    vy + ((base['y'] - vy) * far_plane_scale - inset) / scale)
        result.append({'form_id': form['id'], 'parent_form_id': parents[0]['id'],
            'axis_landmarks': [top['id'], base['id']], 'vanishing_point_index': point_index,
            'near_edge_x': near, 'far_edge_x': far, 'near_y_interval': [upper, lower],
            'reference_inset': inset,
            'status': 'coplanar_reference' if upper < lower else 'no_coplanar_interval',
            'path': {'name': form['id'], 'smooth': False, 'closed': True,
                'points': [[near, upper], [far, vy + (upper - vy) * scale],
                    [far, vy + (lower - vy) * scale], [near, lower]]} if upper < lower else None})
    return result


def construction_references(spec, scope):
    composition = spec['composition']
    stage = next(item for item in composition['stages'] if item['id'] == spec['stage_id'])
    if spec['intent'] in {'edit', 'research'} or stage['phase'] not in {'block_in', 'contour'}:
        return None
    architecture = {item['id'] for item in composition['subjects'] if item['category'] == 'architecture'}
    axes = {key: axis for key in architecture if (axis := vertical_axis(composition, key)) is not None}
    landmarks = {item['id']: item for item in composition['landmarks']}
    connections = []
    for relation in composition['relations']:
        if relation['kind'] not in {'attached_to', 'supported_by', 'continuous_with'}:
            continue
        source, target = landmarks[relation['source']], landmarks[relation['target']]
        if not {source['form_id'], target['form_id']} & set(scope):
            continue
        connections.append({'relation_id': relation['id'], 'kind': relation['kind'],
            'landmark_ids': [source['id'], target['id']], 'form_ids': [source['form_id'], target['form_id']],
            'canvas_points': [[source['x'], source['y']], [target['x'], target['y']]],
            'max_gap': relation['max_gap']})
    crossings = []
    for form in composition['forms']:
        axis = axes.get(form['subject_id'])
        if form['id'] not in scope or not axis or form['id'] == axis[0]['form_id']:
            continue
        top, base = axis
        bounds = form['bounds']
        low, high = max(top['y'], bounds['y']), min(base['y'], bounds['y'] + bounds['height'])
        if bounds['x'] <= top['x'] <= bounds['x'] + bounds['width'] and low < high:
            crossings.append({'form_id': form['id'], 'axis_landmarks': [top['id'], base['id']],
                'canvas_segment': [[top['x'], low], [top['x'], high]]})
    planes = planar_references(composition, set(scope), axes)
    if not (connections or crossings or planes):
        return None
    return {'coordinate_space': 'canvas', 'coplanar_forms': planes, 'planned_connections': connections,
        'potential_axis_crossings': crossings,
        'instruction': 'These calculated references use the unchanged original DrawSpec. For a missing or incorrect '
            'coplanar opening or panel on the named containing facade, batch its applicable path records inside '
            'draw_paths arguments.paths and choose a compatible style from the art direction/current index. '
            'A four-corner continuous frame uses closed=true; four distinct anchors with closed=false draw three '
            'edges. Intentional hidden contour intervals use separate open paths. Preserve correct existing frames. '
            'Verify the intended surface before '
            'using this coplanar construction for a projecting volume. no_coplanar_interval gives an empty '
            'feasible range under that assumption and its reference_inset (1% of the smaller planned dimension). '
            'This inset leaves a visible frame/wall separation; check the actual chosen line thickness. '
            'Preserve the plan and report a concrete conflict when needed. '
            'potential_axis_crossings locates where a construction axis passes through a planned form envelope. '
            'Inspect the actual foreground silhouette and visible contour stops there; the axis locates structure '
            'and the envelope locates a form. Planned connection points locate attachments and support surfaces; '
            'construct the actual material contact, overlap and visible edges. Establish coherent forms and '
            'connections across the whole drawing before focal detail. Current images and original scope remain binding.'}
