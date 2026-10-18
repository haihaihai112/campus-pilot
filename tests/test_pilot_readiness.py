"""Pilot evidence must distinguish real adoption from test traffic and successful turns."""
import asyncio
from types import SimpleNamespace

import pytest

from campus_agent.assistant import Assistant
from campus_agent.config import Settings
from campus_agent.db import Store
from campus_agent.metrics import pilot_stats


def config_at(root, **extra):
    return Settings(runtime=root, mode='fixture', data_profile='whut-2026',
                    demo_date='2026-10-18', **extra)


def test_stats_reports_real_profile_without_inventing_usage(tmp_path):
    report = pilot_stats(config_at(tmp_path))
    assert report['synthetic_data'] is False
    assert report['data_profile'] == 'whut-2026'
    assert report['adoption']['eligible_users_with_turns'] == 0
    assert report['adoption']['reviewed_resolved_users'] == 0


def test_stats_excludes_unclassified_fixture_developer_and_historical_traffic(tmp_path):
    config = config_at(tmp_path)
    db = Store(tmp_path / 'assistant.sqlite')
    cases = [('real1', 'live', 'participant', 'current_need'),
             ('real2', 'live', 'participant', 'current_need'),
             ('dev', 'live', 'developer', 'test'),
             ('history', 'live', 'participant', 'historical_demo'),
             ('fixture', 'fixture', 'participant', 'current_need'),
             ('unknown', 'live', 'unclassified', 'unclassified')]
    for uid, mode, cohort, purpose in cases:
        db.put('turns', uid, {'id': uid, 'user_id': uid, 'model_mode': mode,
            'cohort': cohort, 'usage_purpose': purpose, 'status': 'completed',
            'workflow_outcome': 'response_generated', 'queue_wait_ms': 10,
            'latency_ms': 20, 'total_latency_ms': 30})
    db.put('task_reviews', 'real1', {'turn_id': 'real1', 'outcome': 'resolved'})
    db.put('task_reviews', 'dev', {'turn_id': 'dev', 'outcome': 'resolved'})
    for key, result, mode in [('good', True, 'live'), ('bad', False, 'live'), ('fake', True, 'fixture')]:
        db.put('actions', key, {'id': key, 'user_id': 'real1', 'model_mode': mode,
            'cohort': 'participant', 'usage_purpose': 'current_need',
            'status': 'completed' if result else 'retryable', 'result': {'ok': result}})
    report = pilot_stats(config)
    assert report['adoption']['eligible_users_with_turns'] == 2
    assert report['adoption']['reviewed_resolved_users'] == 1
    assert report['adoption']['users_with_successful_writes'] == 1
    assert report['adoption']['successful_write_operations'] == 1
    assert report['model_modes']['live']['turns'] == 5
    assert report['model_modes']['live']['queue_wait_p50_ms'] == 10
    assert report['model_modes']['live']['end_to_end_p50_ms'] == 30
    assert 'real1' not in str(report)  # aggregates contain no raw identity


async def test_enrollment_does_not_relabel_old_turns_or_existing_sessions(tmp_path, monkeypatch):
    from campus_agent.metrics import enroll_account
    engine = Assistant(config_at(tmp_path))
    uid = engine.accounts.create('student01', 'readiness-test-password')

    async def no_network():
        pass

    async def answer(state, config):
        return {**state, 'reply': '测试响应'}

    monkeypatch.setattr(engine, 'discover_tools', no_network)
    monkeypatch.setattr(engine.graph, 'ainvoke', answer)
    old = engine.enqueue('before enrollment', user_id=uid)
    await asyncio.gather(*list(engine.jobs))
    enroll_account(engine.config, 'student01', 'participant', 'current_need')
    continued = engine.enqueue('same session', session_id=old['session_id'], user_id=uid)
    new = engine.enqueue('new session', user_id=uid)
    await asyncio.gather(*list(engine.jobs))
    assert engine.db.get('turns', old['id'])['cohort'] == 'unclassified'
    assert engine.db.get('turns', continued['id'])['cohort'] == 'unclassified'
    assert engine.db.get('turns', new['id'])['cohort'] == 'participant'
    assert engine.db.get('turns', new['id'])['usage_purpose'] == 'current_need'
    assert len(engine.db.all('enrollment_events')) == 1


