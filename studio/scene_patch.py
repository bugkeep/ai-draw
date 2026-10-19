def scene_patch(before, after):
    prior = {element['id']: element for element in before['elements']}
    current = {element['id']: element for element in after['elements']}
    removed = [element_id for element_id in prior if element_id not in current]
    added = [element_id for element_id in current if element_id not in prior]
    retained = [element_id for element_id in prior if element_id in current]
    patch = {'id': after['id'], 'title': after['title'], 'base_version': before['version'],
             'version': after['version'], 'updated_at': after['updated_at'], 'selection': after['selection'],
             'upserts': [element for element in after['elements'] if prior.get(element['id']) != element],
             'removed_ids': removed}
    if list(current) != retained + added:
        patch['order'] = list(current)
    return patch
