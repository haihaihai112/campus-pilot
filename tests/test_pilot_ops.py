import json
import sqlite3

import pytest


def call(**extra):
    return {'call_id': 'c1', 'requested_model': 'm', 'response_model': 'm', 'endpoint_host': 'example.test',
            'status': 'completed', 'usage_reported': True, 'prompt_tokens': 1000,
            'completion_tokens': 200, 'total_tokens': 1200, **extra}


def pricing():
    return {'currency': 'CNY', 'verified_at': '2026-10-18', 'source_url': 'https://example.test/pricing',
            'rates': [{'endpoint_host': 'example.test', 'model': 'm',
                       'input_per_million': 2, 'output_per_million': 8}]}


def test_budget_projects_per_task_without_mixing_model_rates_or_currencies():
    from campus_agent.pilot_ops import estimate_budget
    sample = {'tasks': [{'id': 't1', 'model_calls': [call(), call(call_id='c2')]}]}
    report = estimate_budget(sample, pricing(), participants=70, tasks_per_person=2, server_cost=30)
    assert report['priced_sample_cost'] == pytest.approx(.0072)
    assert report['projected_model_cost'] == pytest.approx(1.008)
    assert report['projected_total_cost'] == pytest.approx(31.008)
    assert report['currency'] == 'CNY'


@pytest.mark.parametrize('changes', [dict(response_model='other'), dict(usage_reported=False),
                                    dict(prompt_tokens=None), dict(status='failed')])
def test_unknown_or_failed_usage_blocks_full_budget_projection(changes):
    from campus_agent.pilot_ops import estimate_budget
    report = estimate_budget({'tasks': [{'id': 't', 'model_calls': [call(**changes)]}]}, pricing(), 50, 1, 0)
    assert report['projected_model_cost'] is None
    assert report['unpriced_calls'] == 1


def test_duplicate_call_ids_are_not_double_priced_and_bad_numbers_rejected():
    from campus_agent.pilot_ops import estimate_budget
    sample = {'tasks': [{'id': 't', 'model_calls': [call(), call()]}]}
    assert estimate_budget(sample, pricing(), 50, 1, 0)['priced_sample_cost'] == pytest.approx(.0036)
    with pytest.raises(ValueError):
        estimate_budget(sample, pricing(), 50, 1, float('nan'))
    with pytest.raises(ValueError):
        estimate_budget(sample, pricing(), -1, 1, 0)


def test_backup_captures_sqlite_wal_and_restore_requires_new_target(tmp_path):
    from campus_agent.pilot_ops import backup_runtime, restore_runtime
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    with sqlite3.connect(runtime / 'assistant.sqlite') as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE evidence(value TEXT)')
        con.execute("INSERT INTO evidence VALUES('saved')")
        con.commit()
        backup = tmp_path / 'backup'
        backup_runtime(runtime, backup, services_stopped=True)
    restored = tmp_path / 'restored'
    restore_runtime(backup, restored)
    with sqlite3.connect(restored / 'assistant.sqlite') as con:
        assert con.execute('SELECT value FROM evidence').fetchone()[0] == 'saved'
    with pytest.raises(ValueError):
        restore_runtime(backup, restored)
    with pytest.raises(ValueError):
        backup_runtime(runtime, tmp_path / 'unsafe', services_stopped=False)
    manifest = json.loads((backup / 'backup-manifest.json').read_text(encoding='utf-8'))
    assert manifest['cross_service_consistency'] == 'operator_declared_stopped'


def test_restore_rejects_tampering_before_creating_destination(tmp_path):
    from campus_agent.pilot_ops import backup_runtime, restore_runtime
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    with sqlite3.connect(runtime / 'assistant.sqlite') as con:
        con.execute('CREATE TABLE t(a)')
    backup = tmp_path / 'backup'
    backup_runtime(runtime, backup, services_stopped=True)
    (backup / 'assistant.sqlite').write_bytes(b'tampered')
    with pytest.raises(ValueError):
        restore_runtime(backup, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('users', [5, 10])
async def test_offline_burst_exercises_queue_without_adoption_or_paid_calls(tmp_path, users):
    from campus_agent.pilot_ops import simulate_burst
    report = await simulate_burst(tmp_path / 'new', users, delay_ms=60, active_limit=2)
    assert report['accepted'] == users
    assert report['completed'] == users
    assert report['max_active_stub_calls'] == 2
    assert report['queued_turns'] > 0
    assert report['adoption']['eligible_users_with_turns'] == 0
    assert report['paid_model_calls'] == 0
    assert report['transport'] == 'in_process_ASGI'


async def test_offline_burst_reports_429_and_retry_after(tmp_path):
    from campus_agent.pilot_ops import simulate_burst
    report = await simulate_burst(tmp_path / 'new', 10, delay_ms=80, active_limit=2, pending_limit=3)
    assert report['accepted'] == 3
    assert report['rate_limited'] == 7
    assert report['retry_after_present'] is True


def test_incomplete_empty_or_turn_samples_cannot_be_priced_as_free():
    from campus_agent.pilot_ops import estimate_budget
    assert estimate_budget({'tasks': []}, pricing(), 5, 1, 0)['projected_total_cost'] is None
    sample = {'tasks': [{'id': 'x', 'model_calls': [call()], 'incomplete': True}]}
    assert estimate_budget(sample, pricing(), 5, 1, 0)['projected_total_cost'] is None
    with pytest.raises(ValueError):
        estimate_budget({'sample_unit': 'turn', **sample}, pricing(), 5, 1, 0)
    sample['tasks'][0]['model_calls'].append(call(prompt_tokens=2))
    with pytest.raises(ValueError, match='Conflicting'):
        estimate_budget(sample, pricing(), 5, 1, 0)


def test_restore_refuses_traversal_without_creating_target(tmp_path):
    from campus_agent.pilot_ops import restore_runtime
    backup = tmp_path / 'backup'
    backup.mkdir()
    (backup / 'backup-manifest.json').write_text(json.dumps({
        'schema_version': 1, 'files': {'data/../../outside.json': 'x'}}), encoding='utf-8')
    with pytest.raises(ValueError, match='path'):
        restore_runtime(backup, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()


def test_usage_export_never_joins_later_a2a_continuation_or_user_text(tmp_path):
    from campus_agent.db import Store
    from campus_agent.pilot_ops import export_usage
    db = Store(tmp_path / 'assistant.sqlite')
    db.put('turns', 'one', {'id': 'one', 'model_mode': 'live', 'status': 'completed',
                          'a2a_task_id': 'reused', 'model_calls': [call()],
                          'message': 'private-text', 'user_id': 'private-user'})
    planner = Store(tmp_path / 'planner.sqlite')
    planner.put('model_call_records', 'reused', {'task_id': 'reused',
                                              'model_calls': [call(call_id='later')]})
    sample = export_usage(tmp_path, ['one'])
    assert len(sample['tasks'][0]['model_calls']) == 1
    assert sample['sample_unit'] == 'turn'
    assert 'private-' not in json.dumps(sample)
    with pytest.raises(ValueError):
        export_usage(tmp_path, ['nonexistent'])
