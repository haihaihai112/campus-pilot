import argparse
import json
import time
from pathlib import Path

import httpx


def wait_turn(client, base, task_id):
    for _ in range(400):
        value = client.get(f'{base}/v1/turns/{task_id}').json()
        if value['status'] not in ('queued', 'working'):
            return value
        time.sleep(0.5)
    raise TimeoutError('turn did not complete')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8010')
    parser.add_argument('--output', default='runtime/smoke.json')
    parser.add_argument('--save', action='store_true')
    args = parser.parse_args()
    records = []
    # Local-only target: ignore ambient HTTP_PROXY so a system proxy cannot break loopback calls.
    with httpx.Client(timeout=30, trust_env=False) as client:
        health = client.get(args.url + '/health').json()
        print(json.dumps(health, ensure_ascii=True))
        sid = None
        for message in ['新生报到需要带哪些材料？', '帮我安排报到和入住', '周六下午两点到明德校区，还没有缴费。']:
            response = client.post(args.url + '/v1/chat', json={'message': message, 'session_id': sid}).json()
            sid = response['session_id']
            record = wait_turn(client, args.url, response['turn_id'])
            records.append(record)
            print(json.dumps({'status': record['status'], 'reply': record['reply'], 'trace': record['trace']}, ensure_ascii=True))
        if args.save:
            for action in records[-1]['pending_actions']:
                records.append({'confirmation': client.post(args.url + '/v1/actions/' + action + '/confirm').json()})
                records.append({'duplicate_confirmation': client.post(args.url + '/v1/actions/' + action + '/confirm').json()})
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'health': health, 'records': records}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
