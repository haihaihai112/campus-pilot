from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from a2a.client import A2ACardResolver
from a2a.client.transports import JsonRpcTransport
from a2a.types import (Message, MessageSendConfiguration,
                       MessageSendParams, Part, DataPart, TaskIdParams, TaskQueryParams)

from .config import Settings
from .models import failure


class MCPGateway:
    def __init__(self, config: Settings, writer=False, trace=None):
        self.config, self.writer = config, writer
        self.trace = trace if trace is not None else []

    async def _request(self, service, name=None, arguments=None):
        url = self.config.rag_url if service == 'rag' else self.config.campus_url
        token = self.config.write_token if self.writer and service == 'campus' else self.config.read_token
        timeout = 60 if name == 'sync_plan_calendar' else 45 if service == 'rag' else 15
        async with httpx.AsyncClient(headers={'Authorization': f'Bearer {token}'}, timeout=timeout) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
                    await session.initialize()
                    if name is None:
                        result = await session.list_tools()
                        return result.tools
                    result = await session.call_tool(name, arguments or {})
                    if result.isError:
                        return failure('tool_error', '；'.join(x.text for x in result.content if hasattr(x, 'text')))
                    if result.structuredContent is not None:
                        return result.structuredContent
                    return json.loads(next(x.text for x in result.content if hasattr(x, 'text')))

    async def discover(self, service):
        return await asyncio.wait_for(self._request(service), timeout=15)

    async def call(self, name, arguments):
        service = 'rag' if name == 'search_knowledge' else 'campus'
        began = time.perf_counter()
        result = None
        # Write retries are safe ONLY because their operation_id remains unchanged.
        for attempt in range(2):
            try:
                timeout = 60 if name == 'sync_plan_calendar' else 45 if service == 'rag' else 15
                result = await asyncio.wait_for(self._request(service, name, arguments), timeout)
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt == 1:
                    result = failure('unavailable', f'{service} 服务调用失败：{type(exc).__name__}')
        self.trace.append({'protocol': 'MCP', 'tool': name,
                           'arguments': {k: ('[位置已脱敏]' if k in {'origin', 'destination', 'keywords'} else
                                            '[绑定已脱敏]' if k == 'provider_binding' else v)
                                         for k, v in arguments.items() if k not in {'snapshot', 'user_id'}},
                           'status': 'ok' if result.get('ok') else 'error',
                           'latency_ms': round((time.perf_counter() - began) * 1000, 2)})
        return result


class PlannerClient:
    def __init__(self, config: Settings):
        self.config = config

    async def _call(self, operation, payload=None, task_id=None, context_id=None):
        async with httpx.AsyncClient(timeout=20, headers={'Authorization': f'Bearer {self.config.planner_token}'}) as http:
            card = await A2ACardResolver(http, self.config.planner_url).get_agent_card()
            # Do not trust a discovered URL to redirect credentials to another service.
            if card.url.rstrip('/') != self.config.planner_url.rstrip('/'):
                raise ValueError('Agent Card URL does not match configured planner')
            client = JsonRpcTransport(http, agent_card=card)
            if operation == 'send':
                message = Message(role='user', message_id=str(uuid.uuid4()), task_id=task_id, context_id=context_id,
                                  parts=[Part(root=DataPart(data=payload))])
                response = await client.send_message(MessageSendParams(
                    message=message, configuration=MessageSendConfiguration(blocking=False)))
            elif operation == 'get':
                response = await client.get_task(TaskQueryParams(id=task_id))
            else:
                response = await client.cancel_task(TaskIdParams(id=task_id))
            return response.model_dump(mode='json', by_alias=True)

    async def submit(self, payload, task_id=None, context_id=None):
        return await self._call('send', payload, task_id, context_id)

    async def get(self, task_id):
        return await self._call('get', task_id=task_id)

    async def cancel(self, task_id):
        return await self._call('cancel', task_id=task_id)


def task_text(task):
    message = task.get('status', {}).get('message') or {}
    return '\n'.join(p.get('text', '') for p in message.get('parts', []) if p.get('kind') == 'text')


def task_artifact(task):
    for artifact in task.get('artifacts') or []:
        for part in artifact.get('parts', []):
            if part.get('kind') == 'data' and 'plan' in part.get('data', {}):
                return part['data']
    return None
