import hashlib
import json
import math
from graphlib import TopologicalSorter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .actions import element_fingerprint
from .agent_store import stable_id
from .vision import scene_digest


DslText = Annotated[str, Field(min_length=1)]
CanvasNumber = Annotated[float, Field(ge=-100000, le=100000, allow_inf_nan=False)]
CanvasPoint = tuple[CanvasNumber, CanvasNumber]


class DslValue(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class CompositionBounds(DslValue):
    x: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    y: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    width: float = Field(gt=0, le=200000, allow_inf_nan=False)
    height: float = Field(gt=0, le=200000, allow_inf_nan=False)

    @model_validator(mode='after')
    def usable_canvas_range(self):
        if self.x + self.width > 100000 or self.y + self.height > 100000:
            raise ValueError('Composition bounds exceed the supported canvas coordinate range')
        return self


class ArtDirection(DslValue):
    medium: DslText
    rendering: DslText = Field(description='Requested visual treatment, e.g. naturalistic pencil, ink line art, diagram.')
    focal_intent: DslText
    line_quality: DslText
    value_strategy: DslText
    finish_criteria: list[DslText] = Field(min_length=1, description='Whole artwork finish; block-in alone does not satisfy later criteria.')


class CompositionSubject(DslValue):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    bounds: CompositionBounds
    category: Literal['figure', 'animal', 'landscape', 'architecture', 'object', 'diagram', 'environment']
    role: Literal['focal', 'supporting', 'setting']


class PrimaryForm(DslValue):
    id: DslText
    subject_id: DslText
    parent_id: str = ''
    form: Literal['ovoid', 'box', 'cylinder', 'plane', 'ribbon', 'mass']
    bounds: CompositionBounds
    axis_degrees: float = Field(default=0, allow_inf_nan=False)
    description: DslText


class Gesture(DslValue):
    subject_id: DslText
    line_of_action: list[CanvasPoint] = Field(min_length=2)
    description: DslText


class CompositionLandmark(DslValue):
    id: str = Field(min_length=1)
    form_id: DslText
    role: Literal['joint', 'contact', 'apex', 'center', 'feature', 'intersection']
    x: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    y: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    description: str = Field(min_length=1)


class CompositionRelation(DslValue):
    id: DslText
    kind: Literal['attached_to', 'supported_by', 'in_front_of', 'behind', 'continuous_with', 'aligned_with']
    source: str = Field(min_length=1, description='Contact/connection uses landmark IDs; depth uses subject/form IDs.')
    target: str = Field(min_length=1)
    max_gap: float = Field(default=0, ge=0, le=200000, allow_inf_nan=False,
        description='Maximum canvas-unit distance between planned contact/connection landmarks.')
    description: str = Field(min_length=1)


class Proportion(DslValue):
    id: DslText
    source: DslText = Field(description='Declared subject/form ID or reserved frame for the common composition bounds.')
    target: DslText = Field(description='A distinct subject/form ID or frame; e.g. head / figure or figure / frame on the same axis.')
    axis: Literal['width', 'height']
    ratio: float = Field(gt=0, allow_inf_nan=False)
    tolerance: float = Field(default=0.1, ge=0, allow_inf_nan=False,
        description='Absolute tolerance around the declared ratio; measured from planned subject/form bounds.')
    description: DslText


class NegativeSpace(DslValue):
    id: DslText
    bounds: CompositionBounds
    description: DslText


class ValueGroup(DslValue):
    id: DslText
    member_ids: list[DslText] = Field(min_length=1, description='Declared subject or form IDs included in this light/shadow group.')
    value: float = Field(ge=0, le=1, allow_inf_nan=False, description='0=black, 1=paper white; a design value, not pixel approval.')
    description: DslText


class ArtStage(DslValue):
    id: DslText
    phase: Literal['block_in', 'contour', 'modeling', 'detail']
    form_ids: list[DslText] = Field(default_factory=list, description='Empty covers the whole composition.')
    criteria: list[DslText] = Field(min_length=1)


class Perspective(DslValue):
    projection: Literal['orthographic', 'one_point', 'two_point', 'three_point', 'atmospheric', 'free']
    horizon_y: CanvasNumber | None = None
    vanishing_points: list[CanvasPoint] = Field(default_factory=list)

    @model_validator(mode='after')
    def declared_projection(self):
        count = {'one_point': 1, 'two_point': 2, 'three_point': 3}.get(self.projection, 0)
        if len(self.vanishing_points) < count:
            raise ValueError('Perspective needs its declared number of vanishing points')
        return self


class Composition(DslValue):
    revision: int = Field(ge=1)
    frame: CompositionBounds
    coordinate_space: Literal['canvas'] = 'canvas'
    art_direction: ArtDirection
    subjects: list[CompositionSubject] = Field(min_length=1)
    forms: list[PrimaryForm] = Field(min_length=1, description='Major construction masses, e.g. skull/ribcage/pelvis, head/torso/tail, foreground/middle distance.')
    gestures: list[Gesture] = Field(default_factory=list)
    landmarks: list[CompositionLandmark] = Field(default_factory=list)
    proportions: list[Proportion] = Field(default_factory=list)
    relations: list[CompositionRelation] = Field(default_factory=list)
    negative_spaces: list[NegativeSpace] = Field(default_factory=list)
    value_groups: list[ValueGroup] = Field(default_factory=list)
    perspective: Perspective | None = None
    stages: list[ArtStage] = Field(min_length=1, description='Ordered whole-artwork work plan; include all stages required by the requested finish.')
    preserve: list[DslText] = Field(default_factory=list)

    @model_validator(mode='after')
    def coherent_structure(self):
        problems = []
        subjects = {item.id: item for item in self.subjects}
        forms = {item.id: item for item in self.forms}
        landmarks = {item.id: item for item in self.landmarks}
        records = [*self.subjects, *self.forms, *self.landmarks, *self.proportions,
            *self.relations, *self.negative_spaces, *self.value_groups, *self.stages]
        ids = [item.id for item in records]
        if len(set(ids)) != len(ids):
            raise ValueError('DrawSpec IDs must be unique across the composition')
        if 'frame' in ids:
            raise ValueError('frame is reserved for the common composition bounds; use another record ID')
        for item in [*self.subjects, *self.forms, *self.negative_spaces]:
            area, frame = item.bounds, self.frame
            if (area.x < frame.x or area.y < frame.y or area.x + area.width > frame.x + frame.width
                    or area.y + area.height > frame.y + frame.height):
                raise ValueError(f'{item.id} exceeds the common composition frame')
        hierarchy = {}
        for form in self.forms:
            if form.subject_id not in subjects:
                raise ValueError(f'Form {form.id} refers to an unknown subject')
            if form.parent_id:
                if form.parent_id not in forms or forms[form.parent_id].subject_id != form.subject_id:
                    raise ValueError('Form parents must exist in the same subject')
                hierarchy[form.id] = {form.parent_id}
        order = list(TopologicalSorter(hierarchy).static_order())
        members = {key: {key} for key in forms}
        for key in reversed(order):
            parent = forms[key].parent_id
            if parent:
                members[parent].update(members[key])
        members.update({key: {form.id for form in self.forms if form.subject_id == key} for key in subjects})
        for landmark in self.landmarks:
            if landmark.form_id not in forms:
                raise ValueError(f'Landmark {landmark.id} refers to an unknown form')
            frame = self.frame
            if not (frame.x <= landmark.x <= frame.x + frame.width and frame.y <= landmark.y <= frame.y + frame.height):
                problems.append(f'Landmark {landmark.id} exceeds the common composition frame')
            area = forms[landmark.form_id].bounds
            if (landmark.role in {'joint', 'contact'} and not (area.x <= landmark.x <= area.x + area.width
                    and area.y <= landmark.y <= area.y + area.height)):
                problems.append(f'Landmark {landmark.id} at ({landmark.x:g},{landmark.y:g}) is outside '
                    f'its form {landmark.form_id} bounds ({area.x:g},{area.y:g},{area.width:g},{area.height:g})')
        for gesture in self.gestures:
            if gesture.subject_id not in subjects:
                raise ValueError('A gesture needs a declared subject')
            if len(set(gesture.line_of_action)) < 2:
                raise ValueError('A line of action needs distinct canvas points')
            if any(not (self.frame.x <= x <= self.frame.x + self.frame.width
                    and self.frame.y <= y <= self.frame.y + self.frame.height) for x, y in gesture.line_of_action):
                raise ValueError('A line of action exceeds the composition frame')
        for subject in self.subjects:
            owned = {form.id for form in self.forms if form.subject_id == subject.id}
            if not owned:
                raise ValueError(f'Subject {subject.id} needs its construction forms')
            if subject.category in {'figure', 'animal'}:
                if (len(owned) < 2 or not any(gesture.subject_id == subject.id for gesture in self.gestures)
                        or not any(mark.form_id in owned for mark in self.landmarks)):
                    raise ValueError(f'Organic subject {subject.id} needs major forms, a line of action and structural landmarks')
        measured_items = subjects | forms
        measurement_bounds = {key: item.bounds for key, item in measured_items.items()} | {'frame': self.frame}
        for ratio in self.proportions:
            if ratio.source not in measurement_bounds or ratio.target not in measurement_bounds or ratio.source == ratio.target:
                problems.append(f'Proportion {ratio.id} needs two distinct measurement IDs (subject/form or frame): {ratio.source}, {ratio.target}')
                continue
            measured = getattr(measurement_bounds[ratio.source], ratio.axis) / getattr(measurement_bounds[ratio.target], ratio.axis)
            if abs(measured - ratio.ratio) > ratio.tolerance + 1e-9:
                problems.append(f'Proportion {ratio.id} contradicts planned bounds: '
                    f'measured ratio={measured:g}, declared={ratio.ratio:g} +/- {ratio.tolerance:g}')
        occlusion = {}
        for relation in self.relations:
            endpoints = subjects.keys() | forms.keys() | landmarks.keys()
            if relation.source not in endpoints or relation.target not in endpoints or relation.source == relation.target:
                problems.append(f'Relation {relation.id} needs distinct declared subject/form/landmark IDs: {relation.source}, {relation.target}')
                continue
            if relation.kind in {'attached_to', 'supported_by', 'continuous_with'}:
                if relation.source not in landmarks or relation.target not in landmarks:
                    problems.append(f'Connection {relation.id} needs two exact landmark IDs')
                    continue
                a, b = landmarks[relation.source], landmarks[relation.target]
                if math.dist((a.x, a.y), (b.x, b.y)) > relation.max_gap + 1e-9:
                    problems.append(f'Connection {relation.id}: {a.id}=({a.x:g},{a.y:g}), '
                        f'{b.id}=({b.x:g},{b.y:g}), distance={math.dist((a.x, a.y), (b.x, b.y)):g} '
                        f'exceeds max_gap={relation.max_gap:g}')
            if relation.kind in {'behind', 'in_front_of'}:
                if relation.source in landmarks or relation.target in landmarks:
                    raise ValueError('Occlusion refers to subject/form IDs, not isolated landmarks')
                lower, upper = ((relation.source, relation.target) if relation.kind == 'behind'
                    else (relation.target, relation.source))
                for upper_form in members[upper]:
                    occlusion.setdefault(upper_form, set()).update(members[lower])
        TopologicalSorter(occlusion).prepare()
        for group in self.value_groups:
            if set(group.member_ids) - measured_items.keys():
                problems.append(f'Value group {group.id} needs declared subject/form IDs: {sorted(set(group.member_ids) - measured_items.keys())}')
        for stage in self.stages:
            if set(stage.form_ids) - forms.keys():
                problems.append(f'Stage {stage.id} needs declared form IDs: {sorted(set(stage.form_ids) - forms.keys())}')
        if self.stages[0].phase != 'block_in' or self.stages[0].form_ids:
            problems.append('The first stage establishes the whole-composition block-in')
        if problems:
            raise ValueError('\n'.join(problems))
        return self


class DrawingPass(DslValue):
    protocol: Literal['drawspec/1'] = 'drawspec/1'
    stage_id: DslText
    intent: Literal['execute', 'repair', 'edit', 'research'] = 'execute'
    form_ids: list[DslText] = Field(default_factory=list, description='Assigned forms within the stage; empty uses its scope.')
    success_criteria: list[DslText] = Field(min_length=1, description='Visible requirements for THIS input, alongside the stage criteria.')


class DrawSpec(DrawingPass):
    composition: Composition

    @model_validator(mode='after')
    def assigned_subjects_exist(self):
        stages = {stage.id: stage for stage in self.composition.stages}
        if self.stage_id not in stages:
            raise ValueError('stage_id must identify a declared composition stage')
        stage = stages[self.stage_id]
        if set(self.form_ids) - {item.id for item in self.composition.forms}:
            raise ValueError('Assigned form_ids must exist in the common composition')
        if stage.form_ids and set(self.form_ids) - set(stage.form_ids):
            raise ValueError('Assigned forms exceed this stage scope')
        if self.phase == 'block_in' and self.intent == 'execute' and self.form_ids:
            raise ValueError('The block-in pass covers the whole composition; omit form_ids')
        return self

    @property
    def phase(self):
        return next(stage.phase for stage in self.composition.stages if stage.id == self.stage_id)

    @property
    def composition_id(self):
        encoded = json.dumps(self.composition.model_dump(), sort_keys=True, separators=(',', ':'))
        return 'composition_' + hashlib.sha256(encoded.encode()).hexdigest()


class CompositionAmendment(DslValue):
    source_call_id: DslText = Field(description='Exact failed spawn_agent/send_input call ID containing the full composition in THIS task.')
    subjects: list[CompositionSubject] = Field(default_factory=list)
    forms: list[PrimaryForm] = Field(default_factory=list)
    landmarks: list[CompositionLandmark] = Field(default_factory=list)
    proportions: list[Proportion] = Field(default_factory=list)
    relations: list[CompositionRelation] = Field(default_factory=list)
    negative_spaces: list[NegativeSpace] = Field(default_factory=list)
    value_groups: list[ValueGroup] = Field(default_factory=list)
    stages: list[ArtStage] = Field(default_factory=list)
    remove_ids: list[DslText] = Field(default_factory=list)
    art_direction: ArtDirection | None = None
    frame: CompositionBounds | None = None
    perspective: Perspective | None = None
    gestures: list[Gesture] | None = None


class DrawSpecInput(DrawingPass):
    composition: Composition | None = Field(default=None,
        description='Complete common structure for the first assignment or a deliberately revised composition.')
    composition_ref: str = Field(default='', pattern=r'^(?:composition_[a-f0-9]{64})?$',
        description='For subsequent messages, copy shared_drawspec.composition_id and omit composition. '
            'Runtime resolves the exact saved snapshot for this canvas; do not rewrite its descriptions or coordinates.')
    amend: CompositionAmendment | None = Field(default=None,
        description='Repair a FAILED full-composition call without retransmitting its whole plan. '
            'Provide only changed complete records by their IDs; runtime reuses the saved original and validates the entire result. '
            'Never use amend for a successful/published assignment; use composition_ref for its later work.')

    @model_validator(mode='after')
    def one_composition_source(self):
        if sum((self.composition is not None, bool(self.composition_ref), self.amend is not None)) != 1:
            raise ValueError('Provide exactly one composition source: composition, composition_ref or amend')
        if self.composition is not None:
            DrawSpec.model_validate(self.model_dump(exclude={'composition_ref', 'amend'}))
        return self


class DrawingAssignment(DslValue):
    protocol: Literal['drawspec/1'] = 'drawspec/1'
    kind: Literal['assignment']
    goal: str = Field(min_length=1)
    drawspec: DrawSpec


def assignment(message):
    try:
        value = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or (value.get('kind') != 'assignment'
            and not str(value.get('protocol', '')).startswith('drawspec/')):
        return None
    return DrawingAssignment.model_validate(value)


def assignment_goal(message):
    parsed = assignment(message)
    return parsed.goal if parsed else message


def latest_spec(store, task_id, *, include_previous=False):
    task = store.get_task(task_id)
    condition = 'original.session_id=current.session_id AND original.rowid<=current.rowid' if include_previous else 'i.task_id=current.id'
    with store.lock:
        row = store.connection.execute('SELECT i.message,i.task_id FROM agent_inputs i '
            'JOIN tasks original ON original.id=i.task_id JOIN tasks current ON current.id=? '
            f'WHERE {condition} AND json_extract(CASE WHEN json_valid(i.message) THEN i.message ELSE \'{{}}\' END,\'$.protocol\') '
            "='drawspec/1' ORDER BY i.rowid DESC LIMIT 1", (task_id,)).fetchone()
    if row is None:
        return None
    parsed = assignment(row['message'])
    return {'composition_id': parsed.drawspec.composition_id, 'task_id': row['task_id'],
        'drawspec': parsed.drawspec.model_dump(), 'from_previous_task': row['task_id'] != task['id']}


def structure_submitted(store, task_id, composition_id):
    with store.lock:
        rows = store.connection.execute('SELECT i.message,d.actions,d.committed_count FROM drawing_results d '
            'JOIN agent_runs r ON r.id=d.run_id JOIN agent_inputs i ON i.id=r.input_id '
            'JOIN tasks original ON original.id=d.task_id JOIN tasks current ON current.id=? '
            "WHERE original.session_id=current.session_id AND original.rowid<=current.rowid "
            "AND d.rejected_reason='' AND d.committed_count>0", (task_id,)).fetchall()
    for row in rows:
        parsed = assignment(row['message'])
        if (parsed and parsed.drawspec.phase == 'block_in'
                and parsed.drawspec.composition_id == composition_id
                and row['committed_count'] == len(json.loads(row['actions']))):
            return True
    return False


def amend_composition(store, task_id, amendment):
    with store.lock:
        row = store.connection.execute('SELECT name,arguments,output FROM agent_tool_calls '
            'WHERE task_id=? AND caller_id=? AND call_id=?',
            (task_id, f'main:{task_id}', amendment.source_call_id)).fetchone()
    if (row is None or row['name'] not in {'spawn_agent', 'send_input'} or row['output'] is None
            or json.loads(row['output']).get('ok') is not False):
        raise ValueError('amend.source_call_id must identify a failed main assignment in this task')
    original = json.loads(row['arguments'])
    source_spec = original.get('drawspec') if isinstance(original, dict) else None
    composition = source_spec.get('composition') if isinstance(source_spec, dict) else None
    if not isinstance(composition, dict):
        raise ValueError('The amendment source must contain a complete composition; reference the original full call')
    replacements = amendment.model_dump(mode='json', exclude_unset=True, exclude={'source_call_id', 'remove_ids'})
    sections = ('subjects', 'forms', 'landmarks', 'proportions', 'relations', 'negative_spaces', 'value_groups', 'stages')
    found = set()
    remove = set(amendment.remove_ids)
    for key in sections:
        items = composition.get(key, [])
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in items):
            raise ValueError(f'Invalid draft {key}; resubmit a complete composition to repair this section')
        found.update(item['id'] for item in items if item['id'] in remove)
        changed = {item['id']: item for item in replacements.get(key, [])}
        if len(changed) != len(replacements.get(key, [])) or changed.keys() & remove:
            raise ValueError('Amendment records need unique IDs and cannot both replace and remove the same record')
        merged = [changed.pop(item['id'], item) for item in items if item['id'] not in remove]
        merged.extend(changed.values())
        if key in composition or key in replacements:
            composition[key] = merged
    if remove - found:
        raise ValueError(f'Amendment cannot remove undeclared records: {sorted(remove - found)}')
    composition.update({key: value for key, value in replacements.items() if key not in sections})
    return composition


