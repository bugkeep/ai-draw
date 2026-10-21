import json

from shapely import LineString
from shapely.affinity import rotate, translate

from .path_recall import recall_path_geometry


STYLE_FIELDS = ('strokeColor', 'backgroundColor', 'strokeWidth', 'opacity', 'fillStyle',
                'strokeStyle', 'roughness', 'fontSize', 'fontFamily')


def compact_current_polyline(element):
    points, _, _ = recall_path_geometry(element, tolerance=1)
    if not 2 <= len(points) <= 8:
        return None
    geometry = translate(LineString(points), xoff=element['x'], yoff=element['y'])
    geometry = rotate(geometry, element.get('angle', 0), use_radians=True,
        origin=(element['x'] + element['width'] / 2, element['y'] + element['height'] / 2))
    return {'coordinate_space': 'canvas', 'points': list(geometry.coords),
            'source_point_count': len(element['points']), 'max_centerline_error': 1}


def appearance_index(objects, native):
    styles, positions, indexed = [], {}, []
    for item in objects:
        element = native[item['id']]
        style = {key: element.get(key) for key in STYLE_FIELDS}
        signature = json.dumps(style, sort_keys=True)
        if signature not in positions:
            positions[signature] = len(styles)
            styles.append({'id': len(styles), **style})
        indexed.append({**item, 'style_id': positions[signature],
            'text': str(element.get('text', ''))[:120],
            **({'point_count': len(element['points'])} if 'points' in element else {})})
        if element.get('points') and (polyline := compact_current_polyline(element)) is not None:
            indexed[-1]['current_polyline'] = polyline
    return indexed, styles


def indexed_summary(item, styles):
    style = styles[item['style_id']]
    return {'id': item['id'], 'name': item['name'], 'type': item['type'],
        **dict(zip(('x', 'y', 'width', 'height'), item['bounds'])),
        'angle': item['angle'], 'version': item['version'], 'text': item['text'],
        **{key: style[key] for key in ('strokeColor', 'backgroundColor', 'strokeWidth', 'opacity')},
        **({'point_count': item['point_count']} if 'point_count' in item else {})}


def index_scene_once(prompt, index):
    styles = {item['id']: item for item in index.get('styles', [])}
    known = {item['id']: indexed_summary(item, styles) for item in index['objects']
             if 'style_id' in item and item['style_id'] in styles}

    def separate(items):
        kept, references = [], []
        for item in items:
            represented = known.get(item['id'], {})
            if all(key in represented and represented[key] == value for key, value in item.items()):
                references.append(item['id'])
            else:
                kept.append(item)
        return kept, references

    if 'scene' in prompt:
        scene = prompt['scene']
        original = scene.get('elements', [])
        scene['elements'], references = separate(original)
        if references:
            scene['object_index_refs'] = references
            scene['shown_count'] = len(scene['elements'])
            represented = set(known) | {item['id'] for item in scene['elements']}
            scene['has_more'] = len(represented) < scene['element_count']
            if not scene['has_more']:
                scene.pop('first_page', None)
            scene['instruction'] = ('Current bounds, versions, text and appearance for object_index_refs are supplied '
                'once in drawing_execution_memory.current_object_index. Its styles are literal native values, '
                'not visual approval. Query only missing IDs, exact path points or another stated missing fact.')
    if 'relevant_elements' in prompt:
        prompt['relevant_elements'], references = separate(prompt['relevant_elements'])
        if references:
            prompt['relevant_object_index_refs'] = references


def perspective_references(composition, scope):
    perspective = composition.get('perspective')
    if not perspective or not perspective['vanishing_points']:
        return None
    subjects = {item['id']: item for item in composition['subjects'] if item['category'] == 'architecture'}
    forms = {item['id']: item for item in composition['forms']
             if item['id'] in scope and item['subject_id'] in subjects}
    rays = []
    for landmark in composition.get('landmarks', []):
        if landmark['form_id'] not in forms or landmark['role'] not in {'apex', 'contact'}:
            continue
        bounds = subjects[forms[landmark['form_id']]['subject_id']]['bounds']
        x, y = landmark['x'], landmark['y']
        for index, (vx, vy) in enumerate(perspective['vanishing_points']):
            edge = bounds['x'] if vx < x else bounds['x'] + bounds['width']
            if vx == x or edge == x or not min(x, vx) < edge < max(x, vx):
                continue
            rays.append({'landmark_id': landmark['id'], 'vanishing_point_index': index,
                'canvas_ray': [[x, y], [edge, round(y + (vy - y) * (edge - x) / (vx - x), 4)]]})
    return {'coordinate_space': 'canvas', 'rays': rays,
        'instruction': 'Exact projected rays from declared apex/contact landmarks toward existing vanishing points, '
            'sampled at that architectural subject\'s horizontal bounds. Use a ray only for an edge that belongs '
            'to its actual depth direction/plane. They are construction references, not extra visible strokes; '
            'do not draw all rays. Keep verticals vertical in two-point perspective. They do not replace the '
            'immutable composition or authorize changing unrelated existing contours.'}


