import httpx
import pytest

from campus_agent.api import create_app
from campus_agent.config import Settings
from test_pilot import PASSWORD, pilot_config


async def test_tunnel_origin_preserves_login_and_csrf(tmp_path):
    config = pilot_config(tmp_path)
    # Simulate TLS termination with absent or incorrect forwarded scheme.
    config.public_origin = 'https://invite.example'
    config.cookie_secure = True
    app = create_app(config, with_ui=False)
    app.state.engine.accounts.create('alice', PASSWORD)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://invite.example') as client:
        headers = {'Origin': 'https://invite.example', 'X-Forwarded-Proto': 'http'}
        response = await client.post('/v1/auth/login', headers=headers,
                                     json={'username': 'alice', 'password': PASSWORD})
        assert response.status_code == 303
        assert 'secure' in response.headers['set-cookie'].lower()
        assert (await client.post('/v1/auth/login', headers={'Origin': 'https://evil.example'},
                                 json={'username': 'alice', 'password': PASSWORD})).status_code == 403
        assert (await client.get('/login', headers={'Host': 'evil.example'})).status_code == 421
        page = await client.get('/login')
        assert 'action="https://invite.example/login"' in page.text
        assert 'max-age=' in page.headers['strict-transport-security']


@pytest.mark.parametrize('origin', ['http://invite.example', 'https://user:pass@invite.example',
                                  'https://invite.example/path', 'https://invite.example?query=1'])
def test_invalid_public_origin_rejected(tmp_path, monkeypatch, origin):
    monkeypatch.setenv('CAMPUS_PUBLIC_ORIGIN', origin)
    with pytest.raises(ValueError, match='CAMPUS_PUBLIC_ORIGIN'):
        Settings(runtime=tmp_path, mode='fixture', cookie_secure=True)
