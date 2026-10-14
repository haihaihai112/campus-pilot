from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable

from pydantic import Field, field_validator, model_validator

from ..config import Settings
from ..db import Store, dumps
from ..models import Plan, StrictModel, failure, success


class CalendarEvent(StrictModel):
    event_id: str
    title: str
    start: datetime
    end: datetime
    location: str = ''
    location_code: str = 'external'
    managed_plan_id: str | None = None

    @model_validator(mode='after')
    def valid_interval(self):
        if not self.start.tzinfo or not self.end.tzinfo or self.start >= self.end:
            raise ValueError('calendar events require timezone and start < end')
        return self


class CalendarWrite(StrictModel):
    title: str
    start: datetime
    end: datetime
    location: str
    description: str
    reminder_minutes: int = Field(default=30, ge=0, le=10080)
    managed_plan_id: str

    @field_validator('start', 'end')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError('timezone required')
        return value


class CalendarProvider(ABC):
    """Implement in trusted application code, never load a provider name supplied by the model.

    binding_id identifies provider+account+calendar without exposing access tokens.
    list_events must return ALL expanded events overlapping the interval (handle pagination/recurrence).
    upsert_event must reconcile stable_key before creating, including after an uncertain network result.
    Implement OAuth storage/refresh in the concrete provider; never return credentials to the agent.
    """
    name: str
    binding_id: str

    @abstractmethod
    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...

    @abstractmethod
    async def upsert_event(self, event: CalendarWrite, stable_key: str,
                           idempotency_key: str, existing_event_id: str | None) -> str: ...


# Deliberately empty: the user requested an adapter interface without choosing a platform.
PROVIDERS: dict[str, Callable[[Settings], CalendarProvider]] = {}


class CalendarService:
    def __init__(self, config: Settings, campus, provider: CalendarProvider | None = None):
        self.config, self.campus = config, campus
        factory = PROVIDERS.get(config.calendar_provider)
        self.provider = provider or (factory(config) if factory else None)
        self.store = Store(config.runtime / 'calendar-sync.sqlite')
        self.lock = asyncio.Lock()

    def status(self):
        if not self.provider:
            return {'status': 'not_connected', 'provider': None, 'binding_id': None,
                    'message': '日历适配接口已建立，尚未绑定具体平台或账户。'}
        return {'status': 'configured_unverified', 'provider': self.provider.name,
                'binding_id': self.provider.binding_id,
                'message': '适配器已配置，需实际调用验证授权和连接。'}

    async def list_events(self, start: str, end: str):
        if not self.provider:
            return failure('not_connected', '个人日历尚未绑定平台。当前只能查看本地日程或导出ICS。')
        try:
            a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
            if not a.tzinfo or not b.tzinfo or not a < b or b - a > timedelta(days=31):
                raise ValueError('invalid interval')
        except ValueError:
            return failure('invalid_arguments', '日历查询需带时区、开始早于结束，且范围不超过31天。')
        try:
            rows = await asyncio.wait_for(self.provider.list_events(a, b), timeout=10)
            values = [CalendarEvent.model_validate(r).model_dump(mode='json') for r in rows]
            return success({'provider': self.provider.name, 'binding_id': self.provider.binding_id, 'events': values})
        except Exception:
            return failure('unavailable', '外部日历查询失败；不能将其视为空闲日程。')

    async def sync_plan(self, plan_id: str, plan_version: int, provider_binding: str,
                        reminder_minutes: int, operation_id: str, user_id='demo-user'):
        if not self.provider:
            return failure('not_connected', '个人日历未绑定平台，未进行任何外部写入。')
        if self.provider.binding_id != provider_binding:
            return failure('conflict', '日历绑定已改变，请重新确认目标日历。')
        if not 0 <= reminder_minutes <= 10080:
            return failure('invalid_arguments', '提醒分钟数必须在0至10080之间。')
        digest = hashlib.sha256(dumps([plan_id, plan_version, provider_binding, reminder_minutes, user_id]).encode()).hexdigest()
        async with self.lock:
            previous = self.store.get('operations', operation_id)
            if previous and previous['digest'] != digest:
                return failure('conflict', '幂等键已用于不同同步请求。')
            if previous and previous.get('result', {}).get('ok'):
                return previous['result']
            stored = self.campus.store.get('plans', plan_id)
            if not stored or stored['user_id'] != user_id:
                return failure('not_found', '请先保存属于当前用户的本地计划，再同步日历。')
            if stored['version'] != plan_version:
                return failure('conflict', '本地计划版本已变化，请重新预览并确认。')
            plan = Plan.model_validate(stored)
            record = previous or {'digest': digest, 'completed': {}}
            self.store.put('operations', operation_id, record)
            try:
                items = sorted(plan.items, key=lambda i: i.start)
                if not items:
                    return failure('invalid_arguments', '没有可同步的日程。')
                existing = await asyncio.wait_for(self.provider.list_events(
                    items[0].start - timedelta(hours=1), max(i.end for i in items) + timedelta(hours=1)), timeout=10)
                for raw in existing:
                    event = CalendarEvent.model_validate(raw)
                    if event.managed_plan_id == plan_id:
                        continue
                    if any(item.start < event.end and event.start < item.end for item in items):
                        return failure('conflict', '外部日历中已有重叠事项，请重新规划；本次未开始额外写入。')
                for item in items:
                    current = self.campus.store.get('plans', plan_id)
                    if not current or current['version'] != plan_version:
                        raise ValueError('local plan changed during sync')
                    key = hashlib.sha256(f'{provider_binding}:{plan_id}:{item.item_id}'.encode()).hexdigest()
                    if key in record['completed']:
                        continue
                    mapping = self.store.get('events', key)
                    event = CalendarWrite(title=item.title, start=item.start, end=item.end,
                        location=item.location_name or item.location,
                        description='入学安排；所需材料：' + '、'.join(item.materials),
                        reminder_minutes=reminder_minutes, managed_plan_id=plan_id)
                    event_id = await asyncio.wait_for(self.provider.upsert_event(event, key,
                        f'{operation_id}:{item.item_id}', mapping['event_id'] if mapping else None), timeout=10)
                    if not event_id:
                        raise ValueError('provider returned no event ID')
                    mapping = {'event_id': event_id, 'plan_id': plan_id, 'item_id': item.item_id,
                               'version': plan_version, 'binding_id': provider_binding}
                    with self.store.connect() as db:
                        self.store.put('events', key, mapping, db)
                        record['completed'][key] = event_id
                        self.store.put('operations', operation_id, record, db)
            except Exception:
                result = failure('partial_failure' if record['completed'] else 'unavailable',
                    f"日历同步未全部完成，已确认成功 {len(record['completed'])} 项；重试会沿用原操作编号。")
                record['result'] = result
                self.store.put('operations', operation_id, record)
                return result
            current_ids = {item.item_id for item in plan.items}
            retained = [v['item_id'] for v in self.store.all('events')
                        if v['plan_id'] == plan_id and v['binding_id'] == provider_binding and v['item_id'] not in current_ids]
            result = success({'provider': self.provider.name, 'plan_id': plan_id, 'plan_version': plan_version,
                              'synced_events': len(record['completed']), 'retained_remote_items': retained,
                              'notice': '旧版中被移除的远程事项不会自动删除；请在日历中检查。' if retained else
                                        '提醒由所接日历平台执行，具体送达受平台与客户端设置影响。'})
            record['result'] = result
            self.store.put('operations', operation_id, record)
            return result
