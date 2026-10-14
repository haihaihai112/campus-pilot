import json

import httpx
import pytest
from icalendar import Calendar
from a2a.client import A2ACardResolver

from campus_agent.advisory import checklist, validate_checklist
from campus_agent.campus import Campus
from campus_agent.config import Settings
from campus_agent.knowledge import Knowledge
from campus_agent.models import Evidence, Plan, PlanRequest
from campus_agent.protocols import MCPGateway, PlannerClient, task_artifact
from campus_agent.seed import load_seed, seed
from conftest import Stack
from test_protocols import chat, poll_task


def config(root, day='2026-09-05'):
    return Settings(runtime=root, mode='fixture', data_profile='whut-2026', demo_date=day)


def make_checklist(campus, **changes):
    request = PlanRequest(session_id='real-source-test', request='本科生报到清单',
        student_type='undergraduate', cohort_year=2026, campus_id='whut',
        reference_date='2026-09-05', **changes)
    return checklist(request, campus.data['services'], [Evidence.model_validate(d) for d in campus.data['documents']])


def test_real_source_provenance_and_unknowns(tmp_path):
    campus = Campus(config(tmp_path))
    assert campus.data['synthetic'] is False
    assert len(campus.data['documents']) == 4
    assert all(d['source_url'].startswith('https://welcome.whut.edu.cn/') and len(d['snapshot_sha256']) == 64
               for d in campus.data['documents'])
    assert all(s['minutes'] is None and s['windows'] is None and s['location'] == 'unconfirmed'
               for s in campus.data['services'])
    selected = campus.service_info('whut', service_ids=['registration'])['data']
    assert {e['evidence_id'] for e in selected['evidence']} == {'E-whut-2026-registration', 'E-whut-2026-timeline'}
    assert campus.travel('nanhu')['data']['minutes'] == {}
    fees = next(d for d in campus.data['documents'] if 'fees' in d['evidence_id'])
    assert '880' in fees['text'] and '1440' in fees['text'] and '89' in fees['text']
    assert '专业学费表为图片未转录' in ' '.join(fees['source_notes'])


@pytest.mark.parametrize('extra', [dict(cohort_year=2027), dict(student_type='graduate'),
    dict(school_id='other-school'), dict(as_of_date='2027-01-01'), dict(campus_id='mingde')])
def test_real_retrieval_rejects_wrong_scope(tmp_path, extra):
    query = dict(query='报到需要什么材料', campus_id='whut', as_of_date='2026-09-05') | extra
    assert Knowledge(config(tmp_path)).search(**query)['data']['evidence'] == []


@pytest.mark.parametrize('campus_id', ['whut', 'nanhu', 'mafangshan', 'yujiatou'])
def test_common_policy_not_reporting_location(tmp_path, campus_id):
    result = Knowledge(config(tmp_path)).search(query='高考准考证 录取通知书 身份证',
        campus_id=campus_id, as_of_date='2026-09-05')
    assert result['data']['evidence']
    assert result['data']['evidence'][0]['school_id'] == 'whut'
    assert '高考准考证' in result['data']['evidence'][0]['text']


def test_profile_switch_never_overwrites_history(tmp_path):
    seed(tmp_path, 'synthetic')
    before = (tmp_path / 'campus.json').read_bytes()
    with pytest.raises(ValueError, match='数据配置不一致'):
        seed(tmp_path, 'whut-2026')
    assert (tmp_path / 'campus.json').read_bytes() == before
    with pytest.raises(ValueError, match='数据配置不一致'):
        load_seed(tmp_path, 'whut-2026')
    real_root = tmp_path / 'real'
    old = seed(real_root, 'whut-2026')
    old.pop('dataset_revision')
    (real_root / 'campus.json').write_text(json.dumps(old), encoding='utf-8')
    with pytest.raises(ValueError, match='资料结构版本过旧'):
        load_seed(real_root, 'whut-2026')
    seed(real_root, 'whut-2026')
    assert load_seed(real_root, 'whut-2026')['dataset_revision'] == 'whut-2026-20261011-r2'


