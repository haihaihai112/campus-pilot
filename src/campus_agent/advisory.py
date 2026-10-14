"""Source-grounded checklists; unknown school windows never become appointments."""
from datetime import date, datetime, timedelta
import uuid
from zoneinfo import ZoneInfo

from .models import Plan, PlanItem, PlanRequest

TZ = ZoneInfo('Asia/Shanghai')


def checklist(request: PlanRequest, services, evidence):
    if request.student_type != 'undergraduate' or request.cohort_year != 2026:
        raise ValueError('当前仅有2026级本科资料，不能为其他年份或研究生生成有效计划')
    day = date.fromisoformat(request.reference_date) if request.reference_date else request.arrival.astimezone(TZ).date() if request.arrival else None
    if day is None or day.year != 2026:
        raise ValueError('缺少2026年规划参考日期，或资料已超出适用范围')
    if request.campus_id not in {'whut', 'mafangshan', 'yujiatou', 'nanhu'}:
        raise ValueError('此计划不属于武汉理工大学资料范围')
    catalog = {s['id']: s for s in services}
    wanted = set(request.service_ids) - set(request.completed)
    for _ in range(20):
        expanded = wanted | {d for sid in wanted if sid in catalog for d in catalog[sid]['dependencies']
                             if d not in request.completed}
        if wanted == expanded:
            break
        wanted = expanded
    if len(wanted) > 20:
        raise ValueError('最多20项事项')
    if 'green_channel' in wanted and request.funding_route not in {'origin_loan', 'campus_loan'}:
        raise ValueError('请说明生源地贷款或校园地贷款路径；未知资格需向学校核实')
    reminders = {r.service_id: r for r in request.reminders}
    if len(reminders) != len(request.reminders) or set(reminders) - wanted:
        raise ValueError('提醒重复，或不属于本次未完成事项')
    remaining = set(wanted)
    done, items, unarranged = set(request.completed), [], []
    evidence_ids = {e.evidence_id for e in evidence}
    while remaining:
        ready = [sid for sid in remaining if sid not in catalog or set(catalog[sid]['dependencies']) <= done]
        if not ready:
            raise ValueError('清单前置事项无法满足')
        priority = {sid: rank for rank, sid in enumerate(['materials', 'volunteer', 'payment',
            'green_channel', 'online_registration', 'education', 'registration', 'dorm', 'card'])}
        sid = min(ready, key=lambda x: (catalog.get(x, {}).get('due_date') or '9999', priority.get(x, 10), x))
        remaining.remove(sid)
        spec = catalog.get(sid)
        if not spec:
            unarranged.append({'service_id': sid, 'reason': '没有可核实的办理资料'})
            done.add(sid)
            continue
        deadline = spec.get('due_date')
        if deadline and deadline < day.isoformat():
            unarranged.append({'service_id': sid, 'reason': f'2026级公开节点{deadline}已过，需向学院核实补办；不顺延学校截止日期'})
            done.add(sid)
            continue
        required_evidence = spec.get('evidence_ids', [spec['evidence_id']])
        if set(required_evidence) - evidence_ids:
            raise ValueError('缺少来源证据')
        materials = list(spec['materials'])
        if sid == 'green_channel':
            materials = ['生源地信用助学贷款合同', '回执单'] if request.funding_route == 'origin_loan' else [
                '手填的家庭经济困难学生认定申请表', '身份证复印件',
                '未满18周岁者按学校要求准备监护人材料及书面同意']
        reminder = reminders.get(sid)
        note = spec.get('guidance', '')
        if sid == 'registration' and day > date(2026, 9, 12):
            note += ' 公开集中报到日期已过，请先联系招生办027-87859017或027-87858399核实请假及补办，不承诺仍可入学。'
        if reminder:
            if not day <= reminder.start.astimezone(TZ).date() <= day + timedelta(days=7):
                raise ValueError('个人提醒必须在规划参考日期起7天内')
            if reminder.end.astimezone(TZ).date() > day + timedelta(days=7):
                raise ValueError('个人提醒超出7天范围')
            if deadline and reminder.end.astimezone(TZ).date().isoformat() > deadline:
                raise ValueError('个人提醒不得晚于学校公开截止日期')
            note += ' 该时间是用户指定的个人提醒，不是学校开放时段或预约。'
        items.append(PlanItem(item_id=sid, service_id=sid, title=spec['title'],
            location=spec['location'], location_name=spec['location_name'],
            dependencies=spec['dependencies'], materials=materials, evidence_ids=required_evidence,
            due_date=deadline, timing_note=note, kind='personal_reminder' if reminder else 'checklist',
            start=reminder.start if reminder else None, end=reminder.end if reminder else None))
        done.add(sid)
    return Plan(plan_id=request.plan_id or str(uuid.uuid4()), version=request.version,
        user_id=request.user_id, session_id=request.session_id, campus_id=request.campus_id,
        data_profile='whut-2026', conditions=request.model_dump(mode='json'), items=items,
        evidence=evidence, unarranged=unarranged,
        assumptions=['清单顺序为助手建议，不等同于学校强制办理顺序。',
                     '个人报到地点、窗口时间、办理时长、通行时间未知；不生成校方预约。',
                     '费用和资助资格以学校对个人的通知与认定为准；未缴费不等于不能报到。'],
        explanation='依据武汉理工大学2026级本科公开资料生成办理清单；截止日期不自动顺延。')


def validate_checklist(plan, services, evidence, events):
    try:
        request = PlanRequest.model_validate(plan.conditions)
        expected = checklist(request, services, evidence)
    except (ValueError, TypeError) as exc:
        return [str(exc)]
    errors = []
    if (plan.user_id, plan.session_id, plan.campus_id, plan.version) != (
            request.user_id, request.session_id, request.campus_id, request.version):
        errors.append('计划归属或版本与条件不一致')
    if plan.data_profile != 'whut-2026' or [i.model_dump() for i in plan.items] != [i.model_dump() for i in expected.items]:
        errors.append('清单内容、顺序或提醒与来源及用户条件不一致')
    authoritative = {e.evidence_id: e for e in evidence}
    actual = {e.evidence_id: e for e in plan.evidence}
    for item in plan.items:
        for eid in item.evidence_ids:
            if eid not in actual or eid not in authoritative or actual[eid] != authoritative[eid]:
                errors.append('来源证据不一致')
    timed = [i for i in plan.items if i.kind == 'personal_reminder' and i.start and i.end]
    for i, item in enumerate(timed):
        if request.available_windows and not any(a <= item.start < item.end <= b for a, b in request.available_windows):
            errors.append('个人提醒不在用户可用时间内')
        for other in timed[i + 1:]:
            if item.start < other.end and other.start < item.end:
                errors.append('个人提醒相互重叠')
        for event in events:
            if item.start < datetime.fromisoformat(event['end']) and datetime.fromisoformat(event['start']) < item.end:
                errors.append('个人提醒与已有日历冲突')
    return sorted(set(errors))
