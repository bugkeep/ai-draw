import asyncio
import copy
import json
from pathlib import Path

import pytest

from studio.agents import DrawingAgents
from studio.drawing_dsl import assignment
from studio.drawing_progress import completion_context, completion_progress, constraint_forms, planned_progress
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def recorded(name):
    return json.loads((Path(__file__).parent / 'fixtures/drawing-progress-recorded-cafe.json').read_text(
        encoding='utf-8'))['samples'][name]


def test_actual_block_in_with_unfinished_stages_cannot_complete_the_drawing():
    sample = recorded('unfinished')
    original = copy.deepcopy(sample)
    result = planned_progress(sample['current'], sample['records'], sample['canvas_evidence'])
    assert sample == original
    assert not result['ok'] and result['error_kind'] == 'drawing_plan_incomplete'
    assert [item['stage_id'] for item in result['pending_stages']] == ['contour', 'detail']
    note = json.loads(sample['records'][0]['completion'])
    assert [item['evidence'] for item in result['unresolved_reports']] == note['unresolved']
    assert result['pending_stages'][1]['criteria'] == sample['current']['drawspec']['composition']['stages'][2]['criteria']
    assert result['independent_whole_picture_review_required'] and result['stage_checks_are_artist_claims']


def test_actual_contour_report_can_also_complete_detail_without_an_extra_drawing_pass():
    sample = recorded('combined_stages')
    assert [assignment(row['message']).drawspec.stage_id for row in sample['records']] == ['block_in', 'contour']
    result = planned_progress(sample['current'], sample['records'], sample['canvas_evidence'])
    assert result['ok'] and not result['pending_stages'] and not result['unresolved_reports']
    assert result['independent_whole_picture_review_required']


def test_recorded_spatial_constraints_keep_only_their_actual_form_scope():
    plan = recorded('unfinished')['current']['drawspec']['composition']
    assert constraint_forms(plan, 'table_apparent_size') == {'table_1', 'table_2'}
    assert constraint_forms(plan, 'door_window_height') == {'door', 'left_window'}
    assert constraint_forms(plan, 'tables_near_door') == {'table_1', 'door'}
    landmarks = {item['id']: item['form_id'] for item in plan['landmarks']}
    assert constraint_forms(plan, 'furniture1_on_paving') == {
        landmarks['table1_base'], landmarks['paving_under_table1']}
    assert constraint_forms(plan, 'contour') == {item['id'] for item in plan['forms']}


@pytest.mark.parametrize('excluded', ['rejected', 'uncommitted', 'old-composition', 'missing-native-objects'])
def test_recorded_completion_requires_submitted_current_composition_and_live_native_evidence(excluded):
    sample = recorded('combined_stages')
    if excluded == 'rejected':
        sample['records'][-1]['rejected_reason'] = '本次结果不采用'
    elif excluded == 'uncommitted':
        sample['records'][-1]['committed_count'] -= 1
    elif excluded == 'old-composition':
        sample['current']['composition_id'] = 'another-current-composition'
    else:
        sample['canvas_evidence']['live_ids'] = []
    result = planned_progress(sample['current'], sample['records'], sample['canvas_evidence'])
    assert not result['ok'] and result['pending_stages']


def test_sqlite_completion_feedback_precedes_model_review_and_stays_in_this_task(tmp_path):
    async def check():
        sample = recorded('unfinished')
        store = StudioStore(tmp_path / 'progress.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        models = DrawingAgents(store,
            ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
        try:
            session = store.create_session()
            task = store.create_task(session['id'])
            actor = store.agents.create_agent(session['id'], task['id'], 'actual-assignment',
                sample['records'][0]['message'], 'Artist', [])
            run = runtime.children._initialize(session['id'],
                store.agents.next_run(session['id'], actor['agent_id'], task['id']))
            # 建立实际任务和私有草稿状态，不生成模型回复或美术评分。
            result = store.agents.complete_run(session['id'], run['id'], '尚未开始作画')
            assert result['status'] == 'completed' and runtime._child_work_settled(session['id'], task['id'])
            progress = completion_progress(store, task['id'], session['document'])
            assert not progress['ok'] and len(progress['pending_stages']) == 3
            context = completion_context(store, task['id'], session['document'])
            assert len(context) == 1 and context[0]['role'] == 'user'
            assert json.loads(context[0]['content'])['drawing_completion_state'] == progress
            # 实际图片评审用于检查原样传递，不评价本测试的空白画布。
            picture = [{'role': 'user', 'content': json.dumps({
                'current_drawing_review': sample['recorded_picture_review'],
                'instruction': 'The actual current drawing passed independent review for this user request. '
                    'Settle any outstanding work and finish promptly; avoid introducing unrelated cosmetic changes.'})}]
            original = copy.deepcopy(picture)
            delivered = completion_context(store, task['id'], session['document'], picture)
            assert picture == original and len(delivered) == 2
            assert json.loads(delivered[0]['content'])['current_drawing_review'] == sample['recorded_picture_review']
            assert 'required stages' in json.loads(delivered[0]['content'])['instruction']
            assert delivered[-1] == context[-1]
            candidate = await runtime._complete_task(models, task['id'], session['id'], session['document']['id'], '完成')
            assert candidate == progress
            later = store.create_task(session['id'])
            assert completion_progress(store, later['id'], session['document']) is None
            assert completion_context(store, later['id'], session['document']) == []
            assert completion_context(store, later['id'], session['document'], picture) == picture
            other = store.create_session()
            with pytest.raises(ValueError, match='another canvas'):
                completion_progress(store, task['id'], other['document'])
            with store.lock:
                assert store.connection.execute("SELECT COUNT(*) FROM tool_logs WHERE kind='llm_response'").fetchone()[0] == 0
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


@pytest.mark.parametrize('intent', ['edit', 'research'])
def test_local_work_does_not_activate_the_whole_creation_plan(intent):
    sample = recorded('unfinished')
    request = assignment(sample['records'][0]['message'])
    request.drawspec.intent = intent
    sample['records'][0]['message'] = request.model_dump_json()
    assert planned_progress(sample['current'], sample['records'], sample['canvas_evidence']) is None