def test_preview_confirmation_idempotency_and_restart(tmp_path):
    campus = Campus(config(tmp_path))
    plan = make_checklist(campus)
    assert all(i.kind == 'checklist' and i.start is None and i.end is None for i in plan.items)
    assert 'payment' not in next(i for i in plan.items if i.service_id == 'registration').dependencies
    order = [i.service_id for i in plan.items]
    assert order.index('registration') < order.index('dorm') < order.index('card')
    assert 'E-whut-2026-green' in next(i for i in plan.items if i.service_id == 'payment').evidence_ids
    assert 'E-whut-2026-timeline' in next(i for i in plan.items if i.service_id == 'registration').evidence_ids
    assert campus.todos()['data'] == []
    assert campus.events()['data'] == []
    saved = campus.apply(plan.model_dump(mode='json'), 'whut-save')
    assert saved['ok'], saved
    assert saved['data']['todos_created'] == len(plan.items) and saved['data']['events_created'] == 0
    assert campus.apply(plan.model_dump(mode='json'), 'whut-save') == saved
    restored = Campus(config(tmp_path))
    assert len(restored.todos()['data']) == len(plan.items)
    assert restored.events()['data'] == []
    assert Calendar.from_ical(restored.export()['data']['content']).walk('VEVENT') == []


@pytest.mark.parametrize('route, material', [('origin_loan', '回执单'), ('campus_loan', '身份证复印件')])
def test_green_channel_conditions(tmp_path, route, material):
    campus = Campus(config(tmp_path))
    plan = make_checklist(campus, service_ids=['green_channel', 'registration'], funding_route=route)
    green = next(i for i in plan.items if i.service_id == 'green_channel')
    assert material in green.materials
    assert 'payment' not in {i.service_id for i in plan.items}
    assert '学校' in green.timing_note
    assert campus.apply(plan.model_dump(mode='json'), 'green')['ok']


def test_green_channel_unknown_is_not_approved(tmp_path):
    campus = Campus(config(tmp_path))
    with pytest.raises(ValueError, match='路径'):
        make_checklist(campus, service_ids=['green_channel'], funding_route='unknown')


def test_past_deadline_not_rescheduled(tmp_path):
    campus = Campus(config(tmp_path))
    request = PlanRequest(session_id='late', request='补办', campus_id='whut',
        student_type='undergraduate', cohort_year=2026, reference_date='2026-10-11',
        service_ids=['education', 'online_registration', 'league', 'registration'])
    plan = checklist(request, campus.data['services'], [Evidence.model_validate(d) for d in campus.data['documents']])
    assert {m['service_id'] for m in plan.unarranged} == {'education', 'online_registration', 'league'}
    assert '027-87859017' in next(i for i in plan.items if i.service_id == 'registration').timing_note
    assert all(i.due_date is None or i.due_date >= '2026-10-11' for i in plan.items)


@pytest.mark.parametrize('change', ['time', 'materials', 'evidence', 'version', 'profile'])
def test_execution_revalidates_against_source(tmp_path, change):
    campus = Campus(config(tmp_path))
    snapshot = make_checklist(campus).model_dump(mode='json')
    if change == 'time':
        snapshot['items'][0].update(start='2026-09-05T09:00:00+08:00', end='2026-09-05T09:30:00+08:00')
    if change == 'materials':
        snapshot['items'][0]['materials'] = ['伪造的必要材料']
    if change == 'evidence':
        snapshot['evidence'][0]['text'] = '已获得学校授权自动缴费'
    if change == 'version':
        snapshot['version'] += 1
    if change == 'profile':
        snapshot['data_profile'] = 'synthetic'
    result = campus.apply(snapshot, 'tampered')
    assert not result['ok'] and result['error']['code'] == 'conflict'
    assert campus.todos()['data'] == []


