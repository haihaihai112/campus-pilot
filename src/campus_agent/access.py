"""Invite-only pilot accounts and opaque, revocable login sessions.

Identity comes from the server-side login, never from model arguments or UI state.
Internal MCP/A2A credentials remain private service credentials.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from .db import Store

COOKIE = 'campuspilot_session'
PBKDF2_ROUNDS = 600_000


class Accounts:
    def __init__(self, config):
        self.config = config
        self.db = Store(config.runtime / 'accounts.sqlite')
        with self.db.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS logins (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS limits (
                    category TEXT, identity TEXT, window INTEGER, count INTEGER NOT NULL,
                    PRIMARY KEY(category, identity, window));
            ''')
        self.dummy_salt = secrets.token_bytes(16)

    def create(self, username, password):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{3,40}', username):
            raise ValueError('用户名须为3至40位字母、数字、下划线或短横线')
        if len(password) < 12 or len(password) > 256:
            raise ValueError('密码须为12至256个字符')
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ROUNDS).hex()
        uid = 'pilot-' + secrets.token_hex(16)
        with self.db.connect() as con:
            con.execute('INSERT INTO accounts VALUES(?,?,?,?,1,?)',
                        (uid, username, salt.hex(), digest, time.time()))
        return uid

    def consume(self, category, identity, maximum, seconds):
        """Atomic fixed-window counter; survives process restarts."""
        window = int(time.time()) // seconds
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT count FROM limits WHERE category=? AND identity=? AND window=?',
                              (category, identity, window)).fetchone()
            if row and row['count'] >= maximum:
                raise HTTPException(429, '请求过于频繁，请稍后重试', headers={'Retry-After': str(seconds)})
            con.execute('INSERT INTO limits VALUES(?,?,?,1) ON CONFLICT(category,identity,window) '
                        'DO UPDATE SET count=count+1', (category, identity, window))
            con.execute('DELETE FROM limits WHERE category=? AND window<?', (category, window - 2))

    async def login(self, username, password, client_ip):
        self.consume('login-ip', client_ip, 10, 60)
        self.consume('login-name', username.lower(), 10, 60)
        with self.db.connect() as con:
            row = con.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
        salt = bytes.fromhex(row['salt']) if row else self.dummy_salt
        digest = await asyncio.to_thread(hashlib.pbkdf2_hmac, 'sha256', password.encode(), salt, PBKDF2_ROUNDS)
        if not row or not hmac.compare_digest(digest.hex(), row['password_hash']) or not row['active']:
            raise HTTPException(401, '用户名或密码错误')
        token = secrets.token_urlsafe(32)
        expiry = time.time() + self.config.login_ttl_hours * 3600
        with self.db.connect() as con:
            con.execute('DELETE FROM logins WHERE expires_at<?', (time.time(),))
            con.execute('INSERT INTO logins VALUES(?,?,?)',
                        (hashlib.sha256(token.encode()).hexdigest(), row['user_id'], expiry))
        return token

    def resolve(self, token):
        if not token or len(token) > 256:
            return None
        with self.db.connect() as con:
            row = con.execute('SELECT a.user_id FROM logins l JOIN accounts a ON a.user_id=l.user_id '
                              'WHERE token_hash=? AND expires_at>? AND a.active=1',
                              (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return row['user_id'] if row else None

    def revoke(self, token):
        with self.db.connect() as con:
            con.execute('DELETE FROM logins WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def disable(self, username):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT user_id FROM accounts WHERE username=?', (username,)).fetchone()
            if not row:
                raise ValueError('账号不存在')
            con.execute('UPDATE accounts SET active=0 WHERE user_id=?', (row['user_id'],))
            con.execute('DELETE FROM logins WHERE user_id=?', (row['user_id'],))

    def listing(self):
        with self.db.connect() as con:
            return [dict(r) for r in con.execute('SELECT user_id,username,active,created_at FROM accounts')]


def owned(engine, kind, key, user_id):
    row = engine.db.get(kind, key)
    owner = row.get('user_id') if row else None
    if row and owner is None and row.get('session_id'):
        session = engine.db.get('sessions', row['session_id'])
        owner = session.get('user_id', 'demo-user') if session else None
    if not row or (owner or 'demo-user') != user_id:
        raise HTTPException(404, '记录不存在')
    return row


def request_user(request, config):
    if config.auth_mode == 'demo':
        return 'demo-user'
    # Gradio injects the username returned by the mount auth_dependency.
    user_id = getattr(request, 'username', None) or getattr(getattr(request, 'state', None), 'user_id', None)
    if not user_id:
        raise HTTPException(401, '请先登录')
    return user_id


class PilotMiddleware:
    def __init__(self, app, config, accounts):
        self.app, self.config, self.accounts = app, config, accounts

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            if scope['type'] == 'websocket' and self.config.auth_mode == 'pilot':
                await send({'type': 'websocket.close', 'code': 1008})
                return
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = scope.get('path', '')
        if self.config.auth_mode == 'demo':
            scope.setdefault('state', {})['user_id'] = 'demo-user'
            return await self.app(scope, receive, send)
        # Cookie requests must not be initiated by a different origin, including
        # Gradio queue submissions and login (login CSRF). Bearer API clients
        # do not use ambient browser credentials.
        auth = request.headers.get('authorization', '')
        origin = request.headers.get('origin')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not auth.startswith('Bearer '):
            expected = urlsplit(str(request.base_url))
            received = urlsplit(origin) if origin else None
            same = received and (received.scheme, received.netloc) == (expected.scheme, expected.netloc)
            if (origin and not same) or request.headers.get('sec-fetch-site') == 'cross-site':
                return await JSONResponse({'detail': '跨站请求被拒绝'}, status_code=403)(scope, receive, send)
        if path in {'/health', '/login', '/v1/auth/login'}:
            return await self.app(scope, receive, send)
        token = auth[7:] if auth.startswith('Bearer ') else request.cookies.get(COOKIE, '')
        uid = self.accounts.resolve(token)
        if not uid:
            if path in {'/', '/ui', '/ui/'}:
                return await RedirectResponse('/login', status_code=303)(scope, receive, send)
            return await JSONResponse({'detail': '请先登录'}, status_code=401)(scope, receive, send)
        scope.setdefault('state', {}).update(user_id=uid, login_token=token)
        try:
            self.accounts.consume('http', uid, self.config.requests_per_minute, 60)
        except HTTPException as exc:
            return await JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                      headers=exc.headers)(scope, receive, send)
        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'cache-control']
                message = {**message, 'headers': headers + [(b'cache-control', b'private, no-store')]}
            await send(message)
        return await self.app(scope, receive, private_send)