def prepare_assignment(store, session_id, task_id, goal, spec, *, agent_id=None):
    store.agents.check_task(session_id, task_id)
    existing = assignment(goal)
    if existing:
        if spec is not None:
            raise ValueError('Send the local goal and drawspec separately; do not nest drawing assignments')
        goal, spec = existing.goal, existing.drawspec.model_dump()
    if spec is None:
        if latest_spec(store, task_id) is not None:
            raise ValueError('This task communicates through DrawSpec; include composition_ref, stage_id, intent and success_criteria')
        return goal
    parameters = DrawSpecInput.model_validate(spec)
    current = latest_spec(store, task_id)
    data = parameters.model_dump(exclude={'composition_ref', 'amend'})
    if parameters.amend is not None:
        data['composition'] = amend_composition(store, task_id, parameters.amend)
    elif parameters.composition is None:
        source = current or latest_spec(store, task_id, include_previous=True)
        if source is None or source['composition_id'] != parameters.composition_ref:
            raise ValueError('composition_ref must identify the current saved composition in this canvas')
        data['composition'] = source['drawspec']['composition']
    spec = DrawSpec.model_validate(data)
    if current:
        previous = current['drawspec']['composition']['revision']
        if spec.composition.revision < previous:
            raise ValueError('Drawing assignment uses an older composition revision; send the current shared composition')
        if spec.composition.revision == previous and spec.composition_id != current['composition_id']:
            raise ValueError('The same composition revision must contain identical structure for every child')
        if spec.composition.revision > previous:
            active = any(item['owner_task_id'] == task_id and item['state'] == 'Running'
                for item in store.agents.list_agents(session_id))
            if active or store.agents.pending_results(session_id, task_id=task_id):
                raise ValueError('Settle current child work and apply or reject pending results before revising the common composition')
    document = store.get_document(store.session_document_id(session_id))
    if spec.intent == 'edit' and not any(not item.get('isDeleted') for item in document['elements']):
        raise ValueError('An empty canvas needs a whole-composition structure pass before local editing')
    if (spec.intent == 'execute' and spec.phase != 'block_in'
            and not structure_submitted(store, task_id, spec.composition_id)):
        raise ValueError('Inspect and submit a structure result before local refinement; use repair with the SAME Agent to correct a pending private structure')
    if spec.intent == 'repair' and not any(not item.get('isDeleted') for item in document['elements']):
        pending = store.agents.pending_results(session_id, agent_id=agent_id, task_id=task_id) if agent_id else []
        inherited = [assignment(store.agents.get_run(session_id, result['run_id'])['message']) for result in pending]
        if not any(item and item.drawspec.composition_id == spec.composition_id for item in inherited):
            raise ValueError('Repair needs existing canvas structure or a pending private candidate of this same Agent')
    return DrawingAssignment(kind='assignment', goal=goal, drawspec=spec).model_dump_json()