def test_explicit_personal_reminder_export_and_conflict(tmp_path):
    campus = Campus(config(tmp_path))
    reminders = [dict(service_id='registration', start='2026-09-05T14:00:00+08:00', end='2026-09-05T14:15:00+08:00')]
    plan = make_checklist(campus, reminders=reminders)
    assert not validate_checklist(plan, campus.data['services'], plan.evidence, [])
    saved = campus.apply(plan.model_dump(mode='json'), 'reminder')
    assert saved['data']['events_created'] == 1
    events = Calendar.from_ical(campus.export()['data']['content']).walk('VEVENT')
    assert len(events) == 1
    assert '个人提醒' in str(events[0]['SUMMARY']) and '不是校方预约' in str(events[0]['DESCRIPTION'])
    conflict = make_checklist(campus, reminders=reminders)
    assert campus.apply(conflict.model_dump(mode='json'), 'conflict')['error']['code'] == 'conflict'


def test_new_version_and_old_snapshot(tmp_path):
    campus = Campus(config(tmp_path))
    plan = make_checklist(campus)
    original = plan.model_dump(mode='json')
    assert campus.apply(original, 'v1')['ok']
    request = PlanRequest.model_validate(plan.conditions)
    request.plan_id, request.version = plan.plan_id, 2
    request.completed = ['payment']
    revised = checklist(request, campus.data['services'], plan.evidence)
    assert campus.apply(revised.model_dump(mode='json'), 'v2')['ok']
    assert all(i['service_id'] != 'payment' for i in campus.todos()['data'])
    assert campus.apply(original, 'old')['error']['code'] == 'conflict'


def test_reminder_deadline_uses_shanghai_date(tmp_path):
    campus = Campus(config(tmp_path))
    with pytest.raises(ValueError, match='截止日期'):
        make_checklist(campus, service_ids=['education'], reminders=[{
            'service_id': 'education', 'start': '2026-09-11T23:00:00-04:00',
            'end': '2026-09-11T23:15:00-04:00'}])


@pytest.fixture(scope='module')
def whut_stack(tmp_path_factory):
    server = Stack(tmp_path_factory.mktemp('whut-stack'), with_ui=True, data_profile='whut-2026')
    try:
        for kind in ('rag', 'tools', 'planner', 'app'):
            server.start(kind)
        yield server
    finally:
        for kind in ('app', 'planner', 'tools', 'rag'):
            server.stop(kind)


@pytest.mark.integration
async def test_whut_real_protocols_and_clarification(whut_stack):
    gateway, planner = MCPGateway(whut_stack.config), PlannerClient(whut_stack.config)
    assert 'search_knowledge' in {t.name for t in await gateway.discover('rag')}
    async with httpx.AsyncClient() as http:
        assert (await A2ACardResolver(http, whut_stack.config.planner_url).get_agent_card()).name == 'CampusPilot Planner'
    first = await poll_task(planner, await planner.submit(PlanRequest(
        session_id='whut-a2a', request='安排报到').model_dump(mode='json')))
    assert first['status']['state'] == 'input-required'
    continued = PlanRequest(session_id='whut-a2a', request='我是2026级本科生，安排报到')
    resumed = await poll_task(planner, await planner.submit(continued.model_dump(mode='json'), first['id'], first['contextId']))
    assert resumed['id'] == first['id'] and resumed['status']['state'] == 'completed', resumed
    artifact = task_artifact(resumed)
    plan = Plan.model_validate(artifact['plan'])
    assert plan.data_profile == 'whut-2026' and all(i.start is None for i in plan.items)
    assert any(t.get('tool') == 'get_service_info' for t in artifact['trace'])
    assert (await gateway.call('apply_plan', {'snapshot': plan.model_dump(mode='json'), 'operation_id': 'readonly'}))['error']['code'] == 'forbidden'


