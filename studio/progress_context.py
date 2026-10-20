"""Project saved Agent progress into the main model context without repeated reports."""


def result_progress(result):
    return {key: value for key, value in result.items() if key != 'summary'}


def input_progress(run):
    progress = {key: value for key, value in run.items() if key not in {'summary', 'completion'}}
    if run.get('completion'):
        progress['completion'] = {key: value for key, value in run['completion'].items()
            if key in {'status', 'reason', 'review_key', 'review_status', 'source'}}
    return progress


def agent_progress(agent):
    progress = {key: value for key, value in agent.items() if key not in {'summary', 'pending_results'}}
    latest = agent.get('latest_result')
    if latest:
        completion = latest.get('completion') or {}
        progress['latest_result'] = (result_progress(latest) if completion.get('visible_state') else dict(latest))
    elif agent.get('summary'):
        progress['summary'] = agent['summary']
    return progress


PROGRESS_RETRIEVAL = (
    'Progress indexes omit repeated summaries. child_agents.latest_result keeps the current scoped findings '
    'and next strategy once; research-only summaries remain there when there is no visual assessment. '
    'Use wait_agent with the agent_id to retrieve complete saved input results, including older inputs. '
    'Use view_agent_result for a pending candidate image; its private objects are separate from shared-canvas objects. '
    'Complete original records remain in SQLite and tool receipts remain retrievable through memory tools.'
)
