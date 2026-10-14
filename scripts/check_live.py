import asyncio
import json
from campus_agent.config import Settings
from campus_agent.llm import Model, function_tool


async def main():
    config = Settings(mode='live')
    try:
        model = Model(config)
        reply, usage = await model.respond([
            {'role': 'system', 'content': '请调用ping工具，不输出其他内容。'},
            {'role': 'user', 'content': '检查工具调用'}],
            [function_tool('ping', '测试原生工具调用', {'type': 'object', 'properties': {}})])
        result = {'ok': bool(reply.get('tool_calls')), 'model': config.model, 'usage': usage,
                  'tool_names': [c['function']['name'] for c in reply.get('tool_calls', [])]}
    except Exception as exc:
        result = {'ok': False, 'model': config.model, 'error_type': type(exc).__name__}
        cause = exc.__cause__
        if cause:
            result['cause_type'] = type(cause).__name__
    print(json.dumps(result))
    (config.runtime / 'live-preflight.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


asyncio.run(main())
