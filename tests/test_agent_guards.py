from campus_agent.assistant import Assistant
from campus_agent.config import Settings
from campus_agent.llm import tool_message


def make_state():
    return {'messages': [], 'trace': [], 'evidence': {}, 'usage': {}, 'steps': 0, 'calls': 0,
            'searches': 0, 'stop': False, 'session_id': 'test', 'turn_id': 'guard', 'pending_actions': []}


async def test_model_cannot_invoke_write_tool(tmp_path):
    engine = Assistant(Settings(runtime=tmp_path, mode='fixture'))
    engine.db.put('turns', 'guard', {'id': 'guard', 'status': 'working'})
    engine.tool_schemas = []
    state = make_state()
    state['messages'] = [tool_message('apply_plan', {'snapshot': {}, 'operation_id': 'injected'})]
    result = await engine._tools(state)
    assert 'forbidden' in result['messages'][-1]['content']
    assert not engine.db.all('actions')
    assert not result['trace']


async def test_model_iteration_budget_is_enforced(tmp_path):
    engine = Assistant(Settings(runtime=tmp_path, mode='fixture'))
    state = make_state()
    state['steps'] = 6
    result = await engine._think(state)
    assert result['stop'] and '预算' in result['reply']


async def test_tool_budget_is_enforced(tmp_path):
    engine = Assistant(Settings(runtime=tmp_path, mode='fixture'))
    engine.db.put('turns', 'guard', {'id': 'guard', 'status': 'working'})
    engine.tool_schemas = [{'function': {'name': 'search_knowledge'}}]
    state = make_state()
    state['calls'] = 10
    state['messages'] = [tool_message('search_knowledge', {'query': 'test'})]
    result = await engine._tools(state)
    assert 'budget' in result['messages'][-1]['content']
    assert not result['trace']


async def test_search_budget_is_enforced(tmp_path):
    engine = Assistant(Settings(runtime=tmp_path, mode='fixture'))
    engine.db.put('turns', 'guard', {'id': 'guard', 'status': 'working'})
    engine.tool_schemas = [{'function': {'name': 'search_knowledge'}}]
    state = make_state()
    state['searches'] = 2
    state['messages'] = [tool_message('search_knowledge', {'query': 'test'})]
    result = await engine._tools(state)
    assert 'invalid_arguments' in result['messages'][-1]['content']
    assert not result['trace']
