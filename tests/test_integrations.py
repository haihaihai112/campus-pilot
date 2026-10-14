"""Provider responses below are test doubles, not live Amap/calendar measurements."""
import json
from datetime import timedelta

import httpx
import pytest

from campus_agent.campus import Campus
from campus_agent.assistant import Assistant
from campus_agent.config import Settings
from campus_agent.integrations.calendar import CalendarEvent, CalendarProvider, CalendarService
from campus_agent.integrations.maps import AmapProvider, CampusLocations, coordinate
from test_core import make_plan


def provider(tmp_path, body, key='test-secret'):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    config = Settings(runtime=tmp_path, mode='fixture', amap_api_key=key)
    return AmapProvider(config, httpx.MockTransport(respond)), requests


async def test_missing_map_key_never_calls_network(tmp_path):
    api, calls = provider(tmp_path, {}, key='')
    result = await api.weather(city_adcode='110101')
    assert result['error']['code'] == 'not_configured'
    assert not calls


@pytest.mark.parametrize('value', ['nan,30', '120,inf', '181,20', '120,91', 'abc', '120,20,30'])
def test_invalid_coordinates(value):
    with pytest.raises(ValueError):
        coordinate(value)


@pytest.mark.parametrize('code,expected', [('10001', 'authentication'), ('10003', 'rate_limited')])
async def test_map_errors_do_not_expose_key(tmp_path, code, expected):
    api, calls = provider(tmp_path, {'status': '0', 'infocode': code, 'info': 'test-secret'})
    result = await api.weather(city_adcode='110101')
    assert result['error']['code'] == expected
    assert 'test-secret' not in json.dumps(result)
    assert len(calls) == 1


async def test_places_keep_ambiguous_candidates(tmp_path):
    api, calls = provider(tmp_path, {'status': '1', 'pois': [
        {'id': str(i), 'name': '同名地点', 'location': '116.4,39.9', 'adcode': '110101'} for i in range(2)]})
    result = await api.search_places(keywords='同名地点', city='北京')
    assert len(result['data']['places']) == 2 and result['data']['requires_selection']
    assert calls[0].url.params['citylimit'] == 'true'


async def test_route_parsing_is_current_estimate(tmp_path):
    api, calls = provider(tmp_path, {'status': '1', 'route': {'paths': [
        {'duration': '601', 'distance': '820', 'steps': [{'instruction': '步行前往'}]}]}})
    result = await api.get_route(origin='116.4,39.9', destination='116.41,39.91')
    assert result['data']['duration_minutes'] == 11
    assert result['data']['distance_meters'] == 820
    assert '不是未来' in result['data']['notice']
    assert calls[0].url.path == '/v3/direction/walking'


async def test_weather_does_not_invent_unavailable_date(tmp_path):
    api, calls = provider(tmp_path, {'status': '1', 'forecasts': [
        {'city': '测试城市', 'reporttime': '2026-09-21', 'casts': [{'date': '2026-09-21', 'dayweather': '晴'}]}]})
    good = await api.weather(city_adcode='110101', target_date='2026-09-21')
    assert good['data']['forecasts'][0]['dayweather'] == '晴'
    bad = await api.weather(city_adcode='110101', target_date='2026-09-05')
    assert bad['error']['code'] == 'out_of_forecast_range'


def test_synthetic_campus_has_no_real_coordinate(tmp_path):
    locations = CampusLocations(Settings(runtime=tmp_path, campus_locations_file=''))
    assert locations.get('mingde')['error']['code'] == 'not_configured'


class FakeCalendar(CalendarProvider):
    """Test-only provider; never registered or available in the running application."""
    name = 'test-only'
    binding_id = 'test-only:account:calendar'

    def __init__(self):
        self.events = {}
        self.calls = 0
        self.fail_on = None
        self.uncertain = False

    async def list_events(self, start, end):
        return list(self.events.values())

    async def upsert_event(self, event, stable_key, idempotency_key, existing_event_id):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError('test-secret')
        event_id = existing_event_id or stable_key
        self.events[event_id] = CalendarEvent(event_id=event_id, title=event.title, start=event.start,
            end=event.end, location=event.location, managed_plan_id=event.managed_plan_id)
        if self.uncertain:
            self.uncertain = False
            raise TimeoutError('test-secret')
        return event_id


def calendar_stack(tmp_path):
    config = Settings(runtime=tmp_path, mode='fixture', calendar_provider='none')
    campus = Campus(config)
    plan = make_plan()
    assert campus.apply(plan.model_dump(mode='json'), 'local-save')['ok']
    fake = FakeCalendar()
    return CalendarService(config, campus, fake), fake, plan


async def sync(service, plan, op='op', **overrides):
    args = dict(plan_id=plan.plan_id, plan_version=plan.version, provider_binding=service.provider.binding_id,
                reminder_minutes=30, operation_id=op)
    return await service.sync_plan(**{**args, **overrides})


async def test_calendar_without_platform_is_not_empty_success(tmp_path):
    config = Settings(runtime=tmp_path, calendar_provider='none')
    service = CalendarService(config, Campus(config))
    assert service.status()['status'] == 'not_connected'
    assert (await service.list_events('2026-09-05', '2026-09-06'))['error']['code'] == 'not_connected'
    result = await service.sync_plan('plan', 1, 'binding', 30, 'op')
    assert result['error']['code'] == 'not_connected'


