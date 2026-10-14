import asyncio

import httpx
import pytest
from icalendar import Calendar

from campus_agent.assistant import Assistant
from campus_agent.models import Plan, PlanRequest
from campus_agent.protocols import MCPGateway, PlannerClient, task_artifact
from campus_agent.scheduler import validate_plan
from campus_agent.seed import build_seed
from campus_agent.db import Store

pytestmark = pytest.mark.integration


async def poll_task(client, task):
    for _ in range(200):
        if task['status']['state'] in ('input-required', 'completed', 'failed', 'canceled'):
            return task
        await asyncio.sleep(0.1)
        task = await client.get(task['id'])
    raise TimeoutError('A2A task')


async def chat(stack, message, sid=None):
    async with httpx.AsyncClient(timeout=20) as client:
        result = (await client.post(stack.base + '/v1/chat', json={'message': message, 'session_id': sid})).json()
        for _ in range(200):
            turn = (await client.get(stack.base + '/v1/turns/' + result['turn_id'])).json()
            if turn['status'] not in ('queued', 'working'):
                return turn
            await asyncio.sleep(0.1)
    raise TimeoutError('chat turn')


async def test_mcp_discovery_auth_and_readonly(stack):
    gateway = MCPGateway(stack.config)
    assert 'search_knowledge' in {t.name for t in await gateway.discover('rag')}
    assert 'apply_plan' in {t.name for t in await gateway.discover('campus')}
    result = await gateway.call('search_knowledge', {'query': '报到材料', 'campus_id': 'mingde', 'as_of_date': '2026-09-05'})
    assert result['data']['evidence']
    denied = await gateway.call('apply_plan', {'snapshot': {}, 'operation_id': 'forbidden'})
    assert denied['error']['code'] == 'forbidden'
    async with httpx.AsyncClient() as client:
        assert (await client.post(stack.config.campus_url, json={})).status_code == 401


async def test_a2a_input_required_resume_artifact(stack):
    client = PlannerClient(stack.config)
    request = PlanRequest(session_id='a2a-test', request='安排入学')
    first = await poll_task(client, await client.submit(request.model_dump(mode='json')))
    assert first['status']['state'] == 'input-required'
    request.request = '周六下午两点到明德校区，请安排报到入住'
    resumed = await client.submit(request.model_dump(mode='json'), first['id'], first['contextId'])
    result = await poll_task(client, resumed)
    assert result['id'] == first['id'] and result['contextId'] == first['contextId']
    assert result['status']['state'] == 'completed', result
    artifact = task_artifact(result)
    plan = Plan.model_validate(artifact['plan'])
    assert plan.items
    assert all(t['protocol'] == 'MCP' for t in artifact['trace'])
    data = build_seed()
    assert not validate_plan(plan, data['services'], data['travel'], [])


async def test_a2a_cancel_waiting_task(stack):
    client = PlannerClient(stack.config)
    request = PlanRequest(session_id='cancel-test', request='请安排入学')
    task = await poll_task(client, await client.submit(request.model_dump(mode='json')))
    assert task['status']['state'] == 'input-required'
    canceled = await client.cancel(task['id'])
    assert canceled['status']['state'] == 'canceled'
    assert (await client.get(task['id']))['status']['state'] == 'canceled'


async def test_simple_question_does_not_delegate(stack):
    turn = await chat(stack, '明德校区报到需要什么材料？')
    assert turn['status'] == 'completed' and turn['citations']
    assert all(t['protocol'] != 'A2A' for t in turn['trace'])


async def test_unknown_fee_does_not_invent_amount(stack):
    turn = await chat(stack, '明德校区我的专业一年学费多少钱？')
    assert '没有给出个人学费金额' in turn['reply']
    assert not any(s in turn['reply'] for s in ('5000元', '6000元'))


