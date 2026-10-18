"""Contract tests only: providers below are test doubles, not school connections."""
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from campus_agent.api import create_app
from campus_agent.integrations.school import (
    DormAssignment, HousingSnapshot, SchoolService, SchoolSource, StudentBinding,
    TimetableEntry, TimetableSnapshot,
)
from test_pilot import PASSWORD, pilot_config, sign_in


def binding(user_id='alice'):
    return StudentBinding(user_id=user_id, school_id='whut', subject_id='test-subject',
                          authorization_ref='test-only-grant')


def source():
    return SchoolSource(system='test-only-school', record_version='v1',
        retrieved_at='2026-10-18T10:00:00+08:00', data_as_of='2026-10-18T09:00:00+08:00')


class TestProvider:
    __test__ = False
    name = 'test-only'

    def __init__(self):
        self.calls = []
        self.subject_id = 'test-subject'
        self.school_id = 'whut'
        self.error = None

    async def list_timetable(self, identity, start, end):
        self.calls.append((identity, start, end))
        if self.error:
            raise self.error
        return TimetableSnapshot(school_id=self.school_id, subject_id=self.subject_id, source=source(), entries=[
            TimetableEntry(course_id='test-course', title='测试课程',
                start='2026-10-19T08:00:00+08:00', end='2026-10-19T09:00:00+08:00',
                location='测试教室', source='test-only')])

    async def get_housing(self, identity):
        self.calls.append(identity)
        if self.error:
            raise self.error
        return HousingSnapshot(school_id=self.school_id, subject_id=self.subject_id,
            source=source(), assignment=DormAssignment(campus='测试校区', building='测试楼', room='测试室'))


async def test_unconnected_school_is_not_empty_success():
    service = SchoolService()
    assert service.status()['status'] == 'not_connected'
    for result in (await service.timetable('alice', '2026-10-19', '2026-10-25'),
                   await service.housing('alice')):
        assert result['ok'] is False and result['data'] is None
        assert result['error']['code'] == 'not_connected'


async def test_missing_or_foreign_binding_never_calls_provider():
    provider = TestProvider()
    for resolver in (lambda uid: None, lambda uid: binding('bob')):
        service = SchoolService(provider, resolver)
        result = await service.housing('alice')
        assert result['error']['code'] == 'identity_not_bound'
    assert not provider.calls


async def test_school_contract_uses_bound_subject_and_keeps_identity_private():
    provider = TestProvider()
    service = SchoolService(provider, lambda uid: binding(uid))
    result = await service.timetable('alice', '2026-10-19', '2026-10-25')
    assert result['ok'] and len(result['data']['entries']) == 1
    assert result['data']['source']['record_version'] == 'v1'
    assert result['data']['read_only'] is True
    assert 'subject_id' not in result['data'] and 'authorization_ref' not in str(result)
    identity, start, end = provider.calls[0]
    assert identity.user_id == 'alice' and identity.subject_id == 'test-subject'
    assert start == date(2026, 10, 19) and end == date(2026, 10, 25)
    assert (await service.housing('alice'))['data']['assignment']['room'] == '测试室'


@pytest.mark.parametrize('attribute,value', [('subject_id', 'other'), ('school_id', 'other')])
async def test_upstream_foreign_person_or_school_is_rejected(attribute, value):
    provider = TestProvider()
    setattr(provider, attribute, value)
    service = SchoolService(provider, lambda uid: binding(uid))
    for result in (await service.timetable('alice', '2026-10-19', '2026-10-25'),
                   await service.housing('alice')):
        assert result['error']['code'] == 'provider_error'
        assert result['data'] is None and '测试室' not in str(result)


@pytest.mark.parametrize('start,end', [('bad', '2026-10-25'), ('2026-10-25', '2026-10-19'),
                                     ('2026-10-01', '2026-12-01')])
async def test_bad_range_prevents_upstream_call(start, end):
    provider = TestProvider()
    result = await SchoolService(provider, lambda uid: binding(uid)).timetable('alice', start, end)
    assert result['error']['code'] == 'invalid_arguments'
    assert not provider.calls


