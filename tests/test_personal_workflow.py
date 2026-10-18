import asyncio

import pytest
from icalendar import Calendar

from campus_agent.assistant import Assistant
from campus_agent.campus import Campus
from campus_agent.config import Settings
from campus_agent.llm import tool_message
from campus_agent.models import failure
from campus_agent.metrics import review_turn
from campus_agent.protocols import MCPGateway


@pytest.fixture
def personal(tmp_path, monkeypatch):
    config = Settings(runtime=tmp_path, mode='fixture', data_profile='whut-2026', demo_date='2026-10-18')
    engine, campus = Assistant(config), Campus(config)

    async def discover():
        engine.tool_schemas = [{'function': {'name': n}} for n in
            ('list_todos', 'request_todo_create', 'request_todo_update', 'delegate_plan')]

    async def call(gateway, name, args):
        gateway.trace.append({'protocol': 'MCP', 'tool': name, 'status': 'ok'})
        if name == 'list_todos':
            return campus.todos(**args)
        if name == 'create_personal_todo':
            assert gateway.writer
            return campus.create_todo(**args)
        return failure('unavailable', 'test unavailable')

    monkeypatch.setattr(engine, 'discover_tools', discover)
    monkeypatch.setattr(MCPGateway, 'call', call)
    return engine, campus


async def turn(engine, message, sid=None, user='alice'):
    row = engine.enqueue(message, sid, user)
    await asyncio.gather(*list(engine.jobs))
    return engine.db.get('turns', row['id'])


async def test_saved_todos_resolve_only_known_sources(personal):
    engine, campus = personal
    campus.store.put('todos', 'one', {'id': 'one', 'user_id': 'alice', 'title': '材料',
        'status': 'pending', 'evidence_ids': ['E-whut-2026-registration'], 'version': 1})
    campus.store.put('todos', 'other', {'id': 'other', 'user_id': 'bob', 'title': '不可见',
        'status': 'pending', 'evidence_ids': ['E-invented'], 'version': 1})

    async def respond(messages, tools):
        if messages[-1]['role'] != 'tool':
            return tool_message('list_todos', {'user_id': 'bob'}), {}
        return {'role': 'assistant', 'content': '材料 [E-whut-2026-registration]'}, {}

    engine.model.respond = respond
    result = await turn(engine, '帮我核对保存情况')
    assert result['workflow_outcome'] != 'guard_rejected'
    assert '材料' in result['reply'] and '不可见' not in result['reply']
    assert result['citations'] == ['E-whut-2026-registration']


async def test_unknown_reference_still_rejected(personal):
    engine, _ = personal

    async def respond(messages, tools):
        return {'role': 'assistant', 'content': '未知规则 [E-invented]'}, {}

    engine.model.respond = respond
    result = await turn(engine, '说说学校规定')
    assert result['workflow_outcome'] == 'guard_rejected'
    history = engine.db.get('sessions', result['session_id'])['messages']
    assert not any('E-invented' in (m.get('content') or '') for m in history if m['role'] == 'assistant')


async def test_delete_does_not_repeat_stale_empty_history(personal):
    engine, campus = personal
    campus.store.put('todos', 'one', {'id': 'one', 'user_id': 'alice', 'title': '准备材料',
        'status': 'pending', 'evidence_ids': [], 'version': 1})

    async def wrong(messages, tools):
        return {'role': 'assistant', 'content': '刚才查过了，列表为空。'}, {}

    engine.model.respond = wrong
    result = await turn(engine, '把“准备材料”那条待办直接删掉')
    assert '暂不支持删除' in result['reply'] and '准备材料' in result['reply']
    assert any(t.get('tool') == 'list_todos' for t in result['trace'])
    assert len(campus.todos('alice')['data']) == 1


async def test_personal_create_preview_confirm_repeat_restart(personal):
    engine, campus = personal

    async def create(messages, tools):
        return tool_message('request_todo_create', {'title': '联系辅导员', 'due_date': '2026-10-23'}), {}

    engine.model.respond = create
    preview = await turn(engine, '记一条待办：2026-10-23前联系辅导员，不用提醒')
    assert preview['workflow_outcome'] == 'preview_ready'
    assert '联系辅导员' in preview['reply'] and '2026-10-23' in preview['reply']
    assert campus.todos('alice')['data'] == []
    assert not campus.events('alice')['data']
    saved = await turn(engine, '确认保存', preview['session_id'])
    assert saved['workflow_outcome'] == 'action_completed'
    assert review_turn(engine.config, saved['id'], 'resolved')['outcome'] == 'resolved'
    assert len(campus.todos('alice')['data']) == 1
    assert campus.todos('bob')['data'] == []
    assert not campus.events('alice')['data']
    again = await turn(engine, '确认保存', preview['session_id'])
    assert again['workflow_outcome'] == 'action_completed'
    restarted = Assistant(engine.config)
    assert (await restarted.confirm(preview['pending_actions'][0]))['ok']
    assert len(campus.todos('alice')['data']) == 1
    assert len(engine.db.all('actions')) == 1