async def test_end_to_end_confirmation_and_restart(stack):
    gateway = MCPGateway(stack.config)
    before = await gateway.call('list_todos', {})
    first = await chat(stack, '请帮我安排报到入住')
    assert '请补充' in first['reply']
    turn = await chat(stack, '周六下午两点到明德校区，还没缴费。', first['session_id'])
    assert turn['plan_id'] and turn['pending_actions'], turn
    assert (await gateway.call('list_todos', {})) == before
    action = turn['pending_actions'][0]
    async with httpx.AsyncClient(timeout=20) as client:
        saved = (await client.post(stack.base + '/v1/actions/' + action + '/confirm')).json()
        repeated = (await client.post(stack.base + '/v1/actions/' + action + '/confirm')).json()
        assert saved['ok'] and repeated == saved
        calendar = (await client.post(stack.base + '/v1/calendar/propose-sync',
                    json={'session_id': turn['session_id']})).json()
        assert calendar['error']['code'] == 'not_connected'
        ics = await client.get(stack.base + '/v1/calendar.ics')
        assert Calendar.from_ical(ics.text).walk('VEVENT')
    old = await gateway.call('list_todos', {})
    stack.stop('tools')
    stack.start('tools')
    stack.stop('app')
    stack.start('app')
    assert await gateway.call('list_todos', {}) == old
    async with httpx.AsyncClient(timeout=20) as client:
        assert (await client.get(stack.base + '/v1/plans/' + turn['plan_id'])).status_code == 200
        assert (await client.post(stack.base + '/v1/actions/' + action + '/confirm')).json() == saved


async def test_stale_and_rejected_actions(stack):
    engine = Assistant(stack.config)
    # Service-independent verification of the confirmation gate; uses real MCP if it were to write.
    sid = 'action-test'
    engine.db.put('sessions', sid, {'id': sid, 'messages': [], 'evidence': {}})
    engine.db.put('plans', 'revise', {'plan_id': 'revise', 'version': 2})
    action = engine.propose_action(sid, 'apply_plan', {'snapshot': {}}, plan_id='revise', plan_version=1)
    assert (await engine.confirm(action['id']))['error']['code'] == 'conflict'
    action = engine.propose_action(sid, 'apply_plan', {'snapshot': {}})
    assert (await engine.reject(action['id']))['ok']
    assert not (await engine.confirm(action['id']))['ok']


async def test_planner_failure_does_not_break_rag(stack):
    stack.stop('planner')
    try:
        turn = await chat(stack, '明德校区报到材料有哪些？')
        assert turn['status'] == 'completed' and turn['citations']
        turn = await chat(stack, '周六下午两点到明德校区，请安排入学')
        assert '规划服务调用失败' in turn['reply']
        assert not turn['pending_actions']
    finally:
        stack.start('planner')


async def test_active_task_can_be_canceled(stack):
    client = PlannerClient(stack.config)
    request = PlanRequest(session_id='active-cancel', request='安排报到入住',
                          campus_id='mingde', arrival='2026-09-05T14:00:00+08:00')
    task = await client.submit(request.model_dump(mode='json'))
    if task['status']['state'] in ('submitted', 'working'):
        canceled = await client.cancel(task['id'])
        assert canceled['status']['state'] == 'canceled'
        await asyncio.sleep(0.2)
        assert (await client.get(task['id']))['status']['state'] == 'canceled'
    else:
        pytest.fail('Task completed before cancellation could be exercised')


async def test_waiting_a2a_task_survives_planner_restart(stack):
    client = PlannerClient(stack.config)
    request = PlanRequest(session_id='restart-input', request='帮我安排入学', completed=['payment'])
    task = await poll_task(client, await client.submit(request.model_dump(mode='json')))
    assert task['status']['state'] == 'input-required'
    stack.stop('planner')
    stack.start('planner')
    assert (await client.get(task['id']))['status']['state'] == 'input-required'
    request.request = '周六下午两点到明德校区'
    result = await poll_task(client, await client.submit(request.model_dump(mode='json'), task['id'], task['contextId']))
    assert result['status']['state'] == 'completed'
    assert all(i['service_id'] != 'payment' for i in task_artifact(result)['plan']['items'])


async def test_interrupted_turn_is_not_silently_reexecuted(stack):
    db = Store(stack.config.runtime / 'assistant.sqlite')
    db.put('turns', 'interrupted-test', {'id': 'interrupted-test', 'session_id': 'crash',
                                      'status': 'working', 'reply': ''})
    stack.stop('app')
    stack.start('app')
    assert db.get('turns', 'interrupted-test')['status'] == 'interrupted'


async def test_planner_restart_survives_in_flight_task_with_null_metadata(stack):
    """规划服务重启时,残留的 submitted/working 任务必须就地标记为失败。

    回归背景: A2A 序列化未设置的 metadata 会得到 null(键存在、值为 None),
    而恢复代码原先用 setdefault('metadata', {}) 兜底 —— setdefault 只在键缺失时生效,
    于是对 null 执行下标赋值抛 TypeError, 规划服务启动即崩溃且每次都崩。
    """
    db = Store(stack.config.runtime / 'planner.sqlite')
    db.put('a2a_tasks', 'interrupted-task', {
        'id': 'interrupted-task', 'kind': 'task', 'contextId': 'ctx',
        'status': {'state': 'working'}, 'metadata': None, 'artifacts': [], 'history': []})
    stack.stop('planner')
    stack.start('planner')
    value = db.get('a2a_tasks', 'interrupted-task')
    assert value['status']['state'] == 'failed'
    assert value['metadata']['interrupted'] is True


