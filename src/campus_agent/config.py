from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(Path(os.getenv('CAMPUS_ENV_FILE', str(ROOT / '.env'))))


def _bypass_proxy_for_loopback() -> None:
    """Keep ambient system proxies (VPN/Clash) away from this app's own services.

    RAG, tools, planner and app all talk to 127.0.0.1. httpx and the official
    MCP/A2A SDKs honour HTTP_PROXY from the environment, so with a system proxy
    running those local calls get tunnelled and fail. External model calls must
    keep using the proxy, so only the loopback hosts are appended here.
    """
    loopback = {'127.0.0.1', 'localhost', '::1'}
    current = os.getenv('NO_PROXY') or os.getenv('no_proxy') or ''
    existing = {item.strip() for item in current.split(',') if item.strip()}
    if loopback <= existing:
        return
    merged = ','.join(sorted(existing | loopback))
    os.environ['NO_PROXY'] = merged
    os.environ['no_proxy'] = merged


_bypass_proxy_for_loopback()


@dataclass
class Settings:
    data_profile: str = field(default_factory=lambda: os.getenv('CAMPUS_DATA_PROFILE', 'whut-2026'))
    runtime: Path = field(default_factory=lambda: Path(os.getenv('CAMPUS_RUNTIME', str(ROOT / ('runtime-' + os.getenv('CAMPUS_DATA_PROFILE', 'whut-2026'))))))
    mode: str = field(default_factory=lambda: os.getenv('MODEL_MODE', 'live'))
    model: str = field(default_factory=lambda: os.getenv('LLM_MODEL', 'deepseek-chat'))
    api_key: str = field(default_factory=lambda: os.getenv('LLM_API_KEY') or os.getenv('DASHSCOPE_API_KEY', ''))
    base_url: str = field(default_factory=lambda: os.getenv('LLM_BASE_URL', 'https://api.deepseek.com/v1'))
    demo_date: str = field(default_factory=lambda: os.getenv('DEMO_DATE') or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    rag_url: str = field(default_factory=lambda: os.getenv('RAG_MCP_URL', 'http://127.0.0.1:8030/mcp'))
    campus_url: str = field(default_factory=lambda: os.getenv('CAMPUS_MCP_URL', 'http://127.0.0.1:8040/mcp'))
    planner_url: str = field(default_factory=lambda: os.getenv('PLANNER_URL', 'http://127.0.0.1:8020'))
    backend: str = field(default_factory=lambda: os.getenv('RETRIEVER_BACKEND', 'memory'))
    read_token: str = field(default_factory=lambda: os.getenv('MCP_READ_TOKEN', 'local-demo-read'))
    write_token: str = field(default_factory=lambda: os.getenv('MCP_WRITE_TOKEN', 'local-demo-write'))
    planner_token: str = field(default_factory=lambda: os.getenv('A2A_TOKEN', 'local-demo-a2a'))
    amap_api_key: str = field(default_factory=lambda: os.getenv('AMAP_API_KEY', ''))
    campus_locations_file: str = field(default_factory=lambda: os.getenv('CAMPUS_LOCATIONS_FILE', ''))
    calendar_provider: str = field(default_factory=lambda: os.getenv('CALENDAR_PROVIDER', 'none'))
    auth_mode: str = field(default_factory=lambda: os.getenv('AUTH_MODE', 'demo'))
    cookie_secure: bool = field(default_factory=lambda: os.getenv('COOKIE_SECURE', 'true').lower() == 'true')
    login_ttl_hours: int = field(default_factory=lambda: int(os.getenv('LOGIN_TTL_HOURS', '24')))
    requests_per_minute: int = field(default_factory=lambda: int(os.getenv('REQUESTS_PER_MINUTE', '240')))
    turns_per_hour: int = field(default_factory=lambda: int(os.getenv('TURNS_PER_HOUR', '20')))
    max_active_turns: int = field(default_factory=lambda: int(os.getenv('MAX_ACTIVE_TURNS', '4')))
    max_pending_turns: int = field(default_factory=lambda: int(os.getenv('MAX_PENDING_TURNS', '32')))
    max_pending_per_user: int = field(default_factory=lambda: int(os.getenv('MAX_PENDING_PER_USER', '2')))

    def __post_init__(self):
        self.runtime.mkdir(parents=True, exist_ok=True)
        if self.data_profile not in {'synthetic', 'whut-2026'}:
            raise ValueError('CAMPUS_DATA_PROFILE must be whut-2026 or synthetic')
        if self.mode not in {'live', 'fixture'}:
            raise ValueError('MODEL_MODE must be live or fixture')
        if self.auth_mode not in {'demo', 'pilot'}:
            raise ValueError('AUTH_MODE must be demo or pilot')
        for name in ('login_ttl_hours', 'requests_per_minute', 'turns_per_hour',
                     'max_active_turns', 'max_pending_turns', 'max_pending_per_user'):
            if getattr(self, name) < 1:
                raise ValueError(f'{name} must be positive')
        if self.auth_mode == 'pilot':
            tokens = (self.read_token, self.write_token, self.planner_token)
            if len(set(tokens)) != 3 or any(len(t) < 24 or t.startswith('local-demo-') for t in tokens):
                raise ValueError('Pilot mode requires three distinct private MCP/A2A tokens (24+ characters)')


def settings() -> Settings:
    return Settings()
