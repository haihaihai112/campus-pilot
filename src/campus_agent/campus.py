from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from icalendar import Calendar, Event

from .config import Settings
from .db import Store, dumps
from .models import PersonalTodo, Plan, failure, success
from .scheduler import validate_plan
from .seed import load_seed
from .advisory import validate_checklist
from .models import Evidence


class Campus:
    def __init__(self, config: Settings):
        self.config = config
        self.data = load_seed(config.runtime / 'data', config.data_profile)
        self.store = Store(config.runtime / 'campus.sqlite')

    def service_info(self, campus_id: str, service_ids: list[str] | None = None, start_date: str | None = None, end_date: str | None = None, student_type='undergraduate', cohort_year=2026):
        if campus_id not in self.data['campuses']:
            return failure('not_found', '未知校区')
        start_date = start_date or self.config.demo_date
        end_date = end_date or start_date
        a, b = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if student_type != 'undergraduate' or cohort_year != 2026:
            return failure('not_found', '没有该学生类型或入学年份的已核实资料')
        if self.config.data_profile == 'whut-2026' and a <= b and a.year == b.year == 2026:
            services = [s for s in self.data['services'] if not service_ids or s['id'] in service_ids]
            ids = {eid for s in services for eid in s.get('evidence_ids', [s['evidence_id']])}
            return success({'services': services, 'evidence': [d for d in self.data['documents'] if d['evidence_id'] in ids],
                'notice': '公开规定与助手建议清单；窗口、办理时长及个人地点未知，不是预约接口'})
        if a > b or a.year != 2026 or b.year != 2026:
            return failure('not_found', '当前资料仅适用于2026年，且开始日期不能晚于结束日期')
        services = [s for s in self.data['services'] if s['campus_id'] == campus_id
                    and (not service_ids or s['id'] in service_ids)]
        ids = {s['evidence_id'] for s in services}
        return success({'services': services, 'evidence': [d for d in self.data['documents'] if d['evidence_id'] in ids]})

    def travel(self, campus_id: str, location_ids: list[str] | None = None):
        if campus_id not in self.data['campuses']:
            return failure('not_found', '未知校区')
        values = {k: v for k, v in self.data['travel'].items()
                  if not location_ids or all(x in location_ids for x in k.split(':'))}
        return success({'minutes': values, 'synthetic': self.config.data_profile == 'synthetic',
                        'notice': '没有已核实的通行时间，不以默认值替代' if not values else '合成测试数据'})

    def todos(self, user_id='demo-user'):
        rows = [v for v in self.store.all('todos') if v['user_id'] == user_id]
        ids = {eid for row in rows for eid in row.get('evidence_ids', [])}
        # Resolve stored IDs against this service's current source set, never trust
        # a model-supplied ID or treat a personal todo as a school policy source.
        return {**success(rows), 'evidence': [d for d in self.data['documents'] if d['evidence_id'] in ids]}

    def create_todo(self, title, operation_id, user_id='demo-user', due_date=None, start=None, end=None):
        try:
            todo = PersonalTodo(title=title, due_date=due_date, start=start, end=end).checked()
        except ValueError:
            return failure('invalid_arguments', '标题、日期或提醒起止时间无效')
        payload = dumps(['create_personal_todo', user_id, todo.model_dump(mode='json')])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.store.get('operations', operation_id, con=db)
            if old:
                return old['result'] if old['digest'] == payload else failure('conflict', '幂等键内容不一致')
            key = 'personal:' + operation_id
            row = {'id': key, 'user_id': user_id, 'plan_id': None, 'plan_version': None,
                   **todo.model_dump(mode='json'), 'kind': 'personal_reminder' if todo.start else 'personal_todo',
                   'status': 'pending', 'version': 1, 'materials': [], 'evidence_ids': [],
                   'location': '', 'location_name': '', 'timing_note': '用户指定的个人事项，不是校方预约或截止政策'}
            self.store.put('todos', key, row, db)
            if todo.start:
                self.store.put('events', key, row, db)
            result = success({'todo_id': key, 'todos_created': 1, 'events_created': int(bool(todo.start))})
            self.store.put('operations', operation_id, {'digest': payload, 'result': result}, db)
            return result

    def events(self, user_id='demo-user', start_date=None, end_date=None, exclude_plan_id=None, con=None):
        values = [v for v in self.store.all('events', con) if v['user_id'] == user_id
                  and (exclude_plan_id is None or v['plan_id'] != exclude_plan_id)]
        if start_date:
            values = [v for v in values if v['end'][:10] >= start_date]
        if end_date:
            values = [v for v in values if v['start'][:10] <= end_date]
        return success(values)

    def apply(self, snapshot: dict, operation_id: str):
        plan = Plan.model_validate(snapshot)
        digest = hashlib.sha256(dumps(plan.model_dump(mode='json')).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self.store.get('operations', operation_id, con=db)
            if previous:
                if previous['digest'] != digest:
                    return failure('conflict', '幂等键已经用于不同内容')
                return previous['result']
            current = self.store.get('plans', plan.plan_id, con=db)
            if current and (current['user_id'] != plan.user_id or current['version'] >= plan.version):
                return failure('conflict', '计划版本已过期或该版本已保存')
            events = self.events(plan.user_id, exclude_plan_id=plan.plan_id, con=db)['data']
            if self.config.data_profile == 'whut-2026':
                errors = validate_checklist(plan, self.data['services'],
                    [Evidence.model_validate(d) for d in self.data['documents']], events)
            elif plan.data_profile != 'synthetic':
                errors = ['计划与运行数据配置不一致']
            else:
                errors = validate_plan(plan, self.data['services'], self.data['travel'], events)
            if errors or not plan.validated:
                return failure('conflict', '计划未通过执行前校验：' + '；'.join(errors))
            if not plan.items:
                return failure('invalid_arguments', '计划没有可保存事项')
            for kind in ('todos', 'events'):
                for row in self.store.all(kind, db):
                    if row['plan_id'] == plan.plan_id:
                        db.execute('DELETE FROM kv WHERE kind=? AND id=?', (kind, row['id']))
            for item in plan.items:
                key = f'{plan.plan_id}:{item.item_id}'
                common = {'id': key, 'user_id': plan.user_id, 'plan_id': plan.plan_id,
                          'plan_version': plan.version, **item.model_dump(mode='json')}
                self.store.put('todos', key, {**common, 'status': 'pending', 'version': 1}, db)
                if item.start and item.end:
                    self.store.put('events', key, common, db)
            self.store.put('plans', plan.plan_id, plan.model_dump(mode='json'), db)
            result = success({'plan_id': plan.plan_id, 'version': plan.version,
                              'todos_created': len(plan.items),
                              'events_created': sum(bool(i.start and i.end) for i in plan.items)})
            self.store.put('operations', operation_id, {'digest': digest, 'result': result}, db)
            return result

    def update_todo(self, todo_id: str, status: str, expected_version: int, operation_id: str, user_id='demo-user'):
        if status not in {'pending', 'completed'}:
            return failure('invalid_arguments', '不支持的待办状态')
        payload = dumps([todo_id, status, expected_version, user_id])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self.store.get('operations', operation_id, con=db)
            if old:
                return old['result'] if old['digest'] == payload else failure('conflict', '幂等键内容不一致')
            todo = self.store.get('todos', todo_id, con=db)
            if not todo or todo['user_id'] != user_id:
                return failure('not_found', '待办不存在')
            if todo['version'] != expected_version:
                return failure('conflict', '待办已更新，请刷新')
            todo.update(status=status, version=expected_version + 1)
            self.store.put('todos', todo_id, todo, db)
            result = success(todo)
            self.store.put('operations', operation_id, {'digest': payload, 'result': result}, db)
            return result

    def export(self, user_id='demo-user', start_date=None, end_date=None):
        calendar = Calendar()
        calendar.add('prodid', '-//CampusPilot//Demo Calendar//CN')
        calendar.add('version', '2.0')
        for row in self.events(user_id, start_date, end_date)['data']:
            event = Event()
            event.add('uid', row['id'] + '@campus.local')
            event.add('dtstamp', datetime.now(timezone.utc))
            event.add('dtstart', datetime.fromisoformat(row['start']).astimezone(timezone.utc))
            event.add('dtend', datetime.fromisoformat(row['end']).astimezone(timezone.utc))
            event.add('summary', ('个人提醒：' if row.get('kind') == 'personal_reminder' else '') + row['title'])
            event.add('location', row.get('location_name') or row['location'])
            event.add('description', ('用户指定的个人提醒，不是校方预约；' if self.config.data_profile == 'whut-2026' else '合成校园数据；') + '材料：' + '、'.join(row['materials']))
            calendar.add_component(event)
        return success({'filename': 'campus-calendar.ics', 'content': calendar.to_ical().decode('utf-8')})
