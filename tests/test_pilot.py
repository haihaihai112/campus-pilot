import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from icalendar import Calendar

from campus_agent.access import Accounts, COOKIE
from campus_agent.api import create_app
from campus_agent.assistant import Assistant
from campus_agent.config import Settings
from campus_agent.llm import tool_message
from campus_agent.metrics import pilot_stats
from conftest import Stack

PASSWORD = 'test-only-long-password'


def pilot_config(root, **kwargs):
    return Settings(runtime=root, mode='fixture', auth_mode='pilot', cookie_secure=False,
                    read_token='test-read-' + 'r' * 32, write_token='test-write-' + 'w' * 32,
                    planner_token='test-planner-' + 'p' * 32, **kwargs)


async def sign_in(client, username):
    response = await client.post('/v1/auth/login', json={'username': username, 'password': PASSWORD})
    assert response.status_code == 303, response.text
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'samesite=strict' in response.headers['set-cookie'].lower()


@pytest.fixture
def pilot_app(tmp_path):
    app = create_app(pilot_config(tmp_path), with_ui=False)
    for username in ('alice', 'bob'):
        app.state.engine.accounts.create(username, PASSWORD)
    return app


async def test_login_required_and_cookie_logout(pilot_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=pilot_app), base_url='http://test') as client:
        assert (await client.get('/v1/me')).status_code == 401
        assert (await client.get('/docs')).status_code == 401
        assert (await client.get('/ui/')).status_code == 303
        assert (await client.get('/login')).status_code == 200
        wrong = await client.post('/v1/auth/login', json={'username': 'alice', 'password': 'wrong'})
        assert wrong.status_code == 401
        await sign_in(client, 'alice')
        token = client.cookies.get(COOKIE)
        assert (await client.get('/v1/me')).json()['user_id'].startswith('pilot-')
        assert (await client.post('/v1/auth/logout')).status_code == 303
        assert (await client.get('/v1/me', headers={'Authorization': 'Bearer ' + token})).status_code == 401


async def test_login_tokens_hashed_expire_and_disable(tmp_path):
    accounts = Accounts(pilot_config(tmp_path))
    uid = accounts.create('alice', PASSWORD)
    token = await accounts.login('alice', PASSWORD, '127.0.0.1')
    assert accounts.resolve(token) == uid
    assert Accounts(pilot_config(tmp_path)).resolve(token) == uid
    with accounts.db.connect() as con:
        row = con.execute('SELECT * FROM accounts').fetchone()
        assert row['password_hash'] != PASSWORD and row['salt']
        assert token not in str(dict(con.execute('SELECT * FROM logins').fetchone()))
        con.execute('UPDATE logins SET expires_at=?', (time.time() - 1,))
    assert accounts.resolve(token) is None
    token = await accounts.login('alice', PASSWORD, '127.0.0.1')
    accounts.disable('alice')
    assert accounts.resolve(token) is None
    with pytest.raises(HTTPException) as exc:
        await accounts.login('alice', PASSWORD, '127.0.0.1')
    assert exc.value.status_code == 401


async def test_cross_origin_writes_and_login_rejected(pilot_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=pilot_app), base_url='http://test') as client:
        attack = {'Origin': 'https://other.example', 'Sec-Fetch-Site': 'cross-site'}
        assert (await client.post('/v1/auth/login', json={'username': 'alice', 'password': PASSWORD},
                                  headers=attack)).status_code == 403
        await sign_in(client, 'alice')
        assert (await client.post('/v1/chat', json={'message': 'hello'}, headers=attack)).status_code == 403
        assert not pilot_app.state.engine.db.all('turns')


