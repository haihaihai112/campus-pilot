from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Evidence, Plan, PlanItem, PlanRequest

TZ = ZoneInfo('Asia/Shanghai')


def windows_for(service, day):
    key = day.isoformat()
    raw = service.get('exceptions', {}).get(key)
    if raw is None:
        kind = 'sunday' if day.weekday() == 6 else 'saturday' if day.weekday() == 5 else 'weekday'
        raw = service['windows'][kind]
    return [(datetime.fromisoformat(f'{key}T{a}').replace(tzinfo=TZ),
             datetime.fromisoformat(f'{key}T{b}').replace(tzinfo=TZ)) for a, b in raw]


def schedule(request: PlanRequest, services: list[dict], travel: dict[str, int], events: list[dict],
             evidence: list[Evidence]) -> Plan:
    if not request.arrival or not request.campus_id:
        raise ValueError('校区和带时区的到达时间是规划必需条件')
    arrival = request.arrival.astimezone(TZ)
    limit = arrival + timedelta(days=7)
    catalog = {s['id']: s for s in services if s['campus_id'] == request.campus_id}
    wanted = set(request.service_ids) - set(request.completed)
    # Expand dependencies, with an explicit size bound.
    for _ in range(20):
        expanded = wanted | {d for sid in wanted if sid in catalog for d in catalog[sid]['dependencies']
                             if d not in request.completed}
        if expanded == wanted:
            break
        wanted = expanded
    if len(wanted) > 20:
        raise ValueError('计划最多包含20项事项')
    missing = [{'service_id': s, 'reason': '没有该事项的有效校园记录'} for s in sorted(wanted) if s not in catalog]
    remaining = set(wanted) & set(catalog)
    done = set(request.completed)
    cursor, location = arrival, 'gate'
    busy = sorted([(datetime.fromisoformat(e['start']), datetime.fromisoformat(e['end']), e['location'])
                   for e in events], key=lambda x: x[0])
    items = []
    while remaining:
        ready = sorted([s for s in remaining if set(catalog[s]['dependencies']) <= done],
                       key=lambda s: (catalog[s].get('deadline', ''),
                                      {'payment': 0, 'registration': 1, 'dorm': 2, 'card': 3}.get(s, 10), s))
        if not ready:
            missing.extend({'service_id': s, 'reason': '前置事项未完成、无法安排或存在循环依赖'} for s in sorted(remaining))
            break
        sid = ready[0]
        spec = catalog[sid]
        remaining.remove(sid)
        duration = timedelta(minutes=spec['minutes'])
        deadline = min(datetime.fromisoformat(spec['deadline']), limit)
        placed = None
        for offset in range(8):
            day = (arrival + timedelta(days=offset)).date()
            for opening, closing in windows_for(spec, day):
                ranges = [(opening, closing)]
                if request.available_windows:
                    ranges = [(max(opening, a), min(closing, b)) for a, b in request.available_windows
                              if max(opening, a) < min(closing, b)]
                for left, right in ranges:
                    start = max(cursor + timedelta(minutes=travel.get(f'{location}:{spec["location"]}', 30)), left)
                    # Account for travel on both sides of an existing event.
                    for event_start, event_end, event_location in busy:
                        if event_end <= cursor:
                            continue
                        to_event = timedelta(minutes=travel.get(f'{spec["location"]}:{event_location}', 30))
                        from_event = timedelta(minutes=travel.get(f'{event_location}:{spec["location"]}', 30))
                        if start + duration + to_event <= event_start:
                            continue
                        if start >= event_end + from_event:
                            continue
                        start = max(start, event_end + from_event)
                    end = start + duration
                    if end <= min(right, deadline):
                        placed = (start, end)
                        break
                if placed:
                    break
            if placed:
                break
        if placed is None:
            missing.append({'service_id': sid, 'reason': '未来7天内没有满足开放时间、个人空闲时间和通行约束的时段'})
            continue
        start, end = placed
        items.append(PlanItem(item_id=sid, service_id=sid, title=spec['title'], location=spec['location'],
                              location_name=spec.get('location_name', spec['location']),
                              start=start, end=end, dependencies=spec['dependencies'],
                              evidence_ids=[spec['evidence_id']], materials=spec['materials']))
        done.add(sid)
        cursor, location = end, spec['location']
    return Plan(plan_id=request.plan_id or str(uuid.uuid4()), version=request.version, user_id=request.user_id,
                session_id=request.session_id, campus_id=request.campus_id,
                conditions=request.model_dump(mode='json'), items=items, unarranged=missing, evidence=evidence,
                assumptions=['办理时长与步行时间采用合成演示数据；不考虑实时排队。',
                             '个人偏好若无法转为明确时间约束，仅保留在条件中，不视为已满足。'])


def validate_plan(plan: Plan, services, travel, events) -> list[str]:
    """Independent postcondition checks; used by planner and immediately before writes."""
    errors = []
    request = PlanRequest.model_validate(plan.conditions)
    if not request.arrival:
        return ['缺少到达时间']
    catalog = {s['id']: s for s in services if s['campus_id'] == plan.campus_id}
    done = set(request.completed)
    cursor, location = request.arrival, 'gate'
    seen = set()
    evidence_ids = {e.evidence_id for e in plan.evidence}
    for item in sorted(plan.items, key=lambda x: x.start):
        spec = catalog.get(item.service_id)
        if not spec or item.item_id in seen:
            errors.append('未知或重复事项')
            continue
        seen.add(item.item_id)
        if not item.start.tzinfo or not item.end.tzinfo:
            errors.append('日程缺少时区')
            continue
        if item.location != spec['location'] or item.end - item.start != timedelta(minutes=spec['minutes']):
            errors.append('地点或办理时长与来源不符')
        if set(spec['dependencies']) - done:
            errors.append('前置依赖未满足')
        if item.start < cursor + timedelta(minutes=travel.get(f'{location}:{item.location}', 30)):
            errors.append('通行时间不足或日程重叠')
        if item.end > request.arrival + timedelta(days=7) or item.end > datetime.fromisoformat(spec['deadline']):
            errors.append('超出规划期限')
        if not any(a <= item.start < item.end <= b for a, b in windows_for(spec, item.start.date())):
            errors.append('不在窗口开放时间内')
        if request.available_windows and not any(a <= item.start < item.end <= b for a, b in request.available_windows):
            errors.append('不在用户可用时间内')
        if spec['evidence_id'] not in item.evidence_ids or set(item.evidence_ids) - evidence_ids:
            errors.append('证据引用缺失或无效')
        for e in events:
            a, b = datetime.fromisoformat(e['start']), datetime.fromisoformat(e['end'])
            if b <= request.arrival:
                continue
            before = timedelta(minutes=travel.get(f'{item.location}:{e["location"]}', 30))
            after = timedelta(minutes=travel.get(f'{e["location"]}:{item.location}', 30))
            if not (item.end + before <= a or item.start >= b + after):
                errors.append('与已有日程或通行时间冲突')
        done.add(item.service_id)
        cursor, location = item.end, item.location
    return sorted(set(errors))