async def test_queue_wait_and_cancellation_are_recorded_without_network(tmp_path, monkeypatch):
    engine = Assistant(config_at(tmp_path, max_active_turns=1))
    entered, release = asyncio.Event(), asyncio.Event()

    async def discover():
        pass

    async def answer(state, config):
        entered.set()
        await release.wait()
        return {**state, 'reply': '离线同步测试'}

    monkeypatch.setattr(engine, 'discover_tools', discover)
    monkeypatch.setattr(engine.graph, 'ainvoke', answer)
    first = engine.enqueue('one', user_id='u1')
    await asyncio.wait_for(entered.wait(), 5)
    second = engine.enqueue('two', user_id='u2')
    await asyncio.sleep(.04)
    release.set()
    await asyncio.gather(*list(engine.jobs))
    row = engine.db.get('turns', second['id'])
    assert row['queue_wait_ms'] >= 30
    assert row['total_latency_ms'] >= row['queue_wait_ms']
    assert engine.db.get('turns', first['id'])['workflow_outcome'] == 'response_generated'
    await engine.active_slots.acquire()
    canceled = engine.enqueue('cancel while queued', user_id='u3')
    await asyncio.sleep(.01)
    for task in list(engine.jobs):
        task.cancel()
    await asyncio.gather(*list(engine.jobs), return_exceptions=True)
    engine.active_slots.release()
    row = engine.db.get('turns', canceled['id'])
    assert row['status'] == 'interrupted'
    assert row['workflow_outcome'] == 'interrupted'
    assert row['queue_wait_ms'] >= 0


async def test_generated_answer_is_not_automatic_task_success(tmp_path, monkeypatch):
    from campus_agent.metrics import review_turn
    engine = Assistant(config_at(tmp_path))
    async def discover():
        pass
    async def answer(state, config):
        return {**state, 'reply': '测试回复', 'workflow_outcome': 'needs_input'}
    monkeypatch.setattr(engine, 'discover_tools', discover)
    monkeypatch.setattr(engine.graph, 'ainvoke', answer)
    turn = engine.enqueue('plan')
    await asyncio.gather(*list(engine.jobs))
    assert engine.db.get('turns', turn['id'])['workflow_outcome'] == 'needs_input'
    with pytest.raises(ValueError):
        review_turn(engine.config, turn['id'], 'resolved')
    review_turn(engine.config, turn['id'], 'unresolved')
    assert engine.db.get('task_reviews', turn['id'])['outcome'] == 'unresolved'


def test_restart_marks_pending_turns_interrupted_not_successful(tmp_path):
    config = config_at(tmp_path)
    db = Store(tmp_path / 'assistant.sqlite')
    db.put('turns', 'pending', {'id': 'pending', 'status': 'queued', 'created_at': 1})
    engine = Assistant(config)
    row = engine.db.get('turns', 'pending')
    assert row['workflow_outcome'] == 'interrupted'
    assert row['error_type'] == 'ServiceRestart'


async def test_model_records_requested_and_response_model_and_unknown_usage(tmp_path):
    from campus_agent.llm import Model
    from campus_agent.telemetry import MODEL_CALLS
    model = Model(config_at(tmp_path))
    model.config.mode = 'live'  # inject a local transport stub; no credentials or network
    async def create(**kwargs):
        return SimpleNamespace(model='actual-model', usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content='ok', tool_calls=[]))])
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    calls = []
    token = MODEL_CALLS.set(calls)
    try:
        _, usage = await model.respond([], [])
    finally:
        MODEL_CALLS.reset(token)
    assert calls[0]['requested_model'] == 'deepseek-chat'
    assert calls[0]['response_model'] == 'actual-model'
    assert calls[0]['usage_reported'] is False
    assert calls[0]['prompt_tokens'] is None
    assert usage['total_tokens'] == 0  # compatibility aggregate, never price missing usage as zero


async def test_model_failure_records_are_task_local_without_sensitive_errors(tmp_path):
    from campus_agent.llm import Model
    from campus_agent.telemetry import MODEL_CALLS
    model = Model(config_at(tmp_path))
    model.config.mode = 'live'

    async def create(**kwargs):
        await asyncio.sleep(.01)
        raise RuntimeError('private-provider-message')

    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def invoke():
        calls = []
        token = MODEL_CALLS.set(calls)
        try:
            with pytest.raises(RuntimeError):
                await model.respond([], [])
        finally:
            MODEL_CALLS.reset(token)
        return calls

    first, second = await asyncio.gather(invoke(), invoke())
    assert len(first) == len(second) == 1
    assert first[0]['call_id'] != second[0]['call_id']
    assert first[0]['status'] == 'failed'
    assert first[0]['error_type'] == 'RuntimeError'
    assert 'private-' not in str(first)
    assert MODEL_CALLS.get() is None


async def test_failed_planner_returns_usage_metadata_to_own_turn(tmp_path):
    engine = Assistant(config_at(tmp_path))
    engine.db.put('sessions', 's', {'id': 's', 'user_id': 'u'})
    engine.db.put('turns', 't', {'id': 't', 'trace': []})
    call_record = {'call_id': 'failed-call', 'status': 'failed'}

    async def submit(*args):
        return {'id': 'a2a-task', 'contextId': 'context', 'status': {
            'state': 'failed', 'message': {'parts': [{'kind': 'text', 'text': 'failed'}],
                                         'metadata': {'model_calls': [call_record]}}}}

    engine.planner.submit = submit
    state = {'session_id': 's', 'turn_id': 't', 'trace': [], 'model_calls': []}
    result = await engine._delegate({'request': '规划'}, state)
    assert result['ok'] is False
    assert state['model_calls'] == [call_record]