async def test_all_record_routes_enforce_ownership(pilot_app):
    engine = pilot_app.state.engine
    uid = engine.accounts.listing()[0]['user_id']
    engine.db.put('sessions', 'alice-session', {'id': 'alice-session', 'user_id': uid, 'messages': []})
    for kind, key in [('turns', 'alice-turn'), ('plans', 'alice-plan'), ('actions', 'alice-action'),
                      ('task_owners', 'alice-task')]:
        engine.db.put(kind, key, {'id': key, 'user_id': uid, 'session_id': 'alice-session'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=pilot_app), base_url='http://test') as client:
        await sign_in(client, 'bob')
        for route in ('sessions/alice-session', 'turns/alice-turn', 'plans/alice-plan', 'actions/alice-action'):
            assert (await client.get('/v1/' + route)).status_code == 404
        for route in ('actions/alice-action/confirm', 'actions/alice-action/reject',
                      'planning-tasks/alice-task/cancel'):
            assert (await client.post('/v1/' + route)).status_code == 404
        assert (await client.post('/v1/chat', json={'message': 'hello', 'session_id': 'alice-session'})).status_code == 404
        assert (await client.post('/v1/feedback', json={'turn_id': 'alice-turn', 'rating': 'helpful'})).status_code == 404
        assert (await client.post('/v1/calendar/propose-sync', json={'session_id': 'alice-session'})).status_code == 404
        assert (await client.post('/v1/todos/propose-update', json={'session_id': 'alice-session',
                    'todo_id': 'x', 'status': 'completed', 'expected_version': 1})).status_code == 404
        assert len(engine.db.all('turns')) == 1


async def test_model_cannot_select_another_user(tmp_path, monkeypatch):
    engine = Assistant(pilot_config(tmp_path))
    engine.tool_schemas = [{'function': {'name': 'list_todos'}}]
    seen = []

    async def call(self, name, args):
        seen.append(args)
        return {'ok': True, 'data': [], 'error': None}

    monkeypatch.setattr('campus_agent.assistant.MCPGateway.call', call)
    engine.db.put('turns', 'test', {'id': 'test'})
    state = {'messages': [tool_message('list_todos', {'user_id': 'bob'})], 'user_id': 'alice',
             'session_id': 'sid', 'turn_id': 'test', 'calls': 0, 'searches': 0,
             'trace': [], 'usage': {}, 'evidence': {}, 'pending_actions': []}
    await engine._tools(state)
    assert seen == [{'user_id': 'alice'}]


async def test_http_limits_and_persistent_counters(tmp_path):
    config = pilot_config(tmp_path, requests_per_minute=1)
    app = create_app(config, with_ui=False)
    app.state.engine.accounts.create('alice', PASSWORD)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        await sign_in(client, 'alice')
        uid = (await client.get('/v1/me')).json()['user_id']
        response = await client.get('/v1/me')
        assert response.status_code == 429 and response.headers['Retry-After']
    with pytest.raises(HTTPException):
        Accounts(config).consume('http', uid, 1, 60)


async def test_login_attempts_limited_before_hashing(tmp_path):
    accounts = Accounts(pilot_config(tmp_path))
    for _ in range(10):
        accounts.consume('login-ip', 'ip', 10, 60)
    with pytest.raises(HTTPException) as exc:
        await accounts.login('alice', 'wrong', 'ip')
    assert exc.value.status_code == 429


async def test_chat_hourly_limit_and_pending_admission(tmp_path, monkeypatch):
    engine = Assistant(pilot_config(tmp_path, turns_per_hour=1))

    async def dormant(turn_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, 'run_turn', dormant)
    turn = engine.enqueue('first', user_id='alice')
    with pytest.raises(HTTPException) as exc:
        engine.enqueue('second', turn['session_id'], 'alice')
    assert exc.value.status_code == 429
    assert len(engine.db.all('turns')) == 1
    for job in engine.jobs:
        job.cancel()
    await asyncio.gather(*engine.jobs, return_exceptions=True)


async def test_global_pending_limit_prevents_unbounded_jobs(tmp_path, monkeypatch):
    engine = Assistant(pilot_config(tmp_path, max_pending_turns=1))

    async def dormant(turn_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, 'run_turn', dormant)
    engine.enqueue('first', user_id='alice')
    with pytest.raises(HTTPException) as exc:
        engine.enqueue('second', user_id='bob')
    assert exc.value.status_code == 429
    for job in engine.jobs:
        job.cancel()
    await asyncio.gather(*engine.jobs, return_exceptions=True)


async def test_per_user_pending_limit_does_not_block_another_user(tmp_path, monkeypatch):
    engine = Assistant(pilot_config(tmp_path, max_pending_per_user=1))

    async def dormant(turn_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, 'run_turn', dormant)
    engine.enqueue('first', user_id='alice')
    with pytest.raises(HTTPException):
        engine.enqueue('second', user_id='alice')
    engine.enqueue('other user', user_id='bob')
    assert len(engine.db.all('turns')) == 2
    for job in engine.jobs:
        job.cancel()
    await asyncio.gather(*engine.jobs, return_exceptions=True)