def require_current_result(store, task_id, result):
    if result['committed_count'] >= len(result['actions']) or not result.get('run_id'):
        return
    session_id = store.get_task(task_id)['session_id']
    run = store.agents.get_run(session_id, result['run_id'])
    parsed = assignment(run['message'])
    current = latest_spec(store, task_id)
    if parsed and current and parsed.drawspec.composition_id != current['composition_id']:
        raise ValueError('This result uses a different composition revision; inspect it and reassign current work through send_input')


class FormBinding(DslValue):
    form_id: DslText
    object_ids: list[DslText] = Field(min_length=1)


class ConstraintCheck(DslValue):
    constraint_id: DslText
    status: Literal['addressed', 'unresolved', 'unchanged']
    evidence: DslText


class DrawingReport(DslValue):
    protocol: Literal['drawspec/1']
    kind: Literal['report']
    summary: DslText
    bindings: list[FormBinding]
    checks: list[ConstraintCheck] = Field(default_factory=list)
    unresolved: list[DslText]


def parse_report(content, run):
    request = assignment(run['message'])
    if request is None:
        return content, None
    report = DrawingReport.model_validate_json(content)
    spec = request.drawspec
    forms = {form.id for form in spec.composition.forms}
    live = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
    bound = [item.form_id for item in report.bindings]
    if len(set(bound)) != len(bound) or set(bound) - forms:
        raise ValueError('Report bindings need distinct declared form IDs')
    if any(set(item.object_ids) - live for item in report.bindings):
        raise ValueError('Report object_ids must exist in this exact private draft')
    constraints = {item.id for item in [*spec.composition.proportions, *spec.composition.relations, *spec.composition.stages]}
    checked = [item.constraint_id for item in report.checks]
    if len(set(checked)) != len(checked) or set(checked) - constraints:
        raise ValueError('Report checks need distinct declared proportion, relation or stage IDs')
    stage = next(item for item in spec.composition.stages if item.id == spec.stage_id)
    required = set(spec.form_ids or stage.form_ids or forms)
    missing = required - set(bound)
    if spec.intent != 'research' and missing and not report.unresolved:
        raise ValueError(f'Bind the assigned forms or explicitly report unfinished work: {sorted(missing)}')
    return report.summary, {**report.model_dump(), 'composition_id': spec.composition_id,
        'composition_revision': spec.composition.revision, 'stage_id': spec.stage_id, 'phase': spec.phase,
        'intent': spec.intent, 'agent_id': run['agent_id'], 'input_id': run['input_id'], 'run_id': run['id'],
        'unbound_form_ids': sorted(missing), 'checks_are_artist_claims': True}


