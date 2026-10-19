import copy

from pydantic import Field

from studio.agent_tools import ToolInput, definition, tool_parameters


def test_real_title_argument_and_literal_values_survive_schema_annotation_removal():
    class Request(ToolInput):
        title: str = Field(min_length=1, max_length=40)
        payload: dict = Field(default={'title': 'literal default', 'items': ['one']})

    original = Request.model_json_schema()
    original['properties']['payload']['enum'] = [{'title': 'literal enum'}]
    original['properties']['payload']['const'] = {'title': 'literal constant'}
    before = copy.deepcopy(original)
    compact = tool_parameters(original)
    assert 'title' not in compact and 'title' in compact['properties']
    assert compact['properties']['title'] == {'type': 'string', 'minLength': 1, 'maxLength': 40}
    for keyword in ('default', 'enum', 'const'):
        assert compact['properties']['payload'][keyword] == original['properties']['payload'][keyword]
    assert original == before
    assert Request.model_validate({'title': 'A'}).title == 'A'
    assert definition('rename', 'Rename a drawing', Request)['function']['parameters']['required'] == ['title']


def test_nested_definition_and_tuple_constraints_keep_the_same_references_and_shape():
    class Region(ToolInput):
        point: tuple[float, float]

    class Input(ToolInput):
        region: Region | None = None

    original = Input.model_json_schema()
    compact = tool_parameters(original)
    assert compact['properties']['region']['anyOf'][0]['$ref'] == '#/$defs/Region'
    point = compact['$defs']['Region']['properties']['point']
    assert point['minItems'] == point['maxItems'] == 2
    assert point['prefixItems'] == [{'type': 'number'}, {'type': 'number'}]
    assert compact['additionalProperties'] is False and compact['$defs']['Region']['additionalProperties'] is False
    assert compact['$defs']['Region']['required'] == original['$defs']['Region']['required'] == ['point']