async def test_active_turn_semaphore_limits_processing(tmp_path, monkeypatch):
    engine = Assistant(pilot_config(tmp_path, max_active_turns=2, max_pending_turns=10))
    active, maximum = 0, 0
    release = asyncio.Event()

    async def discover():
        pass

    async def invoke(state, config):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await release.wait()
        active -= 1
        return {**state, 'reply': 'test synchronization only'}

    monkeypatch.setattr(engine, 'discover_tools', discover)
    monkeypatch.setattr(engine.graph, 'ainvoke', invoke)
    for index in range(5):
        engine.enqueue('test concurrency', user_id=f'user-{index}')
    for _ in range(50):
        if active == 2:
            break
        await asyncio.sleep(.01)
    assert active == 2
    assert sum(t['status'] == 'working' for t in engine.db.all('turns')) == 2
    release.set()
    await asyncio.gather(*list(engine.jobs))
    assert maximum == 2
    assert all(t['status'] == 'completed' for t in engine.db.all('turns'))


async def test_feedback_updates_one_record_and_metrics_separate_modes(pilot_app):
    engine = pilot_app.state.engine
    uid = engine.accounts.listing()[0]['user_id']
    engine.db.put('turns', 't1', {'id': 't1', 'user_id': uid, 'model_mode': 'fixture',
                               'status': 'completed', 'latency_ms': 20, 'usage': {'total_tokens': 0}})
    engine.db.put('turns', 't2', {'id': 't2', 'user_id': uid, 'model_mode': 'live', 'status': 'failed'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=pilot_app), base_url='http://test') as client:
        await sign_in(client, 'alice')
        first = (await client.post('/v1/feedback', json={'turn_id': 't1', 'rating': 'helpful'})).json()
        second = (await client.post('/v1/feedback', json={'turn_id': 't1', 'rating': 'unhelpful'})).json()
        assert first['id'] == second['id']
    report = pilot_stats(engine.config)
    assert report['model_modes']['fixture']['feedback_records'] == {'unhelpful': 1}
    assert report['model_modes']['live']['turn_states'] == {'failed': 1}
    assert len(engine.db.all('feedback')) == 1


def test_pilot_refuses_demo_service_credentials(tmp_path):
    with pytest.raises(ValueError, match='private MCP/A2A'):
        Settings(runtime=tmp_path, mode='fixture', auth_mode='pilot')


@pytest.fixture(scope='module')
def pilot_stack(tmp_path_factory):
    stack = Stack(tmp_path_factory.mktemp('pilot-stack'), auth_mode='pilot', with_ui=True)
    for username in ('alice', 'bob'):
        Accounts(stack.config).create(username, PASSWORD)
    try:
        for kind in ('rag', 'tools', 'planner', 'app'):
            stack.start(kind)
        yield stack
    finally:
        for kind in ('app', 'planner', 'tools', 'rag'):
            stack.stop(kind)


async def poll_turn(client, turn_id):
    for _ in range(300):
        response = await client.get('/v1/turns/' + turn_id)
        assert response.status_code == 200, response.text
        turn = response.json()
        if turn['status'] not in {'queued', 'working'}:
            return turn
        await asyncio.sleep(.1)
    raise TimeoutError(turn_id)


