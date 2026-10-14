"""Reproducible development evaluation. Fixture runs never measure model capability."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta

from campus_agent.config import ROOT, Settings
from campus_agent.models import Evidence, PlanRequest
from campus_agent.planner import extract_constraints
from campus_agent.protocols import MCPGateway, PlannerClient, task_artifact
from campus_agent.scheduler import schedule, validate_plan
from campus_agent.seed import build_seed


def create_cases():
    cases = []
    for campus in ('mingde', 'zhiyuan'):
        for hour in (9, 11, 14, 16, 18):
            for variant in ('standard', 'busy', 'restricted'):
                arrival = f'2026-09-05T{hour:02}:00:00+08:00'
                events = [] if variant != 'busy' else [{'start': arrival,
                    'end': f'2026-09-05T{hour+1:02}:00:00+08:00', 'location': 'gym'}]
                request = {'session_id': f'eval-{campus}-{hour}-{variant}', 'request': '安排报到和入住',
                           'campus_id': campus, 'arrival': arrival}
                if variant == 'restricted':
                    request['available_windows'] = [[arrival, f'2026-09-05T{hour:02}:10:00+08:00']]
                cases.append({'id': request['session_id'], 'category': variant, 'request': request, 'events': events,
                              'expected': 'empty_with_reasons' if variant == 'restricted' else 'valid_schedule'})
    return cases


async def local_pipeline(config, request):
    trace, usage = [], {}
    gateway = MCPGateway(config, trace=trace)
    request = await extract_constraints(config, request, gateway, usage)
    if not request.arrival or not request.campus_id:
        return {'state': 'input-required', 'usage': usage, 'trace': trace}
    day = request.arrival.date()
    specs = await gateway.call('get_service_info', {'campus_id': request.campus_id,
        'start_date': day.isoformat(), 'end_date': (day + timedelta(days=7)).isoformat()})
    travel = await gateway.call('get_travel_times', {'campus_id': request.campus_id})
    events = await gateway.call('list_calendar_events', {'start_date': day.isoformat(),
        'end_date': (day + timedelta(days=7)).isoformat(), 'user_id': request.user_id})
    knowledge = await gateway.call('search_knowledge', {'query': request.request, 'campus_id': request.campus_id,
                                                       'as_of_date': day.isoformat(), 'top_k': 5})
    if not all(r['ok'] for r in (specs, travel, events, knowledge)):
        raise RuntimeError('MCP dependency unavailable')
    evidence = {d['evidence_id']: Evidence.model_validate(d) for d in specs['data']['evidence']}
    evidence.update({d['evidence_id']: Evidence.model_validate(d) for d in knowledge['data']['evidence']})
    plan = schedule(request, specs['data']['services'], travel['data']['minutes'], events['data'], list(evidence.values()))
    errors = validate_plan(plan, specs['data']['services'], travel['data']['minutes'], events['data'])
    if config.mode == 'live':
        from campus_agent.llm import Model
        reply, consumed = await Model(config).respond([
            {'role': 'system', 'content': '简短解释此计划的取舍和未安排原因，不得改变时间地点，不声称已保存。'},
            {'role': 'user', 'content': plan.model_dump_json()}], [])
        plan.explanation = reply['content']
        for k, value in consumed.items():
            usage[k] = usage.get(k, 0) + value
    return {'plan': plan.model_dump(mode='json'), 'errors': errors, 'usage': usage, 'trace': trace}


async def ablation(config):
    # Explicit evaluation-only direct path. Production ALWAYS delegates through A2A.
    request = PlanRequest(session_id='ablation', user_id='eval-user', request='安排报到入住',
                          campus_id='mingde', arrival='2026-09-05T14:00:00+08:00')
    started = time.perf_counter()
    local = await local_pipeline(config, request)
    local['latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    client = PlannerClient(config)
    task = await client.submit(request.model_dump(mode='json'))
    for _ in range(400):
        if task['status']['state'] in ('completed', 'input-required', 'failed', 'canceled'):
            break
        await asyncio.sleep(0.2)
        task = await client.get(task['id'])
    remote = task_artifact(task) or {'state': task['status']['state']}
    remote['latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
    local_items = local.get('plan', {}).get('items')
    remote_items = remote.get('plan', {}).get('items')
    return {'model_mode': config.mode, 'local': local, 'a2a': remote,
            'same_schedule': bool(local_items) and local_items == remote_items,
            'interpretation': '单次开发对照；时延包含协议和轮询开销，不构成性能结论。fixture不评估模型能力。'}


def audit_legacy():
    rows = []
    for path in sorted((ROOT / 'evaluation' / 'legacy').glob('*.jsonl')):
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        ids = [r['id'] for r in records]
        rows.append({'file': path.name, 'count': len(records), 'duplicate_ids': len(ids) - len(set(ids)),
                     'missing_required_fields': sum(not all(k in r for k in ('id', 'question', 'reference')) for r in records),
                     'eligibility': 'archived_only',
                     'reason': '原评测引用旧语料，包含报到时间、领钥匙凭证等已改变事实；未重标前不用于新系统准确率。'})
    return rows


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ablation', action='store_true')
    parser.add_argument('--output', default='reports/evaluation.json')
    args = parser.parse_args()
    config = Settings()
    if config.data_profile != 'synthetic':
        parser.error('Historical scheduler evaluation requires CAMPUS_DATA_PROFILE=synthetic and matching services; never score WHUT with synthetic labels')
    cases = create_cases()
    (ROOT / 'evaluation' / 'scheduler_30.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    data = build_seed()
    evidence = [Evidence.model_validate(d) for d in data['documents']]
    records = []
    for case in cases:
        request = PlanRequest.model_validate(case['request'])
        started = time.perf_counter()
        plan = schedule(request, data['services'], data['travel'], case['events'], evidence)
        errors = validate_plan(plan, data['services'], data['travel'], case['events'])
        expectation = (bool(plan.items) if case['expected'] == 'valid_schedule' else not plan.items and bool(plan.unarranged))
        # Controlled injected violation: demonstrates what the post-validator detects, not LLM quality.
        corrupted = plan.model_copy(deep=True)
        if corrupted.items:
            corrupted.items[0].start = datetime.fromisoformat(request.arrival.date().isoformat() + 'T03:00:00+08:00')
        corruption_errors = validate_plan(corrupted, data['services'], data['travel'], case['events'])
        records.append({'id': case['id'], 'passed': not errors and bool(expectation), 'errors': errors,
                        'latency_ms': round((time.perf_counter() - started) * 1000, 3),
                        'plan': plan.model_dump(mode='json'),
                        'validation_ablation': {'kind': 'controlled_fault_injection',
                            'enabled_rejects': bool(corruption_errors), 'disabled_would_accept': bool(corrupted.items)}})
    output = {'scope': 'deterministic_scheduler_development_set', 'records': records,
              'passed': sum(r['passed'] for r in records), 'total': len(records),
              'legacy_audit': audit_legacy(), 'semantic_answer_accuracy': None,
              'note': '此30条为约束排程开发用例；问答、追问、确认与故障场景另由协议测试和task_scenarios_30.json覆盖。未产生人工语义评分。'}
    if args.ablation:
        try:
            output['a2a_ablation'] = await ablation(config)
        except Exception as exc:
            output['a2a_ablation'] = {'status': 'failed', 'error_type': type(exc).__name__}
    path = ROOT / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': output['passed'], 'total': output['total'],
                      'ablation_same_schedule': output.get('a2a_ablation', {}).get('same_schedule')}))


if __name__ == '__main__':
    asyncio.run(main())