def artistic_brief(prompt):
    spec = prompt.get('drawspec')
    if not spec:
        return None
    composition = spec['composition']
    if spec['intent'] in {'edit', 'research'} or all(
            item['category'] == 'diagram' for item in composition['subjects']):
        return None
    stage = next(item for item in composition['stages'] if item['id'] == spec['stage_id'])
    scope = spec.get('form_ids') or stage['form_ids'] or [item['id'] for item in composition['forms']]
    focal = [item for item in composition['subjects'] if item['role'] == 'focal']
    return {'stage_id': stage['id'], 'phase': stage['phase'], 'intent': spec['intent'],
        'assigned_form_ids': scope,
        'stage_criteria': stage['criteria'], 'success_criteria': spec['success_criteria'],
        'direction': composition['art_direction'],
        'planned_focal_subjects': [{'id': item['id'], 'description': item['description'],
                                   'canvas_bounds': item['bounds']} for item in focal],
        'subject_roles': [{'id': item['id'], 'role': item['role']} for item in composition['subjects']],
        'value_design': composition.get('value_groups', []),
        'perspective_reference': perspective_references(composition, scope),
        'protected_negative_space_ids': [item['id'] for item in composition.get('negative_spaces', [])],
        'later_planned_stages': [{'id': item['id'], 'phase': item['phase'], 'criteria': item['criteria']}
            for item in composition['stages'][composition['stages'].index(stage) + 1:]],
        'painting_sequence': {
            'block_in': 'Establish the whole silhouette, gesture and major light/dark masses; compare the main '
                'proportions and negative spaces at full view. Resolve shared contacts and depth before local features. '
                'Use restrained temporary guides only where needed and retain their exact IDs for later removal.',
            'contour': 'Develop the visible silhouette and overlapping contours from the established volumes. '
                'Place a contour\'s start, turns, termination and hidden intervals; use distinct paths for separate '
                'visible segments. Preserve attachments and openings. Replace temporary guides as their contours resolve.',
            'modeling': 'Group light, middle tone and shadow to describe the main planes under the intended lighting. '
                'Connect shadow masses coherently, preserve paper highlights, and distinguish form shadows from cast '
                'and contact shadows. Add directional hatching only after the value design reads at full view.',
            'detail': 'Refine characteristic features and material marks where they support the focal reading. '
                'Simplify subordinate edges/texture; finish contacts, occlusion and contour stops. Remove remaining '
                'visible construction marks and check the whole image for consistent visual emphasis.',
        }[stage['phase']],
        'decisions': {
            'visual_focus': 'Choose the primary reading within focal_intent. Use relative contrast, edge emphasis, '
                'overlap and spacing to lead there; supporting forms remain legible without equal emphasis.',
            'expression': 'State the intended experience implied by the request and direction, then express it through '
                'the existing gesture, spatial rhythm, light/dark distribution and breathing room. Preserve planned '
                'pose/layout and requested content; convey meaning visually without adding explanatory labels or props.',
            'unity': 'Choose one compatible palette, contour language, edge hierarchy and mark rhythm within the '
                'declared medium. Monochrome remains monochrome. Apply the same decisions across related forms.',
            'selective_detail': 'Establish the silhouette, major contacts, negative space and coherent value groups '
                'before texture. Give characteristic details to the focal area; simplify subordinate texture and '
                'distant edges without omitting requested subjects. Every new mark should serve form or expression.',
            'artistic_language': 'Choose a specific repeatable formal decision that suits this work: a contour rhythm, '
                'shape family, shadow pattern or restrained accent. Develop it consistently; avoid generic equal-weight '
                'wireframes, unrelated decorations and arbitrary extra texture.',
        },
        'execution': 'Turn these decisions into the NEXT actual tools and visual changes, batching grounded related '
            'forms. Durable notes may retain the chosen expression, focal treatment, style and pending work alongside '
            'an actual action; an art-plan-only model round is unnecessary. Current native styles below describe the '
            'existing picture, not a requirement to keep an accidental default in an original creation. '
            'Form bounds and ovoid/box/cylinder volumes locate construction; they are not instructions to outline '
            'every bounding box or keep every internal volume visible in the finished work. Develop the subject\'s '
            'actual silhouette. Reuse correct current contours and update their style when needed instead of tracing '
            'a duplicate on top. Remove only visibly unwanted construction marks, never objects solely by their names. '
            'Treat review findings as observations to verify with the CURRENT image, native geometry and immutable '
            'landmarks. When a claimed coordinate/depth relation conflicts with those facts, preserve correct geometry '
            'and correct the actual visible ambiguity instead of repeating or reversing the same contour. Keep a '
            'concrete conflicting constraint explicit in report.unresolved when no authorized correction can satisfy '
            'both requirements, citing the actual geometry and constraint IDs. This hands an unapproved candidate '
            'back to the main Agent for a corrected input. Ordinary unfinished drawing remains your next action; '
            'later planned stages belong in summary, not report.unresolved. '
            'Value groups are design intentions, not a literal conversion from value to opacity. '
            'Use filled foreground silhouettes and deliberate contour breaks where background lines would cross them; '
            'keep interior details visible. Match the requested stage: block-in establishes the whole design, contour '
            'and modeling develop form, and detail selects characteristic accents. A block-in ready flag approves only '
            'that stage. State later planned work and unverified whole-artwork finish in the report summary; '
            'only claim checks supported by the current image. Scope and exact preservation constraints remain binding.'}