@pytest.mark.integration
async def test_two_user_real_protocol_plan_save_export_restart(pilot_stack):
    async with httpx.AsyncClient(base_url=pilot_stack.base, timeout=30, trust_env=False) as alice, \
               httpx.AsyncClient(base_url=pilot_stack.base, timeout=30, trust_env=False) as bob:
        assert (await alice.get('/ui/config')).status_code == 401
        await sign_in(alice, 'alice')
        await sign_in(bob, 'bob')
        assert (await alice.get('/ui/')).status_code == 200
        assert 'CampusPilot' in (await alice.get('/ui/config')).text
        uid_a, uid_b = (await alice.get('/v1/me')).json()['user_id'], (await bob.get('/v1/me')).json()['user_id']
        assert uid_a != uid_b
        created = (await alice.post('/v1/chat', json={'message': '周六下午两点到明德校区，帮我安排报到和入住'})).json()
        turn = await poll_turn(alice, created['turn_id'])
        assert turn['status'] == 'completed' and turn['plan_id'], turn
        assert any(t['protocol'] == 'A2A' for t in turn['trace'])
        plan = (await alice.get('/v1/plans/' + turn['plan_id'])).json()
        assert plan['user_id'] == uid_a
        assert (await alice.get('/v1/todos')).json()['data'] == []
        action = turn['pending_actions'][0]
        for path in ('/v1/turns/' + turn['id'], '/v1/plans/' + turn['plan_id'],
                     '/v1/sessions/' + turn['session_id'], '/v1/actions/' + action):
            assert (await bob.get(path)).status_code == 404
        assert (await bob.post('/v1/actions/' + action + '/confirm')).status_code == 404
        assert (await bob.post('/v1/planning-tasks/' + turn['a2a_task_id'] + '/cancel')).status_code == 404
        saved = (await alice.post('/v1/actions/' + action + '/confirm')).json()
        repeated = (await alice.post('/v1/actions/' + action + '/confirm')).json()
        assert saved['ok'] and saved == repeated
        todos = (await alice.get('/v1/todos')).json()['data']
        assert todos and all(t['user_id'] == uid_a for t in todos)
        assert (await bob.get('/v1/todos')).json()['data'] == []
        assert (await bob.get('/v1/calendar')).json()['data'] == []
        assert (await bob.get('/v1/todos', params={'user_id': uid_a})).json()['data'] == []
        assert not Calendar.from_ical((await bob.get('/v1/calendar.ics')).text).walk('VEVENT')
        assert len(Calendar.from_ical((await alice.get('/v1/calendar.ics')).text).walk('VEVENT')) == len(todos)
        bob_created = (await bob.post('/v1/chat', json={'message': '周六下午两点到明德校区，帮我安排报到和入住'})).json()
        bob_turn = await poll_turn(bob, bob_created['turn_id'])
        bob_plan = (await bob.get('/v1/plans/' + bob_turn['plan_id'])).json()
        assert bob_plan['user_id'] == uid_b
        assert [i['start'] for i in bob_plan['items']] == [i['start'] for i in plan['items']]
        assert (await bob.post('/v1/todos/propose-update', json={'session_id': bob_turn['session_id'],
                       'todo_id': todos[0]['id'], 'status': 'completed', 'expected_version': 1})).status_code == 404
        assert (await bob.post('/v1/actions/' + bob_turn['pending_actions'][0] + '/confirm')).json()['ok']
        bob_todos = (await bob.get('/v1/todos')).json()['data']
        assert bob_todos and all(t['user_id'] == uid_b for t in bob_todos)
        pilot_stack.stop('app')
        pilot_stack.start('app')
        assert (await alice.get('/v1/me')).json()['user_id'] == uid_a
        assert (await alice.get('/v1/todos')).json()['data'] == todos
        assert (await bob.get('/v1/todos')).json()['data'] == bob_todos
        assert (await alice.post('/v1/actions/' + action + '/confirm')).json() == saved


@pytest.mark.integration
async def test_ui_callbacks_reject_foreign_session(pilot_stack):
    # Exercise Gradio's mounted API, not just the FastAPI routes.
    async with httpx.AsyncClient(base_url=pilot_stack.base, timeout=30, trust_env=False) as alice, \
               httpx.AsyncClient(base_url=pilot_stack.base, timeout=30, trust_env=False) as bob:
        await sign_in(alice, 'alice')
        await sign_in(bob, 'bob')
        created = (await alice.post('/v1/chat', json={'message': '报到材料'})).json()
        await poll_turn(alice, created['turn_id'])
        response = await bob.post('/ui/gradio_api/call/restore', json={'data': [created['session_id']]})
        assert response.status_code == 200, response.text
        event_id = response.json()['event_id']
        stream = await bob.get('/ui/gradio_api/call/restore/' + event_id)
        assert 'event: error' in stream.text, stream.text
        assert 'event: complete' not in stream.text
        own = await alice.post('/ui/gradio_api/call/restore', json={'data': [created['session_id']]})
        own_stream = await alice.get('/ui/gradio_api/call/restore/' + own.json()['event_id'])
        assert 'event: complete' in own_stream.text
        payload = json.loads(next(line[6:] for line in own_stream.text.splitlines() if line.startswith('data: ')))
        assert payload[0][0]['content'] == '报到材料'
