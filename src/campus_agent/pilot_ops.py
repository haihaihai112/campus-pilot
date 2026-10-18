"""Local operator tools. No model requests, purchases or public business APIs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import shutil
import sqlite3
import stat
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

DATABASES = {'accounts.sqlite', 'assistant.sqlite', 'campus.sqlite', 'planner.sqlite',
             'calendar-sync.sqlite'}


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Expected a finite nonnegative number')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Expected a finite nonnegative number') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('Expected a finite nonnegative number')
    return result


def _unique_calls(calls):
    seen, result = {}, []
    for call in calls:
        key = call.get('call_id')
        if not key:
            raise ValueError('Missing call_id; legacy samples require manual reconstruction')
        if key in seen:
            if seen[key] != call:
                raise ValueError('Conflicting duplicate call_id')
            continue
        seen[key] = call
        result.append(call)
    return result


def estimate_budget(sample, pricing, participants, tasks_per_person, server_cost):
    """Project recorded per-task costs using operator-verified prices, never defaults."""
    if sample.get('sample_unit', 'task') != 'task':
        raise ValueError('Group all turns of each actual task before estimating per-task costs')
    people, frequency, hosting = map(_number, (participants, tasks_per_person, server_cost))
    if people != people.to_integral_value():
        raise ValueError('participants must be a whole number')
    if not pricing.get('currency') or not pricing.get('verified_at') or not pricing.get('source_url'):
        raise ValueError('Supply currency, verified_at and the effective supplier pricing URL')
    rates = {}
    for rate in pricing.get('rates', []):
        key = rate['endpoint_host'], rate['model']
        if key in rates:
            raise ValueError('Duplicate model/endpoint rate')
        rates[key] = (_number(rate['input_per_million']), _number(rate['output_per_million']))
    tasks = sample.get('tasks', [])
    if len({t['id'] for t in tasks}) != len(tasks):
        raise ValueError('Duplicate task sample')
    # Dedup across copied planner artifacts as well as within a task.
    calls = _unique_calls([c for t in tasks for c in t.get('model_calls', [])])
    incomplete = sum(not t.get('model_calls') or t.get('incomplete', False) for t in tasks)
    subtotal, unpriced = Decimal(0), 0
    for call in calls:
        rate = rates.get((call.get('endpoint_host'), call.get('response_model')))
        if (call.get('status') != 'completed' or not call.get('usage_reported') or not rate
                or any(call.get(k) is None for k in ('prompt_tokens', 'completion_tokens'))):
            unpriced += 1
            continue
        tokens = [_number(call[k]) for k in ('prompt_tokens', 'completion_tokens')]
        if any(t != t.to_integral_value() for t in tokens):
            raise ValueError('Token counts must be whole numbers')
        subtotal += sum(t * r for t, r in zip(tokens, rate)) / Decimal(1_000_000)
    mean = subtotal / len(tasks) if tasks and not incomplete and not unpriced else None
    projected = mean * people * frequency if mean is not None else None
    return {'currency': pricing['currency'], 'price_verified_at': pricing['verified_at'],
            'price_source_url': pricing['source_url'], 'sample_tasks': len(tasks),
            'unique_model_calls': len(calls), 'unpriced_calls': unpriced, 'incomplete_tasks': incomplete,
            'priced_sample_cost': float(subtotal), 'mean_task_cost': float(mean) if mean is not None else None,
            'participants': int(people), 'tasks_per_person': float(frequency), 'server_cost': float(hosting),
            'projected_model_cost': float(projected) if projected is not None else None,
            'projected_total_cost': float(projected + hosting) if projected is not None else None,
            'limitations': 'Sample extrapolation only. Failed/unknown usage blocks projection; no price is assumed. '
                          'Input rate must conservatively cover cache misses; taxes, retries and other fees need review.'}


def _rows(path, kind):
    if not path.exists():
        return []
    # Read-only access: exporting evidence must not initialize or migrate databases.
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as con:
        return [json.loads(row[0]) for row in con.execute('SELECT value FROM kv WHERE kind=?', (kind,))]


def export_usage(runtime, turn_ids):
    runtime = Path(runtime)
    turns = {r['id']: r for r in _rows(runtime / 'assistant.sqlite', 'turns')}
    tasks = []
    if not turn_ids or len(set(turn_ids)) != len(turn_ids):
        raise ValueError('Select distinct turn IDs representing whole task samples')
    for index, turn_id in enumerate(turn_ids, 1):
        row = turns.get(turn_id)
        if not row or row.get('model_mode') != 'live':
            raise ValueError('Select recorded live turns only; fixture is not a cost sample')
        # Use this turn's returned metadata. A2A task IDs may be reused by later continuations;
        # joining the planner's latest record would misattribute another turn's cost.
        calls = row.get('model_calls', [])
        tasks.append({'id': f'sample-{index}', 'model_calls': _unique_calls(calls),
                      'incomplete': row.get('status') != 'completed' or not calls or
                                    row.get('workflow_outcome') in {'failed', 'interrupted', 'tool_failed',
                                                                   'budget_exhausted'}})
    return {'schema_version': 1, 'sample_unit': 'turn', 'tasks': tasks,
            'note': 'No user messages or account IDs. A task spanning turns must be grouped before estimating.'}


def _allowed(name):
    parts = PurePosixPath(name).parts
    return (name in DATABASES or (len(parts) > 1 and parts[0] == 'data'
            and PurePosixPath(name).suffix in {'.json', '.md', '.txt'}))


def _target(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir() or destination.exists() or destination == source or source in destination.parents:
        raise ValueError('Source must exist; destination must be new and outside source')
    return source, destination


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _linked(path):
    # pathlib.is_junction is available only from Python 3.12; support project's 3.11.
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0)
                                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def backup_runtime(runtime, destination, *, services_stopped=False):
    if not services_stopped:
        raise ValueError('Stop all four services first and explicitly declare services_stopped')
    runtime, destination = _target(runtime, destination)
    files = []
    for path in runtime.rglob('*'):
        if _linked(path):
            raise ValueError('Symlinks/junctions are not supported')
        if path.is_file() and _allowed(path.relative_to(runtime).as_posix()):
            files.append(path)
    if not any(p.name in DATABASES for p in files):
        raise ValueError('No recognized runtime databases')
    destination.mkdir(parents=True)
    manifest = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'cross_service_consistency': 'operator_declared_stopped', 'files': {}}
    # SQLite backup API includes committed WAL; copying the .sqlite alone would lose it.
    for source in files:
        name = source.relative_to(runtime).as_posix()
        out = destination / name
        out.parent.mkdir(parents=True, exist_ok=True)
        if name in DATABASES:
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as incoming, sqlite3.connect(out) as saved:
                incoming.backup(saved)
                if saved.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Database integrity check failed')
        else:
            shutil.copyfile(source, out)
        manifest['files'][name] = _hash(out)
    (destination / 'backup-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def restore_runtime(backup, destination):
    backup, destination = _target(backup, destination)
    manifest_path = backup / 'backup-manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('Invalid manifest path')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or not manifest.get('files'):
        raise ValueError('Unsupported or empty manifest')
    files = []
    for name, digest in manifest['files'].items():
        relative = PurePosixPath(name)
        if ('\\' in name or ':' in name or relative.is_absolute() or '..' in relative.parts or
                not _allowed(name)):
            raise ValueError('Invalid backup file path')
        path = backup / name
        if any(_linked(p) for p in (path, *path.parents)):
            raise ValueError('Symlinks/junctions are not supported')
        if backup not in path.resolve().parents or not path.is_file() or _hash(path) != digest:
            raise ValueError('Backup checksum mismatch')
        if name in DATABASES:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as con:
                if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Database integrity check failed')
        files.append((name, path))
    # Validate everything before creating the new runtime. Never overwrite an existing one.
    destination.mkdir(parents=True)
    for name, source in files:
        out = destination / name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, out)
    return {'restored_files': len(files), 'target': str(destination), 'verified': True}


async def simulate_burst(runtime, users, delay_ms=100, active_limit=2, pending_limit=32):
    """Real login/chat ASGI handlers with delayed local model stub, not remote capacity."""
    import httpx
    from .api import create_app
    from .config import Settings
    from .metrics import enroll_account, pilot_stats

    runtime = Path(runtime)
    if runtime.exists() or users not in (5, 10) or delay_ms < 1 or active_limit < 1 or pending_limit < 1:
        raise ValueError('Use a new runtime, 5 or 10 accounts, and positive limits/delay')
    config = Settings(runtime=runtime, mode='fixture', auth_mode='pilot', cookie_secure=False,
                      data_profile='whut-2026', api_key='', demo_date='2026-10-18',
                      read_token=secrets.token_urlsafe(32), write_token=secrets.token_urlsafe(32),
                      planner_token=secrets.token_urlsafe(32), max_active_turns=active_limit,
                      max_pending_turns=pending_limit, requests_per_minute=10000)
    app = create_app(config, with_ui=False)
    engine = app.state.engine
    active, maximum = 0, 0

    async def no_discovery():
        pass

    async def delayed_stub(messages, tools):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(delay_ms / 1000)
            return {'role': 'assistant', 'content': '离线排队测试完成。'}, {
                'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        finally:
            active -= 1

    engine.discover_tools = no_discovery
    engine.model.respond = delayed_stub
    clients = []
    try:
        for index in range(users):
            username, password = f'loadtest{index:02}', secrets.token_urlsafe(24)
            engine.accounts.create(username, password)
            enroll_account(config, username, 'test', 'test')
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local.test')
            clients.append(client)
            response = await client.post('/v1/auth/login', json={'username': username, 'password': password})
            response.raise_for_status() if response.status_code != 303 else None
        replies = await asyncio.gather(*(c.post('/v1/chat', json={'message': '离线排队测试'}) for c in clients))
        if any(r.status_code not in (202, 429) for r in replies):
            raise RuntimeError('Unexpected chat response')
        await asyncio.wait_for(asyncio.gather(*list(engine.jobs)), timeout=30)
        turns = engine.db.all('turns')
        limited = [r for r in replies if r.status_code == 429]
        return {'transport': 'in_process_ASGI', 'model_mode': 'fixture', 'paid_model_calls': 0,
                'users': users, 'accepted': sum(r.status_code == 202 for r in replies),
                'completed': sum(t['status'] == 'completed' for t in turns), 'rate_limited': len(limited),
                'retry_after_present': bool(limited) and all('retry-after' in r.headers for r in limited),
                'queued_turns': sum(t.get('queue_wait_ms', 0) >= delay_ms / 2 for t in turns),
                'max_active_stub_calls': maximum, 'active_limit': active_limit, 'pending_limit': pending_limit,
                'queue_wait_ms': [t.get('queue_wait_ms') for t in turns],
                'end_to_end_ms': [t.get('total_latency_ms') for t in turns],
                'adoption': pilot_stats(config)['adoption'],
                'limitations': 'Test accounts, in-process HTTP only. No socket/TLS/browser, MCP/A2A or real-model load. '
                              'Not a measured remote concurrency limit.'}
    finally:
        for task in list(engine.jobs):
            task.cancel()
        await asyncio.gather(*list(engine.jobs), return_exceptions=True)
        await asyncio.gather(*(c.aclose() for c in clients))