async def test_calendar_idempotency_persists_across_restart(tmp_path):
    service, fake, plan = calendar_stack(tmp_path)
    first = await sync(service, plan)
    assert first['ok'] and len(fake.events) == len(plan.items)
    restored = CalendarService(service.config, service.campus, fake)
    assert await sync(restored, plan) == first
    assert fake.calls == len(plan.items)
    assert (await sync(restored, plan, reminder_minutes=10))['error']['code'] == 'conflict'


async def test_calendar_partial_failure_resumes_completed_items(tmp_path):
    service, fake, plan = calendar_stack(tmp_path)
    fake.fail_on = 2
    first = await sync(service, plan)
    assert first['error']['code'] == 'partial_failure' and len(fake.events) == 1
    assert 'test-secret' not in json.dumps(first)
    restored = CalendarService(service.config, service.campus, fake)
    assert (await sync(restored, plan))['ok']
    assert len(fake.events) == len(plan.items) and fake.calls == len(plan.items) + 1


async def test_calendar_uncertain_remote_outcome_reconciles_stable_key(tmp_path):
    service, fake, plan = calendar_stack(tmp_path)
    fake.uncertain = True
    assert not (await sync(service, plan))['ok']
    assert len(fake.events) == 1
    assert (await sync(service, plan))['ok']
    assert len(fake.events) == len(plan.items)


async def test_calendar_binding_version_and_owner_checks(tmp_path):
    service, fake, plan = calendar_stack(tmp_path)
    for args in ({'provider_binding': 'other'}, {'plan_version': 2}, {'user_id': 'other'}):
        assert not (await sync(service, plan, **args))['ok']
    assert not fake.events and not fake.calls


async def test_calendar_conflict_prevents_writes(tmp_path):
    service, fake, plan = calendar_stack(tmp_path)
    item = plan.items[0]
    fake.events['busy'] = CalendarEvent(event_id='busy', title='已有安排', start=item.start, end=item.end)
    assert (await sync(service, plan))['error']['code'] == 'conflict'
    assert fake.calls == 0


async def test_calendar_updated_plan_upserts_existing_remote_events(tmp_path):
    service, fake, plan = calendar_stack(tmp_path)
    assert (await sync(service, plan))['ok']
    old_ids = set(fake.events)
    updated = make_plan('2026-09-07T09:00:00+08:00', plan_id=plan.plan_id, version=2)
    assert service.campus.apply(updated.model_dump(mode='json'), 'local-revise')['ok']
    assert (await sync(service, updated, op='revise'))['ok']
    assert set(fake.events) == old_ids
    assert min(e.start for e in fake.events.values()) == updated.items[0].start


async def test_calendar_query_validates_timezone_and_range(tmp_path):
    service, fake, plan = calendar_stack(tmp_path)
    a = plan.items[0].start
    result = await service.list_events(a.isoformat(), (a + timedelta(days=32)).isoformat())
    assert result['error']['code'] == 'invalid_arguments'
    assert (await service.list_events('2026-09-05T12:00', '2026-09-06T12:00'))['error']['code'] == 'invalid_arguments'


@pytest.mark.parametrize('body,method,args', [
    ([], 'weather', {'city_adcode': '110101'}),
    ({'status': '1', 'pois': 'invalid'}, 'search_places', {'keywords': 'school', 'city': 'test'}),
    ({'status': '1', 'route': []}, 'get_route', {'origin': '116,39', 'destination': '117,40'}),
    ({'status': '1', 'forecasts': [None]}, 'weather', {'city_adcode': '110101'}),
])
async def test_malformed_provider_response_is_structured_error(tmp_path, body, method, args):
    api, calls = provider(tmp_path, body)
    assert (await getattr(api, method)(**args))['error']['code'] == 'provider_error'


async def test_calendar_proposal_only_executes_after_confirmation(tmp_path, monkeypatch):
    engine = Assistant(Settings(runtime=tmp_path, mode='fixture'))
    plan = make_plan()
    engine.db.put('plans', plan.plan_id, plan.model_dump(mode='json'))
    engine.db.put('sessions', 'unit', {'id': 'unit', 'last_plan_id': plan.plan_id})
    calls = []

    class Gateway:
        def __init__(self, config, writer=False, **kwargs):
            self.writer = writer

        async def call(self, name, args):
            calls.append((name, args, self.writer))
            if name == 'get_integration_status':
                return {'ok': True, 'data': {'calendar': {'binding_id': 'test-binding', 'provider': 'test-only'}}}
            if name == 'list_calendar_events':
                return {'ok': True, 'data': [{'plan_id': plan.plan_id, 'plan_version': plan.version}]}
            assert name == 'sync_plan_calendar' and self.writer
            return {'ok': True, 'data': {'synced_events': len(plan.items)}}

    monkeypatch.setattr('campus_agent.assistant.MCPGateway', Gateway)
    result = await engine.propose_calendar_sync('unit')
    assert result['ok'] and all(not c[2] for c in calls)
    action_id = result['data']['action_id']
    action = engine.db.get('actions', action_id)
    assert action['arguments']['provider_binding'] == 'test-binding'
    assert 'snapshot' not in action['arguments']
    assert (await engine.confirm(action_id))['ok']
    assert calls[-1][1]['operation_id'] == action_id
    count = len(calls)
    assert (await engine.confirm(action_id))['ok'] and len(calls) == count
    stale = await engine.propose_calendar_sync('unit')
    plan.version = 2
    engine.db.put('plans', plan.plan_id, plan.model_dump(mode='json'))
    count = len(calls)
    assert (await engine.confirm(stale['data']['action_id']))['error']['code'] == 'conflict'
    assert len(calls) == count
