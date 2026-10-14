import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from campus_agent.config import Settings


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Stack:
    def __init__(self, root, auth_mode='demo', with_ui=False, data_profile='synthetic', reference_date='2026-09-05'):
        self.root = root
        self.with_ui = with_ui
        self.ports = {kind: free_port() for kind in ('rag', 'tools', 'planner', 'app')}
        self.env = {**os.environ, 'MODEL_MODE': 'fixture', 'CAMPUS_RUNTIME': str(root), 'PYTHONUTF8': '1',
                    'AMAP_API_KEY': '', 'CALENDAR_PROVIDER': 'none', 'CAMPUS_LOCATIONS_FILE': '',
                    'AUTH_MODE': auth_mode, 'COOKIE_SECURE': 'false',
                    'CAMPUS_DATA_PROFILE': data_profile, 'DEMO_DATE': reference_date,
                    'MCP_READ_TOKEN': 'test-read-' + 'r' * 32,
                    'MCP_WRITE_TOKEN': 'test-write-' + 'w' * 32,
                    'A2A_TOKEN': 'test-planner-' + 'p' * 32,
                    'RAG_MCP_URL': f'http://127.0.0.1:{self.ports["rag"]}/mcp',
                    'CAMPUS_MCP_URL': f'http://127.0.0.1:{self.ports["tools"]}/mcp',
                    'PLANNER_URL': f'http://127.0.0.1:{self.ports["planner"]}'}
        self.config = Settings(runtime=root, mode='fixture', rag_url=self.env['RAG_MCP_URL'],
                               campus_url=self.env['CAMPUS_MCP_URL'], planner_url=self.env['PLANNER_URL'],
                               auth_mode=auth_mode, cookie_secure=False, data_profile=data_profile, demo_date=reference_date,
                               read_token=self.env['MCP_READ_TOKEN'], write_token=self.env['MCP_WRITE_TOKEN'],
                               planner_token=self.env['A2A_TOKEN'])
        self.processes, self.logs = {}, {}
        self.base = f'http://127.0.0.1:{self.ports["app"]}'

    def start(self, kind):
        self.logs[kind] = (self.root / f'{kind}.log').open('a', encoding='utf-8')
        command = [sys.executable, '-m', 'campus_agent.cli', kind, '--port', str(self.ports[kind])]
        if kind == 'app' and not self.with_ui:
            command.append('--no-ui')
        self.processes[kind] = subprocess.Popen(command, env=self.env, stdout=self.logs[kind], stderr=subprocess.STDOUT)
        for _ in range(120):
            if self.processes[kind].poll() is not None:
                raise RuntimeError((self.root / f'{kind}.log').read_text(encoding='utf-8'))
            try:
                if httpx.get(f'http://127.0.0.1:{self.ports[kind]}/health', timeout=3).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise TimeoutError(kind)

    def stop(self, kind):
        process = self.processes.get(kind)
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if kind in self.logs:
            self.logs[kind].close()


@pytest.fixture(scope='module')
def stack(tmp_path_factory):
    server = Stack(tmp_path_factory.mktemp('protocol-stack'))
    try:
        for kind in ('rag', 'tools', 'planner', 'app'):
            server.start(kind)
        yield server
    finally:
        for kind in ('app', 'planner', 'tools', 'rag'):
            server.stop(kind)


@pytest.fixture(autouse=True)
def legacy_regression_profile(monkeypatch):
    # Baseline scheduling tests intentionally retain synthetic, frozen data.
    # WHUT tests explicitly pass their profile; test mode never chooses data implicitly.
    monkeypatch.setenv('CAMPUS_DATA_PROFILE', 'synthetic')
    monkeypatch.setenv('DEMO_DATE', '2026-09-05')
