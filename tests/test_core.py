from datetime import datetime

from icalendar import Calendar
import pytest

from campus_agent.campus import Campus
from campus_agent.config import Settings
from campus_agent.knowledge import Knowledge
from campus_agent.models import Evidence, PlanRequest
from campus_agent.scheduler import schedule, validate_plan
from campus_agent.seed import build_seed


def make_plan(arrival='2026-09-05T14:00:00+08:00', events=None, **kwargs):
    data = build_seed()
    request = PlanRequest(session_id='unit', request='报到入住', campus_id='mingde', arrival=arrival, **kwargs)
    evidence = [Evidence.model_validate(d) for d in data['documents']]
    plan = schedule(request, data['services'], data['travel'], events or [], evidence)
    assert not validate_plan(plan, data['services'], data['travel'], events or [])
    return plan


def test_dependencies_and_travel():
    plan = make_plan()
    order = [i.service_id for i in plan.items]
    assert order.index('payment') < order.index('registration') < order.index('dorm')
    assert plan.items[0].start.isoformat() == '2026-09-05T14:15:00+08:00'
    assert next(i for i in plan.items if i.service_id == 'dorm').start.date().isoformat() == '2026-09-05'


def test_closed_window_moves_to_monday():
    plan = make_plan('2026-09-05T18:00:00+08:00')
    assert plan.items[0].start.date().isoformat() == '2026-09-07'


def test_existing_event_and_travel_both_sides():
    events = [{'start': '2026-09-05T14:00:00+08:00', 'end': '2026-09-05T15:00:00+08:00', 'location': 'dormitory'}]
    plan = make_plan(events=events)
    assert plan.items[0].start >= datetime.fromisoformat('2026-09-05T15:20:00+08:00')


def test_available_windows_and_unarranged():
    plan = make_plan(available_windows=[('2026-09-05T14:00:00+08:00', '2026-09-05T14:10:00+08:00')])
    assert not plan.items
    assert len(plan.unarranged) == 4


def test_completed_dependency_not_repeated():
    plan = make_plan(service_ids=['dorm'], completed=['payment', 'registration'])
    assert [i.service_id for i in plan.items] == ['dorm']


def test_validator_detects_bad_schedule():
    plan = make_plan()
    data = build_seed()
    plan.items[1].start = plan.items[0].start
    assert validate_plan(plan, data['services'], data['travel'], [])


def test_atomic_idempotent_save_and_ics(tmp_path):
    campus = Campus(Settings(runtime=tmp_path, mode='fixture'))
    plan = make_plan()
    assert campus.todos()['data'] == []
    first = campus.apply(plan.model_dump(mode='json'), 'confirm-1')
    assert first['ok']
    assert campus.apply(plan.model_dump(mode='json'), 'confirm-1') == first
    assert len(campus.todos()['data']) == len(plan.items)
    assert len(campus.events()['data']) == len(plan.items)
    plan.version += 1
    assert not campus.apply(plan.model_dump(mode='json'), 'confirm-1')['ok']
    parsed = Calendar.from_ical(campus.export()['data']['content'])
    assert len(parsed.walk('VEVENT')) == len(plan.items)
    assert all(e.get('DTSTART').dt.tzinfo for e in parsed.walk('VEVENT'))
    restored = Campus(Settings(runtime=tmp_path, mode='fixture'))
    assert restored.todos() == campus.todos()


def test_save_revalidates_conflicts(tmp_path):
    campus = Campus(Settings(runtime=tmp_path, mode='fixture'))
    plan = make_plan()
    assert campus.apply(plan.model_dump(mode='json'), 'one')['ok']
    other = make_plan()
    failed = campus.apply(other.model_dump(mode='json'), 'two')
    assert failed['error']['code'] == 'conflict'
    assert len(campus.events()['data']) == len(plan.items)


def test_todo_version_and_idempotency(tmp_path):
    campus = Campus(Settings(runtime=tmp_path, mode='fixture'))
    plan = make_plan()
    campus.apply(plan.model_dump(mode='json'), 'save')
    row = campus.todos()['data'][0]
    result = campus.update_todo(row['id'], 'completed', 1, 'update')
    assert result['data']['version'] == 2
    assert campus.update_todo(row['id'], 'completed', 1, 'update') == result
    assert not campus.update_todo(row['id'], 'pending', 1, 'stale')['ok']


def test_retrieval_filters_and_metadata(tmp_path):
    kb = Knowledge(Settings(runtime=tmp_path, mode='fixture'))
    result = kb.search(query='报到材料', campus_id='mingde', as_of_date='2026-09-05')
    assert result['ok'] and result['data']['evidence']
    assert all(e['campus_id'] == 'mingde' and e['backend'] == 'memory' for e in result['data']['evidence'])
    assert kb.search(query='报到', campus_id='mingde', as_of_date='2027-01-01')['data']['evidence'] == []


def test_seed_documents_match_structured():
    data = build_seed()
    docs = {d['evidence_id']: d for d in data['documents']}
    for service in data['services']:
        text = docs[service['evidence_id']]['text']
        assert service['location_name'] in text
        assert all(material in text for material in service['materials'])
        assert str(service['minutes']) in text


def test_missing_timezone_rejected():
    with pytest.raises(ValueError):
        PlanRequest(session_id='x', request='test', arrival='2026-09-05T14:00:00')