async def test_out_of_range_timetable_is_not_returned():
    result = await SchoolService(TestProvider(), lambda uid: binding(uid)).timetable(
        'alice', '2026-10-20', '2026-10-25')
    assert result['error']['code'] == 'provider_error'


async def test_connected_empty_is_distinct_from_unconnected():
    class EmptyProvider(TestProvider):
        async def list_timetable(self, identity, start, end):
            return TimetableSnapshot(school_id='whut', subject_id=identity.subject_id,
                                     source=source(), entries=[])

        async def get_housing(self, identity):
            return HousingSnapshot(school_id='whut', subject_id=identity.subject_id,
                                   source=source(), assignment=None)

    service = SchoolService(EmptyProvider(), lambda uid: binding(uid))
    timetable = await service.timetable('alice', '2026-10-19', '2026-10-25')
    housing = await service.housing('alice')
    assert timetable['ok'] and timetable['data']['entries'] == []
    assert housing['ok'] and housing['data']['assignment'] is None
    assert timetable['data']['source'] and housing['data']['source']


@pytest.mark.parametrize('error,code', [(RuntimeError('private upstream body'), 'provider_error'),
                                      (TimeoutError('private upstream body'), 'unavailable')])
async def test_upstream_failure_never_echoes_private_details(error, code):
    provider = TestProvider()
    provider.error = error
    result = await SchoolService(provider, lambda uid: binding(uid)).housing('alice')
    assert result['error']['code'] == code
    assert 'private upstream body' not in str(result)


def test_source_and_course_times_require_timezone_and_order():
    with pytest.raises(ValidationError):
        SchoolSource(system='test', record_version='v1',
            retrieved_at='2026-10-18T10:00:00', data_as_of='2026-10-18T09:00:00+08:00')
    with pytest.raises(ValidationError):
        TimetableEntry(course_id='test', title='test', start='2026-10-19T10:00:00+08:00',
            end='2026-10-19T09:00:00+08:00', location='test', source='test')
    with pytest.raises(ValidationError):
        DormAssignment(campus='test', building='test', room='test', roommates=['private'])


async def test_personal_school_routes_authenticate_and_ignore_client_identity(tmp_path, monkeypatch):
    app = create_app(pilot_config(tmp_path), with_ui=False)
    accounts = app.state.engine.accounts
    uid = accounts.create('2026000001', PASSWORD)
    accounts.create('2026000002', PASSWORD)
    calls = []

    async def fake_call(self, name, args):
        calls.append((name, args))
        return {'ok': False, 'data': None, 'error': {'code': 'not_connected', 'message': '尚未接入'}}

    monkeypatch.setattr('campus_agent.api.MCPGateway.call', fake_call)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/v1/school/housing')).status_code == 401
        await sign_in(client, '2026000001')
        response = await client.get('/v1/school/timetable', params={
            'start_date': '2026-10-19', 'end_date': '2026-10-25', 'user_id': '2026000002'})
        assert response.status_code == 200 and response.json()['error']['code'] == 'not_connected'
        assert calls[-1] == ('get_timetable', {'start_date': '2026-10-19', 'end_date': '2026-10-25', 'user_id': uid})
        response = await client.get('/v1/school/housing', params={'student_id': '2026000002'})
        assert calls[-1] == ('get_dorm_assignment', {'user_id': uid})
        assert response.headers['cache-control'] == 'private, no-store'
        assert (await client.get('/v1/school/timetable', params={
            'start_date': 'bad', 'end_date': '2026-10-25'})).status_code == 422


async def test_school_contract_in_openapi(tmp_path):
    app = create_app(pilot_config(tmp_path), with_ui=False)
    schema = app.openapi()
    for path, model in (('/v1/school/timetable', 'TimetableResponse'), ('/v1/school/housing', 'HousingResponse')):
        operation = schema['paths'][path]['get']
        assert operation['responses']['200']['content']['application/json']['schema']['$ref'].endswith(model)
        assert all(p['name'] not in {'user_id', 'student_id', 'subject_id'} for p in operation.get('parameters', []))