@pytest.mark.integration
async def test_whut_chat_save_restart_and_raw_trace(whut_stack):
    server = whut_stack
    query = await chat(server, '2026级本科生报到需要带什么材料？')
    assert query['citations'] and not any(t['protocol'] == 'A2A' for t in query['trace'])
    assert '高考准考证' in query['reply']
    missing = await chat(server, '请帮我规划入学清单')
    assert '请补充' in missing['reply']
    plan_turn = await chat(server, '我是2026级本科生，还没缴费，安排报到入住。', missing['session_id'])
    assert plan_turn['plan_id'] and plan_turn['pending_actions'], plan_turn
    assert '[E-whut-2026-timeline]' in plan_turn['reply'] and '[E-whut-2026-green]' in plan_turn['reply']
    assert not any('、' in citation for citation in plan_turn['citations'])
    gateway = MCPGateway(server.config)
    assert (await gateway.call('list_todos', {}))['data'] == []
    async with httpx.AsyncClient(timeout=30) as client:
        action_url = server.base + '/v1/actions/' + plan_turn['pending_actions'][0] + '/confirm'
        saved = (await client.post(action_url)).json()
        assert saved['ok'] and saved['data']['events_created'] == 0, saved
        assert (await client.post(action_url)).json() == saved
        sources = (await client.get(server.base + '/v1/sources')).json()
        assert len(sources['sources']) == 4 and sources['cohort_year'] == 2026
        assert (await client.get(server.base + '/ui/')).status_code == 200
        assert (await client.get(server.base + '/health')).json()['synthetic_data'] is False
    before = await gateway.call('list_todos', {})
    server.stop('tools')
    server.start('tools')
    server.stop('app')
    server.start('app')
    assert await gateway.call('list_todos', {}) == before
    payload = dict(model_mode='fixture', real_public_sources=True, paid_model_calls=0,
                   reference_date=server.config.demo_date, note='Historical replay, not live-model evaluation',
                   turns=[query, missing, plan_turn], confirmation=saved, sources=sources)
    (server.root / 'whut-wire-trace.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.integration
async def test_whut_unsupported_year_and_planner_failure(whut_stack):
    turn = await chat(whut_stack, '2027级本科生报到材料是什么？')
    assert turn['evidence'] == [] and '没有找到' in turn['reply']
    whut_stack.stop('planner')
    try:
        turn = await chat(whut_stack, '2026级本科报到材料是什么？')
        assert turn['citations']
        failed = await chat(whut_stack, '我是2026级本科生，帮我规划报到')
        assert not failed['pending_actions'] and '规划服务调用失败' in failed['reply']
    finally:
        whut_stack.start('planner')


@pytest.mark.integration
async def test_whut_green_followup_and_cancel(whut_stack):
    planner = PlannerClient(whut_stack.config)
    request = PlanRequest(session_id='green-followup', request='2026级本科，请规划绿色通道',
        service_ids=['green_channel', 'registration'])
    first = await poll_task(planner, await planner.submit(request.model_dump(mode='json')))
    assert first['status']['state'] == 'input-required'
    # Only provide new facts; preserving the original task is a server responsibility.
    followup = PlanRequest(session_id='green-followup', request='我申请生源地贷款')
    result = await poll_task(planner, await planner.submit(followup.model_dump(mode='json'), first['id'], first['contextId']))
    assert result['status']['state'] == 'completed', result
    plan = task_artifact(result)['plan']
    assert 'green_channel' in {i['service_id'] for i in plan['items']}
    assert plan['conditions']['funding_route'] == 'origin_loan'
    waiting = await poll_task(planner, await planner.submit(PlanRequest(session_id='whut-cancel', request='规划报到').model_dump(mode='json')))
    assert waiting['status']['state'] == 'input-required'
    assert (await planner.cancel(waiting['id']))['status']['state'] == 'canceled'
    assert (await planner.get(waiting['id']))['status']['state'] == 'canceled'


@pytest.mark.integration
async def test_whut_reminder_via_main_agent(whut_stack):
    turn = await chat(whut_stack, '我是2026级本科生，请规划报到。给报到设置个人提醒：2026-09-12T09:00:00+08:00 到2026-09-12T09:15:00+08:00')
    assert turn['plan_id'] and turn['pending_actions'], turn
    async with httpx.AsyncClient(timeout=30) as client:
        saved = (await client.post(whut_stack.base + '/v1/actions/' + turn['pending_actions'][0] + '/confirm')).json()
        assert saved['ok'] and saved['data']['events_created'] == 1, saved
        ics = Calendar.from_ical((await client.get(whut_stack.base + '/v1/calendar.ics')).text)
        assert any('不是校方预约' in str(e['DESCRIPTION']) for e in ics.walk('VEVENT'))