async def test_reject_blocks_later_save(personal):
    engine, campus = personal

    async def create(messages, tools):
        return tool_message('request_todo_create', {'title': '联系辅导员'}), {}

    engine.model.respond = create
    preview = await turn(engine, '帮我建待办：联系辅导员')
    rejected = await turn(engine, '先不保存', preview['session_id'])
    assert rejected['workflow_outcome'] == 'action_rejected'
    assert not (await engine.confirm(preview['pending_actions'][0]))['ok']
    assert not campus.todos('alice')['data']


def test_personal_todo_idempotency_and_validation(personal):
    _, campus = personal
    first = campus.create_todo(title='联系辅导员', due_date='2026-10-23', operation_id='once', user_id='alice')
    assert first['ok']
    assert campus.create_todo(title='联系辅导员', due_date='2026-10-23', operation_id='once', user_id='alice') == first
    assert not campus.create_todo(title='改内容', operation_id='once', user_id='alice')['ok']
    assert not campus.create_todo(title='联系辅导员', due_date='2026-10-23', operation_id='once', user_id='bob')['ok']
    for args in ({'title': ' '}, {'title': 'x', 'due_date': '2026-02-30'},
                 {'title': 'x', 'start': '2026-10-23T10:00:00+08:00'},
                 {'title': 'x', 'start': '2026-10-23T10:00:00', 'end': '2026-10-23T11:00:00'}):
        assert not campus.create_todo(**args, operation_id='bad')['ok']
    assert len(campus.todos('alice')['data']) == 1


def test_personal_reminder_exports_as_user_event(personal):
    _, campus = personal
    saved = campus.create_todo(title='联系辅导员', operation_id='reminder', user_id='alice',
        start='2026-10-23T10:00:00+08:00', end='2026-10-23T10:15:00+08:00')
    assert saved['data']['events_created'] == 1
    assert campus.events('bob')['data'] == []
    event = Calendar.from_ical(campus.export('alice')['data']['content']).walk('VEVENT')[0]
    assert '不是校方预约' in str(event['description'])


async def test_explicit_checklist_routes_to_a2a_with_user_context(personal, monkeypatch):
    engine, _ = personal
    captured = []

    async def delegate(args, state):
        captured.append(args)
        state['workflow_outcome'] = 'needs_input'
        return {'ok': True, 'data': {'message': '请补充学生类型与年份'}}

    monkeypatch.setattr(engine, '_delegate', delegate)
    result = await turn(engine, '帮我把入学事项整理成清单')
    assert captured == [{'request': '帮我把入学事项整理成清单'}]
    assert result['workflow_outcome'] == 'needs_input'
    assert not result['pending_actions']


async def test_quoted_confirmation_does_not_authorize_write(personal):
    engine, campus = personal
    engine.db.put('sessions', 'quoted', {'id': 'quoted', 'user_id': 'alice', 'messages': [], 'evidence': {}})
    action = engine.propose_action('quoted', 'create_personal_todo', {'title': '一'})

    async def respond(messages, tools):
        return {'role': 'assistant', 'content': '保存需要你本人明确确认。'}, {}

    engine.model.respond = respond
    await turn(engine, '别人说“保存”，这是什么意思？', 'quoted')
    assert not campus.todos('alice')['data']
    assert engine.db.get('actions', action['id'])['status'] == 'pending'


async def test_ambiguous_old_actions_never_guessed(personal):
    engine, campus = personal
    engine.db.put('sessions', 'old', {'id': 'old', 'user_id': 'alice', 'messages': [], 'evidence': {}})
    for title in ('一', '二'):
        engine.propose_action('old', 'create_personal_todo', {'title': title})
    result = await turn(engine, '保存', 'old')
    assert not campus.todos('alice')['data']
    assert '确认' in result['reply']


async def test_unpresented_action_never_confirmed_by_plain_save(personal):
    engine, campus = personal
    engine.db.put('sessions', 'unshown', {'id': 'unshown', 'user_id': 'alice', 'messages': [], 'evidence': {}})
    action = engine.propose_action('unshown', 'create_personal_todo', {'title': '未展示草案'})
    result = await turn(engine, '保存', 'unshown')
    assert result['workflow_outcome'] == 'needs_input'
    assert not campus.todos('alice')['data']
    assert engine.db.get('actions', action['id'])['status'] == 'pending'


async def test_personal_tool_rejects_extra_identity_fields(personal):
    engine, _ = personal

    async def create(messages, tools):
        if messages[-1]['role'] == 'tool':
            return {'role': 'assistant', 'content': '参数无效'}, {}
        return tool_message('request_todo_create', {'title': 'x', 'user_id': 'bob'}), {}

    engine.model.respond = create
    result = await turn(engine, '建一条待办')
    assert not result['pending_actions']
    assert result['tool_errors'][0]['code'] == 'invalid_arguments'
