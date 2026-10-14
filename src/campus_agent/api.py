from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .assistant import Assistant
from .access import COOKIE, PilotMiddleware, owned, request_user
from .config import Settings
from .models import ChatRequest
from .protocols import MCPGateway
from .integrations.maps import PlaceQuery, RouteQuery, WeatherQuery


class TodoProposal(BaseModel):
    session_id: str
    todo_id: str
    status: str
    expected_version: int


class CalendarProposal(BaseModel):
    session_id: str
    reminder_minutes: int = Field(default=30, ge=0, le=10080)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=256)


class FeedbackRequest(BaseModel):
    turn_id: str
    rating: str = Field(pattern='^(helpful|unhelpful)$')
    comment: str = Field(default='', max_length=2000)


LOGIN_HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CampusPilot · 登录</title><style>
body{font-family:system-ui;background:#f3f7f6;margin:0;padding:48px 20px;color:#163b36}
main{max-width:400px;margin:auto;background:white;padding:32px;border-radius:20px}
input,button{display:block;box-sizing:border-box;width:100%;padding:12px;margin:12px 0;font:inherit}
button{background:#16776c;color:white;border:0;border-radius:8px;cursor:pointer}
small{color:#526460;line-height:1.8}a{color:#16776c}</style>
<main><h1>CampusPilot</h1><p>新生入学事务助手 · 邀请体验</p>
<form method="post" action="/login">
<label>账号<input name="username" autocomplete="username" required maxlength="40"></label>
<label>密码<input name="password" type="password" autocomplete="current-password" required maxlength="256"></label>
<button type="submit">登录工作台</button></form>
<small>使用公开资料辅助整理办理事项，个人报到安排以学校通知为准。账号由组织者分配。
体验时请勿提交身份证、录取通知书或其他敏感材料。</small></main></html>'''


def create_app(config: Settings, with_ui=True):
    engine = Assistant(config)

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks = list(engine.jobs)
        for job in tasks:
            job.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title='CampusPilot · 新生入学事务助手', version='0.3.1', lifespan=lifespan)
    app.state.engine = engine
    app.add_middleware(PilotMiddleware, config=config, accounts=engine.accounts)

    def require(kind, key, request):
        return owned(engine, kind, key, request_user(request, config))

    def login_response(token):
        response = RedirectResponse('/ui/', status_code=303)
        response.set_cookie(COOKIE, token, max_age=config.login_ttl_hours * 3600,
                            httponly=True, secure=config.cookie_secure, samesite='strict', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/')
    async def home():
        return RedirectResponse('/ui/', status_code=303)

    @app.get('/login', response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse(LOGIN_HTML, headers={'Cache-Control': 'no-store'})

    @app.post('/login')
    async def login_form(request: Request):
        if config.auth_mode != 'pilot':
            return RedirectResponse('/ui/', status_code=303)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 4096:
                raise HTTPException(413, '登录请求过大')
        try:
            values = parse_qs(body.decode('utf-8'), max_num_fields=4)
            credentials = LoginRequest(username=values.get('username', [''])[0],
                                       password=values.get('password', [''])[0])
        except ValueError:
            raise HTTPException(422, '请填写有效账号与密码')
        token = await engine.accounts.login(credentials.username, credentials.password,
                                            request.client.host if request.client else 'unknown')
        return login_response(token)

    @app.post('/v1/auth/login')
    async def login(credentials: LoginRequest, request: Request):
        if config.auth_mode != 'pilot':
            raise HTTPException(400, '演示模式无需登录')
        token = await engine.accounts.login(credentials.username, credentials.password,
                                            request.client.host if request.client else 'unknown')
        return login_response(token)

    @app.post('/v1/auth/logout')
    async def logout(request: Request):
        engine.accounts.revoke(getattr(request.state, 'login_token', ''))
        response = RedirectResponse('/login', status_code=303)
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.get('/v1/me')
    async def me(request: Request):
        uid = request_user(request, config)
        sessions = [s for s in engine.db.all('sessions') if s.get('user_id', 'demo-user') == uid]
        turns = [t for t in engine.db.all('turns') if t.get('user_id', 'demo-user') == uid]
        return {'user_id': uid, 'auth_mode': config.auth_mode, 'synthetic_data': config.data_profile == 'synthetic',
                'data_profile': config.data_profile,
                'sessions': [{'id': s['id']} for s in sessions], 'turn_count': len(turns),
                'limits': {'turns_per_hour': config.turns_per_hour, 'pending_per_user': config.max_pending_per_user}}

    @app.get('/health')
    async def health():
        async def probe(name, url):
            try:
                async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                    response = await client.get(url)
                    return name, response.status_code == 200
            except Exception:
                return name, False
        states = await asyncio.gather(probe('rag', config.rag_url.rsplit('/mcp', 1)[0] + '/health'),
            probe('campus', config.campus_url.rsplit('/mcp', 1)[0] + '/health'),
            probe('planner', config.planner_url + '/health'))
        return {'status': 'ok' if all(v for _, v in states) else 'degraded', 'dependencies': dict(states),
                'product': 'CampusPilot', 'auth_mode': config.auth_mode,
                'model_mode': config.mode, 'model': config.model, 'synthetic_data': config.data_profile == 'synthetic',
                'data_profile': config.data_profile, 'source_snapshot_date': '2026-10-11' if config.data_profile == 'whut-2026' else None,
                'source_scope': '2026级本科' if config.data_profile == 'whut-2026' else 'synthetic',
                'demo_date': config.demo_date}

    @app.post('/v1/chat', status_code=202)
    async def chat(body: ChatRequest, request: Request):
        turn = engine.enqueue(body.message, body.session_id, request_user(request, config))
        return {'session_id': turn['session_id'], 'turn_id': turn['id'], 'status': turn['status']}

    @app.get('/v1/turns/{turn_id}')
    async def get_turn(turn_id: str, request: Request):
        return require('turns', turn_id, request)

    @app.get('/v1/sessions/{session_id}')
    async def get_session(session_id: str, request: Request):
        require('sessions', session_id, request)
        return {'session_id': session_id, 'turns': [t for t in engine.db.all('turns') if t['session_id'] == session_id]}

    @app.get('/v1/plans/{plan_id}')
    async def get_plan(plan_id: str, request: Request):
        return require('plans', plan_id, request)

    @app.get('/v1/actions/{action_id}')
    async def get_action(action_id: str, request: Request):
        return require('actions', action_id, request)

    @app.post('/v1/actions/{action_id}/confirm')
    async def confirm(action_id: str, request: Request):
        require('actions', action_id, request)
        return await engine.confirm(action_id)

    @app.post('/v1/actions/{action_id}/reject')
    async def reject(action_id: str, request: Request):
        require('actions', action_id, request)
        return await engine.reject(action_id)

    @app.post('/v1/todos/propose-update')
    async def update_todo(body: TodoProposal, request: Request):
        require('sessions', body.session_id, request)
        if body.status not in {'pending', 'completed'}:
            raise HTTPException(422, 'invalid todo status')
        result = await MCPGateway(config).call('list_todos', {'user_id': request_user(request, config)})
        if not result.get('ok'):
            raise HTTPException(503, '待办服务暂不可用')
        if not any(t['id'] == body.todo_id for t in result['data']):
            raise HTTPException(404, '待办不存在')
        return engine.propose_action(body.session_id, 'update_todo_status',
            body.model_dump(exclude={'session_id'}))

    @app.post('/v1/planning-tasks/{task_id}/cancel')
    async def cancel(task_id: str, request: Request):
        require('task_owners', task_id, request)
        return await engine.planner.cancel(task_id)

    @app.get('/v1/todos')
    async def todos(request: Request):
        return await MCPGateway(config).call('list_todos', {'user_id': request_user(request, config)})

    @app.get('/v1/integrations')
    async def integrations():
        return await MCPGateway(config).call('get_integration_status', {})

    @app.post('/v1/maps/places')
    async def places(request: PlaceQuery):
        return await MCPGateway(config).call('search_places', request.model_dump())

    @app.post('/v1/maps/route')
    async def route(request: RouteQuery):
        return await MCPGateway(config).call('get_route', request.model_dump())

    @app.post('/v1/weather')
    async def weather(request: WeatherQuery):
        return await MCPGateway(config).call('get_weather_forecast', request.model_dump(mode='json'))

    @app.post('/v1/calendar/propose-sync')
    async def propose_sync(body: CalendarProposal, request: Request):
        require('sessions', body.session_id, request)
        return await engine.propose_calendar_sync(body.session_id, body.reminder_minutes)

    @app.get('/v1/calendar/external')
    async def external_calendar(start: str, end: str):
        if config.auth_mode == 'pilot':
            raise HTTPException(409, '试用版尚未绑定个人日历')
        return await MCPGateway(config).call('list_external_calendar_events', {'start': start, 'end': end})

    @app.get('/v1/calendar')
    async def calendar(request: Request):
        return await MCPGateway(config).call('list_calendar_events', {'user_id': request_user(request, config)})

    @app.get('/v1/calendar.ics')
    async def export(request: Request):
        result = await MCPGateway(config).call('export_calendar', {'user_id': request_user(request, config)})
        if not result.get('ok'):
            raise HTTPException(503, result['error'])
        return Response(result['data']['content'], media_type='text/calendar',
                        headers={'Content-Disposition': 'attachment; filename="campuspilot-calendar.ics"',
                                 'Cache-Control': 'private, no-store'})

    @app.post('/v1/feedback', status_code=201)
    async def feedback(body: FeedbackRequest, request: Request):
        require('turns', body.turn_id, request)
        uid = request_user(request, config)
        key = uid + ':' + body.turn_id
        old = engine.db.get('feedback', key)
        record = {'id': old['id'] if old else str(uuid.uuid4()), 'user_id': uid,
                  **body.model_dump(), 'updated_at': time.time()}
        engine.db.put('feedback', key, record)
        return record

    @app.get('/v1/evidence/{evidence_id}')
    async def evidence(evidence_id: str):
        # Only allow enumerated evidence IDs; no user-controlled file paths.
        from .seed import load_seed
        data = load_seed(config.runtime / 'data', config.data_profile)
        doc = next((d for d in data['documents'] if d['evidence_id'] == evidence_id), None)
        if not doc:
            raise HTTPException(404, 'evidence not found')
        return doc

    @app.get('/v1/sources')
    async def sources():
        from .seed import load_seed
        data = load_seed(config.runtime / 'data', config.data_profile)
        return {'data_profile': config.data_profile, 'school': data.get('school_name'),
                'cohort_year': data.get('cohort_year'), 'student_type': data.get('student_type'),
                'snapshot_date': data.get('fetched_at'), 'exclusions': data.get('exclusions', []),
                'sources': [{k: d.get(k) for k in ('evidence_id', 'title', 'source_url',
                             'snapshot_sha256', 'fetched_at', 'source_notes')} for d in data['documents']]}

    if with_ui:
        import gradio as gr
        from .ui import build_ui
        app = gr.mount_gradio_app(app, build_ui(engine), path='/ui',
                                  auth_dependency=lambda request: request_user(request, config),
                                  blocked_paths=[str(config.runtime.resolve())])
    return app
