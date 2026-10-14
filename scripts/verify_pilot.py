"""Zero-model-cost, real four-process pilot smoke test. Never writes login secrets to the report."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from campus_agent.access import Accounts
from campus_agent.config import Settings


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, help='New isolated test directory; not your pilot data')
    parser.add_argument('--output', required=True)
    parser.add_argument('--data-profile', choices=['whut-2026', 'synthetic'], default='whut-2026')
    parser.add_argument('--reference-date', default=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    args = parser.parse_args()
    root, output = Path(args.runtime).resolve(), Path(args.output).resolve()
    if root.exists() or output.exists():
        parser.error('Use new runtime and output paths; existing evidence is never overwritten')
    root.mkdir(parents=True)
    ports = {kind: free_port() for kind in ('rag', 'tools', 'planner', 'app')}
    tokens = {name: secrets.token_urlsafe(32) for name in ('MCP_READ_TOKEN', 'MCP_WRITE_TOKEN', 'A2A_TOKEN')}
    env = {**os.environ, **tokens, 'MODEL_MODE': 'fixture', 'AUTH_MODE': 'pilot', 'COOKIE_SECURE': 'false',
           'CAMPUS_RUNTIME': str(root), 'PYTHONUTF8': '1', 'AMAP_API_KEY': '', 'CALENDAR_PROVIDER': 'none',
           'CAMPUS_LOCATIONS_FILE': '', 'RETRIEVER_BACKEND': 'memory',
           'CAMPUS_DATA_PROFILE': args.data_profile, 'DEMO_DATE': args.reference_date,
           'RAG_MCP_URL': f'http://127.0.0.1:{ports["rag"]}/mcp',
           'CAMPUS_MCP_URL': f'http://127.0.0.1:{ports["tools"]}/mcp',
           'PLANNER_URL': f'http://127.0.0.1:{ports["planner"]}'}
    config = Settings(runtime=root, mode='fixture', auth_mode='pilot', cookie_secure=False,
                      data_profile=args.data_profile, demo_date=args.reference_date,
                      read_token=tokens['MCP_READ_TOKEN'], write_token=tokens['MCP_WRITE_TOKEN'],
                      planner_token=tokens['A2A_TOKEN'])
    password = secrets.token_urlsafe(24)
    accounts = Accounts(config)
    for username in ('verifyalice', 'verifybob'):
        accounts.create(username, password)
    processes, logs = [], []
    report = {'test_adapter': 'fixture', 'synthetic_data': args.data_profile == 'synthetic', 'paid_model_calls': 0,
              'data_profile': args.data_profile, 'reference_date': args.reference_date,
              'note': 'Two test accounts; not real user adoption or model-quality evidence', 'steps': []}
    try:
        for kind in ports:
            log = (root / (kind + '.log')).open('w', encoding='utf-8')
            logs.append(log)
            command = [sys.executable, '-m', 'campus_agent.cli', kind, '--port', str(ports[kind])]
            if kind == 'app':
                command.append('--no-ui')
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError(kind + ' failed; inspect isolated runtime logs')
                try:
                    if httpx.get(f'http://127.0.0.1:{ports[kind]}/health', timeout=2, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.2)
            else:
                raise TimeoutError(kind)
        base = f'http://127.0.0.1:{ports["app"]}'
        with httpx.Client(base_url=base, timeout=30, trust_env=False) as alice, \
             httpx.Client(base_url=base, timeout=30, trust_env=False) as bob:
            for client, username in ((alice, 'verifyalice'), (bob, 'verifybob')):
                assert client.post('/v1/auth/login', json={'username': username, 'password': password}).status_code == 303
            sid = None
            messages = ('明德校区报到材料是什么？', '帮我安排入学', '周六下午两点到明德校区，还没缴费。') if args.data_profile == 'synthetic' else (
                '2026级本科生报到需要什么材料？', '帮我规划入学清单', '我是2026级本科生，还没缴费，请安排报到入住清单。')
            for index, message in enumerate(messages):
                if index == 1:
                    sid = None  # Planning clarification must start without the QA question's scope.
                submitted = alice.post('/v1/chat', json={'message': message, 'session_id': sid}).json()
                sid = submitted['session_id']
                for _ in range(300):
                    turn = alice.get('/v1/turns/' + submitted['turn_id']).json()
                    if turn['status'] not in {'queued', 'working'}:
                        break
                    time.sleep(.1)
                else:
                    raise TimeoutError('turn')
                assert turn['status'] == 'completed'
                report['steps'].append({'operation': 'chat', 'turn': turn})
            assert report['steps'][0]['turn']['citations']
            assert '请补充' in report['steps'][1]['turn']['reply']
            assert turn['plan_id'] and turn['pending_actions']
            plan = alice.get('/v1/plans/' + turn['plan_id']).json()
            report['plan'] = plan
            before = alice.get('/v1/todos').json()
            assert before['data'] == []
            action = turn['pending_actions'][0]
            denied = bob.post('/v1/actions/' + action + '/confirm')
            assert denied.status_code == 404
            saved = alice.post('/v1/actions/' + action + '/confirm').json()
            repeated = alice.post('/v1/actions/' + action + '/confirm').json()
            assert saved['ok'] and repeated == saved
            todos = alice.get('/v1/todos').json()['data']
            assert len(todos) == saved['data']['todos_created']
            assert bob.get('/v1/todos').json()['data'] == []
            report['steps'].extend([{'operation': 'before_confirmation', 'todos': before['data']},
                                    {'operation': 'foreign_confirmation', 'http_status': denied.status_code},
                                    {'operation': 'confirm', 'result': saved},
                                    {'operation': 'repeat_confirmation', 'result': repeated},
                                    {'operation': 'personal_todos', 'count': len(todos)},
                                    {'operation': 'other_account_todos', 'count': 0}])
            report['checks_passed'] = True
    except BaseException as exc:
        report['checks_passed'] = False
        report['error'] = type(exc).__name__
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
        for log in logs:
            log.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'checks_passed': report['checks_passed'], 'output': str(output), 'paid_model_calls': 0}))


if __name__ == '__main__':
    main()
