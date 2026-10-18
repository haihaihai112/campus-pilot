from __future__ import annotations

import json
import re
import uuid
import time
from urllib.parse import urlsplit
from datetime import datetime, timedelta

import httpx
from openai import AsyncOpenAI

from .config import Settings
from .telemetry import MODEL_CALLS


def function_tool(name, description, schema):
    return {'type': 'function', 'function': {'name': name, 'description': description, 'parameters': schema}}


class Model:
    def __init__(self, config: Settings):
        self.config = config
        self.client = None
        if config.mode == 'live':
            if not config.api_key:
                raise ValueError('真实模型模式需要 LLM_API_KEY；无密钥测试请显式设置 MODEL_MODE=fixture')
            # One recorded invocation per outbound attempt; implicit retries hide billable failures.
            self.client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url, timeout=60, max_retries=0,
                                      http_client=httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(local_address='0.0.0.0'), timeout=60))

    async def respond(self, messages, tools):
        if self.config.mode == 'fixture':
            return fixture_response(messages, tools, self.config), {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        extra = {'extra_body': {'enable_thinking': False}} if 'dashscope.aliyuncs.com' in self.config.base_url else {}
        record = {'call_id': uuid.uuid4().hex, 'requested_model': self.config.model, 'response_model': None,
                  'endpoint_host': urlsplit(self.config.base_url).hostname,
                  'status': 'failed', 'usage_reported': False,
                  'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None}
        began = time.perf_counter()
        try:
            response = await self.client.chat.completions.create(
                model=self.config.model, messages=messages, tools=tools or None, temperature=0.1,
                max_tokens=1800, **extra)
            record.update(response_model=response.model, status='completed', usage_reported=response.usage is not None)
            if response.usage is not None:
                record.update({k: getattr(response.usage, k, None)
                    for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')})
        except BaseException as exc:
            record['error_type'] = type(exc).__name__
            raise
        finally:
            record['latency_ms'] = round((time.perf_counter() - began) * 1000, 2)
            collector = MODEL_CALLS.get()
            if collector is not None:
                collector.append(record)
        message = response.choices[0].message
        value = {'role': 'assistant', 'content': message.content or ''}
        if message.tool_calls:
            value['tool_calls'] = [c.model_dump(exclude_none=True) for c in message.tool_calls]
        usage = response.usage
        return value, {k: int(getattr(usage, k, 0) or 0) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}


def tool_message(name, args):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'fixture-' + uuid.uuid4().hex,
            'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}


def infer_fixture(text, config):
    """Deterministic test adapter only. It does not claim language-model capability."""
    campus = 'zhiyuan' if '致远' in text or 'zhiyuan' in text else 'mingde' if '明德' in text or 'mingde' in text else None
    if config.data_profile == 'whut-2026':
        campus = next((sid for word, sid in [('马房山', 'mafangshan'), ('余家头', 'yujiatou'), ('南湖', 'nanhu')]
                       if word in text), 'whut')
    arrival = None
    match = re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\+08:00)?', text)
    if match:
        if config.data_profile == 'synthetic' or any(w in text for w in ('到校', '到达', '抵达')):
            arrival = match.group() + ('' if '+08:00' in match.group() else '+08:00')
    elif '周六' in text or '星期六' in text:
        day = datetime.fromisoformat(config.demo_date)
        day += timedelta(days=(5 - day.weekday()) % 7)
        arrival = day.strftime('%Y-%m-%d') + ('T18:00:00+08:00' if '六点' in text or '18:' in text else 'T14:00:00+08:00')
    return campus, arrival


def fixture_whut_conditions(text):
    student = 'graduate' if '研究生' in text else 'undergraduate' if '本科' in text else None
    years = re.findall(r'(20\d{2})\s*级', text)
    funding = 'origin_loan' if '生源地' in text else 'campus_loan' if '校园地' in text else None
    return student, int(years[-1]) if years else None, funding


def fixture_response(messages, tools, config):
    names = {t['function']['name'] for t in tools}
    users = [m for m in messages if m['role'] == 'user']
    last = users[-1]['content']
    last_index = max(i for i, m in enumerate(messages) if m['role'] == 'user')
    results = [m for m in messages[last_index + 1:] if m['role'] == 'tool']
    if 'submit_constraints' in names:
        payload = json.loads(last)
        request = payload['request']
        campus, arrival = infer_fixture(request['request'], config)
        request['campus_id'] = request.get('campus_id') or campus
        request['arrival'] = request.get('arrival') or arrival
        if config.data_profile == 'whut-2026':
            student, year, funding = fixture_whut_conditions(request['request'])
            for key, value in [('student_type', student), ('cohort_year', year), ('funding_route', funding)]:
                if value is not None:
                    request[key] = value
            if '绿色通道' in request['request'] and 'green_channel' not in request['service_ids']:
                request['service_ids'].append('green_channel')
        return tool_message('submit_constraints', request)
    if results:
        result = json.loads(results[-1]['content'])
        if not result.get('ok'):
            text = result.get('error', {}).get('message', '工具调用失败')
        elif isinstance(result.get('data'), dict) and 'evidence' in result['data']:
            evidence = result['data']['evidence']
            if '多少钱' in last or '金额' in last:
                text = '资料没有给出个人学费金额，应以缴费通知为准。'
                if evidence:
                    text += f" [{evidence[0]['evidence_id']}]"
            else:
                text = '\n'.join(f"{e['text']} [{e['evidence_id']}]" for e in evidence[:2]) or '没有找到该日期的有效资料。'
        else:
            text = json.dumps(result.get('data'), ensure_ascii=False)
        prefix = '【确定性测试模式；资料仅适用于武汉理工大学2026级本科】\n' if config.data_profile == 'whut-2026' else '【确定性测试模式】\n'
        return {'role': 'assistant', 'content': prefix + text}
    all_text = '\n'.join(m['content'] for m in users)
    campus, arrival = infer_fixture(all_text, config)
    if '连接状态' in last:
        return tool_message('get_integration_status', {})
    if '同步' in last and '日历' in last:
        return tool_message('request_calendar_sync', {})
    if '天气' in last:
        code = re.search(r'(?<!\d)\d{6}(?!\d)', last)
        if not code:
            return {'role': 'assistant', 'content': '【确定性测试模式】请提供6位城市行政区划代码，或在外部工具面板查询天气。'}
        day = re.search(r'\d{4}-\d{2}-\d{2}', last)
        return tool_message('get_weather_forecast', {'city_adcode': code.group(), 'target_date': day.group() if day else None})
    if '学校通知' in last:
        return tool_message('search_school_notices', {'query': last, 'campus_id': campus})
    if '课表' in last:
        return tool_message('get_timetable', {'start_date': config.demo_date, 'end_date': config.demo_date})
    if any(w in last for w in ['安排', '计划', '规划']) or ('待补充规划任务' in messages[0]['content']):
        args = {'request': all_text, 'campus_id': campus, 'arrival': arrival}
        if config.data_profile == 'whut-2026':
            student, year, funding = fixture_whut_conditions(all_text)
            args.update(student_type=student, cohort_year=year, funding_route=funding)
            if '个人提醒' in all_text:
                times = re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00', all_text)
                if len(times) >= 2:
                    args['reminders'] = [{'service_id': 'registration', 'start': times[-2], 'end': times[-1]}]
        return tool_message('delegate_plan', args)
    if '待办' in last:
        return tool_message('list_todos', {})
    if '日历' in last or '日程' in last:
        return tool_message('list_calendar_events', {})
    args = {'query': last, 'campus_id': campus, 'as_of_date': config.demo_date}
    if config.data_profile == 'whut-2026':
        student, year, _ = fixture_whut_conditions(all_text)
        args.update(school_id='whut', student_type=student or 'undergraduate', cohort_year=year or 2026)
    return tool_message('search_knowledge', args)
