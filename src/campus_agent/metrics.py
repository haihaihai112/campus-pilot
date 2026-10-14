"""Local-only usage summary; accounts created are not counted as active users."""
from collections import Counter
from datetime import datetime, timezone

from .db import Store


def pilot_stats(config):
    db = Store(config.runtime / 'assistant.sqlite')
    turns, feedback, actions = db.all('turns'), db.all('feedback'), db.all('actions')
    modes = {}
    for mode in ('fixture', 'live'):
        rows = [t for t in turns if t.get('model_mode') == mode]
        users = {t.get('user_id', 'demo-user') for t in rows}
        times = sorted(t['latency_ms'] for t in rows if t.get('status') == 'completed' and 'latency_ms' in t)
        turn_ids = {t['id'] for t in rows}
        ratings = Counter(f['rating'] for f in feedback if f['turn_id'] in turn_ids)
        modes[mode] = {'unique_users_with_turns': len(users), 'turns': len(rows),
                       'turn_states': dict(Counter(t['status'] for t in rows)),
                       'reported_tokens': sum(t.get('usage', {}).get('total_tokens', 0) for t in rows),
                       'completed_latency_p50_ms': times[len(times) // 2] if times else None,
                       'feedback_records': dict(ratings)}
    return {'product': 'CampusPilot', 'sampled_at': datetime.now(timezone.utc).isoformat(),
            'synthetic_data': True, 'model_modes': modes,
            'action_states': dict(Counter(a['status'] for a in actions)),
            'definitions': {'unique_users_with_turns': 'distinct server-side identities that submitted a turn; not accounts created',
                            'feedback': 'latest voluntary rating per user and turn; not answer accuracy',
                            'latency': 'completed turn processing time, excluding queue wait; not a load-test result'}}
