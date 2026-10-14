"""Real-model behavioural evaluation over question sets owned by this project.

Measures only what the recorded turn data makes checkable: tool success, citation
guard behaviour, completion, clarification, latency and token usage. It does NOT
produce semantic accuracy, RAGAS or human fact-support scores; those need a set
re-labelled against the current knowledge base.

Sets:
  refusal       evaluation/legacy/dataset_refusal_10.jsonl (10 labelled items)
  qa            task_scenarios_30.json category == qa (answers exist in the KB)
  clarification task_scenarios_30.json T11 then T12 in one session
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time

import httpx

from campus_agent.config import ROOT

CITATION_GUARD = '生成结果引用了无法核实的资料编号'
GROUNDING_GUARD = '已检索到资料，但生成结果未提供可核查引用'
REFUSAL_MARKERS = ('资料', '未收录', '没有', '不足', '无法', '以', '为准', '不清楚', '查不到')
FABRICATION_PATTERNS = (r'\d+\s*人', r'\d+(?:\.\d+)?\s*%', r'[1-9]\d{3,}\s*元')


def load_sets():
    scenarios = json.loads((ROOT / 'evaluation' / 'task_scenarios_30.json').read_text(encoding='utf-8'))
    refusal = [json.loads(line) for line in
               (ROOT / 'evaluation' / 'legacy' / 'dataset_refusal_10.jsonl').read_text(encoding='utf-8').splitlines()
               if line.strip()]
    qa = [s for s in scenarios if s['category'] == 'qa']
    pair = [s for s in scenarios if s['id'] in ('T11', 'T12')]
    return refusal, qa, sorted(pair, key=lambda s: s['id'])


def wait_turn(client, base, turn_id, limit=600):
    for _ in range(limit):
        turn = client.get(f'{base}/v1/turns/{turn_id}').json()
        if turn['status'] not in ('queued', 'working'):
            return turn
        time.sleep(0.5)
    raise TimeoutError(f'turn {turn_id} did not finish')


def run_turn(client, base, message, session_id=None):
    started = time.perf_counter()
    response = client.post(base + '/v1/chat', json={'message': message, 'session_id': session_id}).json()
    turn = wait_turn(client, base, response['turn_id'])
    return response['session_id'], turn, round((time.perf_counter() - started) * 1000, 1)


def score_refusal(reply):
    """Mechanical pre-screen only; the stored reply is the evidence for human review."""
    admits_gap = any(marker in reply for marker in REFUSAL_MARKERS)
    fabricated = [pattern for pattern in FABRICATION_PATTERNS if re.search(pattern, reply)]
    return {'admits_gap': admits_gap, 'fabricated_patterns': fabricated,
            'preliminary_pass': bool(admits_gap and not fabricated)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8010')
    parser.add_argument('--output', default='reports/live-eval-deepseek.json')
    parser.add_argument('--sets', default='refusal,qa,clarification')
    args = parser.parse_args()
    wanted = {name.strip() for name in args.sets.split(',')}
    refusal, qa, pair = load_sets()
    records = []
    # Local-only target: ignore ambient HTTP_PROXY so a system proxy cannot break loopback calls.
    with httpx.Client(timeout=60, trust_env=False) as client:
        health = client.get(args.url + '/health').json()
        if health.get('model_mode') != 'live' or health.get('data_profile') != 'synthetic':
            parser.error('This historical evaluation requires a live-model synthetic stack. WHUT needs separately labelled questions.')
        if wanted & {'refusal'}:
            for item in refusal:
                _, turn, wall = run_turn(client, args.url, item['question'])
                records.append({'set': 'refusal', 'id': item['id'], 'prompt': item['question'],
                                'reference': item['reference'], 'wall_ms': wall, 'turn': turn,
                                'refusal_score': score_refusal(turn['reply'])})
        if wanted & {'qa'}:
            for item in qa:
                _, turn, wall = run_turn(client, args.url, item['prompt'])
                records.append({'set': 'qa', 'id': item['id'], 'prompt': item['prompt'],
                                'assertion': item['assertion'], 'wall_ms': wall, 'turn': turn})
        if wanted & {'clarification'}:
            session = None
            for item in pair:
                session, turn, wall = run_turn(client, args.url, item['prompt'], session)
                records.append({'set': 'clarification', 'id': item['id'], 'prompt': item['prompt'],
                                'session_id': session, 'wall_ms': wall, 'turn': turn})

    calls = [call for record in records for call in record['turn']['trace'] if call.get('protocol') == 'MCP']
    searches = [record for record in records if any(
        call.get('tool') == 'search_knowledge' for call in record['turn']['trace'])]
    guarded = [record for record in records if record['turn']['reply'].startswith(CITATION_GUARD)]
    ungrounded = [record for record in records if record['turn']['reply'].startswith(GROUNDING_GUARD)]
    grounded = [record for record in records if record['turn']['citations']]
    citation_ids = {cid for record in records for cid in record['turn']['citations']}
    evidence_ids = {e['evidence_id'] for record in records for e in record['turn']['evidence']}
    refusal_scored = [record['refusal_score'] for record in records if record.get('refusal_score')]
    latencies = [record['turn']['latency_ms'] for record in records if record['turn'].get('latency_ms')]
    usage_total = {}
    for record in records:
        for key, value in (record['turn'].get('usage') or {}).items():
            usage_total[key] = usage_total.get(key, 0) + value

    summary = {
        'scope': 'real_model_behavioural_evaluation',
        'model_mode': health.get('model_mode'), 'model': health.get('model'),
        'turns': len(records), 'sets': sorted(wanted),
        'tool_calls': len(calls),
        'tool_success': sum(1 for call in calls if call.get('status') == 'ok'),
        'tool_success_rate': round(sum(1 for call in calls if call.get('status') == 'ok') / len(calls), 4) if calls else None,
        'turns_with_search': len(searches),
        'citation_guard_fired': len(guarded), 'grounding_guard_fired': len(ungrounded),
        'citation_guard_rate': round(len(guarded) / len(searches), 4) if searches else None,
        'turns_with_verified_citation': len(grounded),
        'citations_outside_evidence': len(citation_ids - evidence_ids),
        'abnormal_turns': [record['id'] for record in records if record['turn']['status'] != 'completed'],
        'refusal_items': len(refusal_scored),
        'refusal_preliminary_pass': sum(1 for score in refusal_scored if score['preliminary_pass']),
        'clarification_first_turn_asked': [record['id'] for record in records
                                           if record['set'] == 'clarification' and not record['turn'].get('plan_id')],
        'latency_ms': {'median': round(statistics.median(latencies), 1),
                       'max': round(max(latencies), 1)} if latencies else None,
        'usage_total': usage_total,
        'not_measured': ['semantic answer accuracy', 'RAGAS / LLM-as-judge scores', 'human fact-support rate',
                         'forbidden write-tool attempts (the guard blocks without writing a trace entry)'],
        'note': '意图是记录真实模型可被机械核查的行为；拒答项仅做机械初筛，逐条回复已存档供人工判读。',
    }
    output = {'summary': summary, 'records': records}
    path = ROOT / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
