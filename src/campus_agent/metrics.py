"""Operator-only pilot evidence; generated responses are not task success."""
from collections import Counter
from datetime import datetime, timezone
import sqlite3
import uuid

from .db import Store

COHORTS = ('participant', 'test', 'developer', 'historical_demo', 'unclassified')
PURPOSES = ('current_need', 'historical_demo', 'test', 'unclassified')


def enroll_account(config, username, cohort, purpose):
    """Operator declaration, applied to NEW sessions only; no retroactive adoption."""
    if cohort not in COHORTS or purpose not in PURPOSES:
        raise ValueError('Unknown cohort or purpose')
    path = config.runtime / 'accounts.sqlite'
    if not path.exists():
        raise ValueError('Create an invite account first')
    with sqlite3.connect(path) as con:
        row = con.execute('SELECT user_id FROM accounts WHERE username=?', (username,)).fetchone()
    if not row:
        raise ValueError('Account not found')
    db = Store(config.runtime / 'assistant.sqlite')
    record = {'user_id': row[0], 'cohort': cohort, 'usage_purpose': purpose,
              'recorded_at': datetime.now(timezone.utc).isoformat()}
    with db.connect() as con:
        db.put('enrollments', row[0], record, con)
        db.put('enrollment_events', uuid.uuid4().hex, record, con)
    return {'cohort': cohort, 'usage_purpose': purpose, 'applies_to': 'new_sessions_only'}


def review_turn(config, turn_id, outcome):
    """Manual operator judgment, distinct from feedback and automated guards."""
    db = Store(config.runtime / 'assistant.sqlite')
    turn = db.get('turns', turn_id)
    if not turn or outcome not in ('resolved', 'unresolved'):
        raise ValueError('Unknown turn or review outcome')
    if outcome == 'resolved' and (turn.get('status') != 'completed' or
            turn.get('workflow_outcome') not in ('response_generated', 'preview_ready', 'action_completed')):
        raise ValueError('Interrupted, guarded, failed or clarification turns cannot be resolved')
    if outcome == 'resolved' and turn.get('workflow_outcome') == 'action_completed':
        actions = [db.get('actions', aid, {}) for aid in turn.get('pending_actions', [])]
        if not actions or not all(a.get('status') == 'completed' and a.get('result', {}).get('ok') for a in actions):
            raise ValueError('Confirmed writes need a persisted successful execution result')
    record = {'turn_id': turn_id, 'outcome': outcome, 'reviewer': 'operator',
              'reviewed_at': datetime.now(timezone.utc).isoformat()}
    with db.connect() as con:
        db.put('task_reviews', turn_id, record, con)
        db.put('review_events', uuid.uuid4().hex, record, con)
    return record


def _median(rows, field):
    values = sorted(t[field] for t in rows if isinstance(t.get(field), (int, float)))
    if not values:
        return None
    n = len(values)
    return (values[(n - 1) // 2] + values[n // 2]) / 2


def _eligible(row):
    return (row.get('model_mode') == 'live' and row.get('cohort') == 'participant'
            and row.get('usage_purpose') == 'current_need' and row.get('user_id') not in (None, 'demo-user'))


def pilot_stats(config):
    db = Store(config.runtime / 'assistant.sqlite')
    turns, feedback, actions = db.all('turns'), db.all('feedback'), db.all('actions')
    reviews = {r['turn_id']: r for r in db.all('task_reviews')}
    modes = {}
    for mode in ('fixture', 'live'):
        rows = [t for t in turns if t.get('model_mode') == mode]
        users = {t.get('user_id', 'demo-user') for t in rows}
        completed = [t for t in rows if t.get('status') == 'completed']
        turn_ids = {t['id'] for t in rows}
        ratings = Counter(f['rating'] for f in feedback if f['turn_id'] in turn_ids)
        modes[mode] = {'unique_users_with_turns': len(users), 'turns': len(rows),
            'turn_states': dict(Counter(t.get('status', 'unknown') for t in rows)),
            'workflow_outcomes': dict(Counter(t.get('workflow_outcome', 'unrecorded') for t in rows)),
            'failure_types': dict(Counter(t['error_type'] for t in rows if t.get('error_type'))),
            'tool_failure_codes': dict(Counter(e['code'] for t in rows for e in t.get('tool_errors', []))),
            'cohort_purpose_turns': dict(Counter(
                f"{t.get('cohort', 'unclassified')}:{t.get('usage_purpose', 'unclassified')}" for t in rows)),
            'reported_tokens': sum(t.get('usage', {}).get('total_tokens', 0) for t in rows),
            'completed_latency_p50_ms': _median(completed, 'latency_ms'),
            'queue_wait_p50_ms': _median(rows, 'queue_wait_ms'),
            'end_to_end_p50_ms': _median(rows, 'total_latency_ms'),
            'timing_samples': {k: sum(isinstance(t.get(k), (int, float)) for t in rows)
                for k in ('queue_wait_ms', 'latency_ms', 'total_latency_ms')},
            'feedback_records': dict(ratings),
            'users_with_feedback': len({f.get('user_id') for f in feedback if f['turn_id'] in turn_ids})}
    eligible = [t for t in turns if _eligible(t)] if config.data_profile == 'whut-2026' else []
    eligible_ids = {t['id'] for t in eligible}
    saved = [a for a in actions if _eligible(a) and a.get('status') == 'completed'
             and a.get('result', {}).get('ok') is True] if config.data_profile == 'whut-2026' else []
    account_path = config.runtime / 'accounts.sqlite'
    account_count = 0
    if account_path.exists():
        with sqlite3.connect(account_path) as con:
            account_count = con.execute('SELECT count(*) FROM accounts').fetchone()[0]
    adoption = {'accounts_created': account_count,
        'eligible_users_with_turns': len({t['user_id'] for t in eligible}),
        'eligible_turns': len(eligible), 'reviewed_turns': sum(t['id'] in reviews for t in eligible),
        'reviewed_resolved_users': len({t['user_id'] for t in eligible
            if reviews.get(t['id'], {}).get('outcome') == 'resolved'}),
        'users_with_successful_writes': len({a['user_id'] for a in saved}),
        'successful_write_operations': len(saved),
        'eligible_feedback_users': len({f.get('user_id') for f in feedback if f['turn_id'] in eligible_ids})}
    return {'product': 'CampusPilot', 'sampled_at': datetime.now(timezone.utc).isoformat(),
        'synthetic_data': config.data_profile == 'synthetic', 'data_profile': config.data_profile,
        'model_modes': modes, 'adoption': adoption,
        'action_states': dict(Counter(a['status'] for a in actions)),
        'definitions': {
            'adoption': 'live + WHUT + operator-enrolled participant/current_need; legacy/unclassified excluded',
            'resolved': 'manual operator review, not independent accuracy; feedback is separate',
            'writes': 'distinct successful action IDs; repeats do not add operations; not school transactions',
            'unique_users_with_turns': 'submitted server identities, including tests; not adoption',
            'feedback': 'latest voluntary rating per user/turn, not accuracy',
            'latency': 'processing excludes queue; end-to-end includes queue; no load-capacity claim',
            'missing': 'legacy rows without classification/timing remain unknown; no backfill guesses'}}
