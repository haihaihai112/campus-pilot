from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sqlite3
import subprocess
import sys

import uvicorn

from .config import ROOT, Settings
from .seed import seed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['seed', 'rag', 'tools', 'planner', 'app', 'all', 'accounts', 'stats', 'init-pilot',
                                          'enroll', 'review-turn'])
    parser.add_argument('action', nargs='?', choices=['add', 'list', 'disable'])
    parser.add_argument('username', nargs='?')
    parser.add_argument('--port', type=int)
    parser.add_argument('--base-port', type=int, default=8010, help='For all: app=N, planner=N+10, rag=N+20, tools=N+30')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--no-ui', action='store_true')
    parser.add_argument('--fixture', action='store_true', help='Explicit deterministic test adapter; not a live model')
    parser.add_argument('--username', dest='enroll_username')
    parser.add_argument('--cohort', choices=['participant', 'test', 'developer', 'historical_demo', 'unclassified'])
    parser.add_argument('--purpose', choices=['current_need', 'historical_demo', 'test', 'unclassified'])
    parser.add_argument('--turn-id')
    parser.add_argument('--outcome', choices=['resolved', 'unresolved'])
    args = parser.parse_args()
    if args.fixture:
        os.environ['MODEL_MODE'] = 'fixture'
    if args.command == 'all':
        os.environ['PLANNER_URL'] = f'http://127.0.0.1:{args.base_port + 10}'
        os.environ['RAG_MCP_URL'] = f'http://127.0.0.1:{args.base_port + 20}/mcp'
        os.environ['CAMPUS_MCP_URL'] = f'http://127.0.0.1:{args.base_port + 30}/mcp'
    config = Settings()
    if args.command == 'enroll':
        from .metrics import enroll_account
        if not args.enroll_username or not args.cohort or not args.purpose:
            parser.error('enroll requires --username, --cohort and --purpose')
        print(json.dumps(enroll_account(config, args.enroll_username, args.cohort, args.purpose), ensure_ascii=False))
        return
    if args.command == 'review-turn':
        from .metrics import review_turn
        if not args.turn_id or not args.outcome:
            parser.error('review-turn requires --turn-id and --outcome')
        print(json.dumps(review_turn(config, args.turn_id, args.outcome), ensure_ascii=False))
        return
    if args.command == 'init-pilot':
        path = ROOT / '.env.pilot'
        # Exclusive creation: never overwrite an operator's secrets or settings.
        with path.open('x', encoding='utf-8') as out:
            out.write('AUTH_MODE=pilot\nMODEL_MODE=fixture\nCOOKIE_SECURE=false\n'
                      'LLM_MODEL=deepseek-chat\nLLM_BASE_URL=https://api.deepseek.com/v1\n'
                      'LLM_API_KEY=\nCAMPUS_DATA_PROFILE=whut-2026\nRETRIEVER_BACKEND=memory\n'
                      '# Local HTTP only. Set COOKIE_SECURE=true behind HTTPS for remote users.\n')
            for name in ('MCP_READ_TOKEN', 'MCP_WRITE_TOKEN', 'A2A_TOKEN'):
                out.write(name + '=' + secrets.token_urlsafe(32) + '\n')
        print('Created .env.pilot with private service credentials; set CAMPUS_ENV_FILE to its path.')
        return
    if args.command == 'accounts':
        from .access import Accounts
        accounts = Accounts(config)
        if args.action == 'list':
            print(json.dumps(accounts.listing(), ensure_ascii=False, indent=2))
        elif args.action in {'add', 'disable'} and args.username:
            if args.action == 'disable':
                accounts.disable(args.username)
                print('Account disabled; all login sessions revoked.')
            else:
                password = getpass.getpass('Password (12+ characters): ')
                if password != getpass.getpass('Confirm password: '):
                    parser.error('Passwords do not match')
                try:
                    uid = accounts.create(args.username, password)
                except (ValueError, sqlite3.IntegrityError) as exc:
                    parser.error(str(exc))
                print('Account created: ' + uid)
        else:
            parser.error('Usage: campus accounts add|disable USERNAME, or campus accounts list')
        return
    if args.command == 'stats':
        from .metrics import pilot_stats
        print(json.dumps(pilot_stats(config), ensure_ascii=False, indent=2))
        return
    if args.command == 'seed':
        seed(config.runtime / 'data', config.data_profile)
        print('Campus data initialized: ' + config.data_profile)
        return
    if args.command == 'all':
        # Publish all documents before launching concurrent readers on Windows.
        # Mismatched profiles fail here, rather than leaving a partially running stack.
        seed(config.runtime / 'data', config.data_profile)
        children = []
        try:
            for command in ['rag', 'tools', 'planner', 'app']:
                ports = {'app': args.base_port, 'planner': args.base_port + 10, 'rag': args.base_port + 20, 'tools': args.base_port + 30}
                child_host = args.host if command == 'app' else '127.0.0.1'
                child_args = [sys.executable, '-m', 'campus_agent.cli', command, '--host', child_host, '--port', str(ports[command])]
                if args.no_ui and command == 'app':
                    child_args.append('--no-ui')
                children.append(subprocess.Popen(child_args, cwd=ROOT))
            for child in children:
                child.wait()
        except KeyboardInterrupt:
            pass
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                child.wait(timeout=15)
        return
    if args.command in ('rag', 'tools'):
        from .mcp_servers import create_mcp
        app = create_mcp('rag' if args.command == 'rag' else 'campus', config)
        port = 8030 if args.command == 'rag' else 8040
    elif args.command == 'planner':
        from .planner import create_planner
        app, port = create_planner(config), 8020
    else:
        from .api import create_app
        app, port = create_app(config, not args.no_ui), 8010
    uvicorn.run(app, host=args.host, port=args.port or port, log_level='warning')


if __name__ == '__main__':
    main()