async def test_mcp_down_returns_observable_failure(stack):
    stack.stop('tools')
    try:
        result = await MCPGateway(stack.config).call('list_todos', {})
        assert not result['ok'] and result['error']['code'] == 'unavailable'
    finally:
        stack.start('tools')


async def test_planner_down_keeps_chat_and_queries_working(stack):
    """规划服务不可用时应部分可用:知识问答与事项查询走 RAG/校园 MCP,不受规划服务影响。"""
    stack.stop('planner')
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            health = (await client.get(stack.base + '/health')).json()
        assert health['status'] == 'degraded'
        assert health['dependencies'] == {'rag': True, 'campus': True, 'planner': False}

        knowledge = await chat(stack, '明德校区报到需要带什么材料？', 'degradation-knowledge')
        assert knowledge['status'] == 'completed'
        assert knowledge['reply']

        todos = await chat(stack, '我现在有哪些待办事项？', 'degradation-todos')
        assert todos['status'] == 'completed'
        assert todos['reply']
    finally:
        stack.start('planner')


async def test_planner_down_planning_request_fails_readably(stack):
    """规划服务不可用时,排程请求必须给出可读失败话术,而不是中断会话或编造计划。"""
    stack.stop('planner')
    try:
        turn = await chat(stack, '周六下午两点到明德校区，帮我安排报到和入住。', 'degradation-plan')
        assert turn['status'] == 'completed'
        assert '规划服务调用失败' in turn['reply']
        assert '仍可咨询规定或查询已有事项' in turn['reply']
    finally:
        stack.start('planner')


async def test_external_tools_real_mcp_discovery_and_unconfigured_state(stack):
    from campus_agent.integrations.tools import READ_INTEGRATIONS
    gateway = MCPGateway(stack.config)
    discovered = {t.name for t in await gateway.discover('campus')}
    assert READ_INTEGRATIONS | {'sync_plan_calendar'} <= discovered
    result = await gateway.call('get_integration_status', {})
    assert result['data']['maps_weather']['status'] == 'not_configured'
    assert result['data']['calendar']['status'] == 'not_connected'
    cases = [('get_weather_forecast', {'city_adcode': '110101'}, 'not_configured'),
             ('get_route', {'origin': '116.4,39.9', 'destination': '116.41,39.91'}, 'not_configured'),
             ('search_school_notices', {'query': '开学通知'}, 'not_connected'),
             ('get_timetable', {'start_date': '2026-09-05', 'end_date': '2026-09-12'}, 'not_connected'),
             ('list_external_calendar_events', {'start': '2026-09-05T00:00:00+08:00',
                                               'end': '2026-09-12T00:00:00+08:00'}, 'not_connected')]
    for name, args, code in cases:
        assert (await gateway.call(name, args))['error']['code'] == code
    denied = await gateway.call('sync_plan_calendar', {'plan_id': 'x', 'plan_version': 1,
        'provider_binding': 'x', 'reminder_minutes': 30, 'operation_id': 'forbidden'})
    assert denied['error']['code'] == 'forbidden'


async def test_fixture_weather_calls_real_mcp_without_fabricated_forecast(stack):
    turn = await chat(stack, '查询110101的天气')
    assert turn['status'] == 'completed'
    assert '未配置' in turn['reply'] and '确定性测试模式' in turn['reply']
    assert any(t.get('tool') == 'get_weather_forecast' for t in turn['trace'])
    assert not turn['pending_actions']


async def test_external_api_validation_and_calendar_no_platform(stack):
    async with httpx.AsyncClient() as client:
        result = (await client.get(stack.base + '/v1/integrations')).json()
        assert result['data']['calendar']['status'] == 'not_connected'
        invalid = await client.post(stack.base + '/v1/maps/route', json={'origin': 'nan,0', 'destination': '116,30'})
        assert invalid.status_code == 422
        weather = (await client.post(stack.base + '/v1/weather', json={'city_adcode': '110101'})).json()
        assert weather['error']['code'] == 'not_configured'