def result_evidence(store, run, report, review_key):
    document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
    digest = scene_digest(document)
    with store.lock:
        baseline = store.connection.execute('SELECT document FROM draft_review_baselines WHERE run_id=?', (run['id'],)).fetchone()
        images = store.connection.execute('SELECT id,region,delivered_at FROM canvas_observations WHERE task_id=? '
            'AND caller_id=? AND scene_hash=? ORDER BY created_at DESC', (run['owner_task_id'], run['id'], digest)).fetchall()
    initial = json.loads(baseline['document']) if baseline and baseline['document'] else run['document']
    old = {item['id']: element_fingerprint(item) for item in initial['elements'] if not item.get('isDeleted')}
    new = {item['id']: element_fingerprint(item) for item in run['draft'] if not item.get('isDeleted')}
    evidence, seen = [], set()
    for row in images:
        view = 'full' if row['region'] == 'null' else 'focus'
        if view not in seen:
            evidence.append({'observation_id': row['id'], 'scope': 'private_draft',
                'view': view, 'region': json.loads(row['region']), 'delivered_to_artist': row['delivered_at'] is not None})
            seen.add(view)
    return {**report, 'task_id': run['owner_task_id'], 'native_result_id': stable_id('result', run['id']),
        'changed_ids': sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key)),
        'scene_hash': digest, 'image_evidence': evidence, 'review_key': review_key}
