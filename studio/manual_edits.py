"""Merge explicit native editing intent without overwriting concurrent work."""

import copy
import json

from pydantic import BaseModel, ConfigDict, Field


EDITOR_METADATA = {'version', 'versionNonce', 'updated', 'index'}
GEOMETRY_FIELDS = {'x', 'y', 'width', 'height', 'angle', 'points', 'pressures', 'lastCommittedPoint'}
NATIVE_TYPES = {'rectangle', 'ellipse', 'diamond', 'text', 'line', 'arrow', 'freedraw'}
MISSING = object()


class NativeEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    before: dict | None
    after: dict | None
    fields: list[str] = Field(default_factory=list)
    place_after: str | None = None
    place_before: str | None = None


class ManualEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mutation_id: str = Field(min_length=1, max_length=100)
    client_id: str = Field(min_length=1, max_length=64)
    changes: list[NativeEdit] = Field(default_factory=list)
    order_before: list[str] | None = None
    order_after: list[str] | None = None
    selection: list[str] | None = None


class ManualEditConflict(Exception):
    def __init__(self, conflicts, document):
        super().__init__('画布中的同一内容已被同时修改，本地草稿仍保留。')
        self.conflicts, self.document = conflicts, document


def native_content(element):
    if element is None:
        return None
    return {**{key: value for key, value in element.items() if key not in EDITOR_METADATA},
            'boundElements': element.get('boundElements') or []}


def validate_native_scene(elements, selection):
    ids = [element.get('id') for element in elements]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError('Canvas elements need unique IDs')
    if any(element.get('type') not in NATIVE_TYPES for element in elements):
        raise ValueError('Unsupported Excalidraw element')
    if set(selection) - set(ids):
        raise ValueError('Selection refers to missing elements')
    json.dumps(elements, allow_nan=False)


def merge_order(current, before, after):
    """Reorder known slots, retaining concurrent additions and their relative order."""
    if before is None and after is None:
        return current, False
    if before is None or after is None or len(set(before)) != len(before) or len(set(after)) != len(after):
        raise ValueError('Layer order needs unique before and after IDs')
    retained = set(before) & set(after) & set(current)
    old = [value for value in before if value in retained]
    desired = [value for value in after if value in retained]
    actual = [value for value in current if value in retained]
    if desired != old and actual not in (old, desired):
        return current, True
    ordered = list(current)
    if desired != old:
        iterator = iter(desired)
        ordered = [next(iterator) if value in retained else value for value in ordered]
    return ordered, False


def merge_manual_edit(document, edit):
    changes = edit['changes']
    if len({change['id'] for change in changes}) != len(changes):
        raise ValueError('A manual edit must contain each changed object only once')
    current = {element['id']: element for element in document['elements']}
    merged = dict(current)
    conflicts = []
    for change in changes:
        key, before, after = change['id'], change['before'], change['after']
        if before is None and after is None:
            raise ValueError('An object edit needs before or after content')
        if any(item is not None and item.get('id') != key for item in (before, after)):
            raise ValueError('An object edit cannot change its identity')
        if after is not None:
            validate_native_scene([after], [])
        actual = current.get(key)
        if before is None:
            if actual is not None:
                conflicts.append({'id': key, 'fields': ['creation']})
            else:
                merged[key] = copy.deepcopy(after)
            continue
        if actual is None:
            if after is not None:
                conflicts.append({'id': key, 'fields': ['deleted']})
            continue
        if after is None or before.get('type') != after.get('type'):
            if native_content(actual) != native_content(before):
                conflicts.append({'id': key, 'fields': ['object']})
            elif after is None:
                merged.pop(key)
            else:
                merged[key] = copy.deepcopy(after)
            continue
        fields = set(change['fields'])
        if fields & (EDITOR_METADATA | {'id', 'type'}):
            raise ValueError('Manual field edits cannot replace identity or editor metadata')
        before_content, after_content, actual_content = map(native_content, (before, after, actual))
        intended = {field for field in set(before_content) | set(after_content)
                    if before_content.get(field, MISSING) != after_content.get(field, MISSING)}
        if intended - fields:
            raise ValueError('Manual field list must include every intended native object change')
        changed = {field for field in fields if before_content.get(field, MISSING) != after_content.get(field, MISSING)}
        guards = (changed | GEOMETRY_FIELDS if changed & GEOMETRY_FIELDS else changed) | {'type', 'isDeleted'}
        if 'isDeleted' in changed:
            guards |= set(before_content) | set(actual_content) | set(after_content)
        # Geometry is an atomic group: moving an old path must not silently use
        # a concurrently reshaped path with a different origin/angle/size.
        bad = [field for field in guards if actual_content.get(field, MISSING) != before_content.get(field, MISSING)
               and actual_content.get(field, MISSING) != after_content.get(field, MISSING)]
        if bad:
            conflicts.append({'id': key, 'fields': sorted(bad)})
            continue
        replacement = dict(actual)
        for field in changed:
            if field in after:
                replacement[field] = copy.deepcopy(after[field])
            else:
                replacement.pop(field, None)
        merged[key] = replacement
    if conflicts:
        raise ManualEditConflict(conflicts, document)
    order, order_conflict = merge_order(list(merged), edit.get('order_before'), edit.get('order_after'))
    if order_conflict:
        raise ManualEditConflict([{'id': '', 'fields': ['order']}], document)
    if edit.get('order_after') is not None:
        known = set(edit['order_before'])
        removed = {item['id'] for item in changes if item['after'] is None}
        if set(edit['order_after']) != known - removed:
            raise ValueError('Manual layer order does not match its native object changes')
    for change in changes:
        if change['before'] is not None or change['after'] is None:
            continue
        key = change['id']
        order.remove(key)
        if change.get('place_after') in order:
            order.insert(order.index(change['place_after']) + 1, key)
        elif change.get('place_before') in order:
            order.insert(order.index(change['place_before']), key)
        else:
            order.append(key)
    elements = [merged[key] for key in order]
    selection = document['selection'] if edit.get('selection') is None else edit['selection']
    selection = [key for key in selection if key in merged and not merged[key].get('isDeleted')]
    validate_native_scene(elements, selection)
    return elements, selection
