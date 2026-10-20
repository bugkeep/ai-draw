import json
from pathlib import Path

from studio.draft_review import trace_page_pending


def recorded_calls():
    return json.loads((Path(__file__).parent / 'fixtures/trace-preparation-calls.json').read_text(encoding='utf-8'))['calls']


def test_actual_trace_pages_defer_automatic_review_until_the_plan_is_complete():
    calls = recorded_calls()
    traces = [item for item in calls if item['name'] in {'trace_reference', 'continue_reference_trace'}]
    assert len(traces) == 4
    assert [trace_page_pending(item['name'], item['output']) for item in traces] == [True, True, True, False]
    assert sum(item['output']['returned_paths'] for item in traces) == traces[-1]['output']['total_paths'] == 729


def test_reference_reads_and_explicit_completion_do_not_defer_review():
    calls = recorded_calls()
    for item in calls:
        if item['name'] in {'view_reference_image', 'finish_drawing'}:
            assert not trace_page_pending(item['name'], item['output'])
    pending = next(item['output'] for item in calls if item['name'] == 'trace_reference')
    assert not trace_page_pending('inspect_scene', pending)
    assert not trace_page_pending('view_canvas', pending)
    assert not trace_page_pending('draw_paths', pending)


def test_an_unfinished_or_invalid_trace_cursor_cannot_suspend_automatic_review():
    call = next(item for item in recorded_calls() if item['name'] == 'trace_reference')
    output = call['output']
    assert not trace_page_pending(call['name'], None)
    for cursor in [None, {}, {**output['next_page'], 'trace_plan_id': 'different-plan'},
                   {**output['next_page'], 'offset': output['offset']}]:
        assert not trace_page_pending(call['name'], {**output, 'next_page': cursor})
    assert not trace_page_pending(call['name'], {**output, 'ok': False})
    assert not trace_page_pending(call['name'], {**output, 'action_count': 0})
