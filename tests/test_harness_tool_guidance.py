import copy

import pytest

from studio.agent_tools import CHILD_TOOLS, MAIN_TOOLS
from studio.agents import context_tools


@pytest.mark.parametrize('child,catalog', [(False, MAIN_TOOLS), (True, CHILD_TOOLS)])
def test_tool_guidance_keeps_native_catalog_schema_permissions_and_defaults(child, catalog):
    original = copy.deepcopy(catalog)
    prompted = context_tools(child=child)
    assert len(prompted) == len(original)
    for actual, native in zip(prompted, original):
        assert actual['function']['name'] == native['function']['name']
        if native['function']['name'] in {'send_input', 'reject_agent_result'}:
            assert {**actual, 'function': {**actual['function'], 'description': native['function']['description']}} == native
        else:
            assert actual == native
    assert catalog == original
    prompted[0]['function']['parameters']['description'] = 'caller-local annotation'
    assert catalog == original
