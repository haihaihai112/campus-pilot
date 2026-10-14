from contextvars import ContextVar
import secrets

from starlette.responses import JSONResponse

role = ContextVar('service_role', default='none')


class TokenMiddleware:
    def __init__(self, app, read_token: str, write_token: str | None = None, public_paths=()):
        self.app, self.read_token, self.write_token = app, read_token, write_token
        self.public_paths = public_paths

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') in self.public_paths:
            return await self.app(scope, receive, send)
        authorization = dict(scope['headers']).get(b'authorization', b'').decode()
        token = authorization.removeprefix('Bearer ')
        access = 'none'
        if self.write_token and secrets.compare_digest(token, self.write_token):
            access = 'write'
        elif secrets.compare_digest(token, self.read_token):
            access = 'read'
        if access == 'none':
            return await JSONResponse({'error': 'unauthorized'}, status_code=401)(scope, receive, send)
        marker = role.set(access)
        try:
            await self.app(scope, receive, send)
        finally:
            role.reset(marker)
