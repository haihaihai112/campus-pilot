from __future__ import annotations

import asyncio
import time
import uuid

import gradio as gr

from .protocols import MCPGateway
from .access import owned, request_user
from .models import ChatRequest


def build_ui(engine):
    def user(request):
        return request_user(request, engine.config)

    def require(kind, key, request):
        return owned(engine, kind, key, user(request))

    def plan_markdown(plan):
        if not plan:
            return '提交入学安排需求后，这里会显示计划预览。'
        lines = [f"### 计划预览 · 第 {plan['version']} 版", '**尚未确认的计划不会写入待办或日历。**', '']
        for item in plan['items']:
            timing = f"{item['start'][5:16].replace('T', ' ')} → {item['end'][11:16]}" if item.get('start') and item.get('end') else '时间待核实 · 仅保存待办'
            lines.extend([f"- {item['title']} · {timing}",
                          f"  地点：{item.get('location_name') or item['location']}；材料：{'、'.join(item['materials'])}"])
            if item.get('due_date'):
                lines.append('  2026级公开截止节点：' + item['due_date'])
            if item.get('timing_note'):
                lines.append('  ' + item['timing_note'])
        lines.extend(f"- 未安排：{m['service_id']}，{m['reason']}" for m in plan['unarranged'])
        lines.append('\n' + plan['explanation'])
        lines.extend('- ' + a for a in plan['assumptions'])
        return '\n'.join(lines)

    async def submit(message, sid, history, request: gr.Request):
        if not message.strip():
            raise gr.Error('请输入问题或需要安排的事项')
        body = ChatRequest(message=message, session_id=sid or None)
        turn = engine.enqueue(body.message, body.session_id, user(request))
        history = list(history or []) + [{'role': 'user', 'content': message}]
        yield '', turn['session_id'], history + [{'role': 'assistant', 'content': '正在处理…'}], '', None, [], '', turn['id'], ''
        while True:
            current = engine.db.get('turns', turn['id'])
            trace = current.get('trace', [])
            if current['status'] not in ('queued', 'working'):
                break
            progress = trace[-1] if trace else {}
            labels = {'submitted': '规划请求已接收', 'working': '正在核实条件并安排日程',
                      'input-required': '需要补充信息', 'completed': '计划已生成'}
            status_text = labels.get(progress.get('state'), '正在查询所需信息')
            yield '', turn['session_id'], history + [{'role': 'assistant', 'content': status_text + '…'}], '', None, trace, '', turn['id'], current.get('a2a_task_id', '')
            await asyncio.sleep(0.5)
        plan = engine.db.get('plans', current['plan_id']) if current.get('plan_id') else None
        sources = '\n\n'.join(f"**[{e['evidence_id']}] {e['title']}**\n\n{e['text']}\n\n[查看来源](/v1/evidence/{e['evidence_id']})" for e in current.get('evidence', []))
        yield '', turn['session_id'], history + [{'role': 'assistant', 'content': current['reply']}], plan_markdown(plan), plan, current.get('trace', []), sources, turn['id'], current.get('a2a_task_id', '')

    async def save(turn_id, request: gr.Request, reject=False):
        if not turn_id:
            return '请先生成计划。'
        turn = require('turns', turn_id, request)
        if not turn or not turn.get('pending_actions'):
            return '本轮没有待确认操作。'
        results = []
        for action_id in turn['pending_actions']:
            require('actions', action_id, request)
            result = await (engine.reject(action_id) if reject else engine.confirm(action_id))
            if result.get('ok'):
                data = result['data']
                results.append('已拒绝，未执行。' if reject else
                               f"已保存 {data['todos_created']} 项待办和 {data['events_created']} 项日程。" if 'todos_created' in data else
                               f"已同步 {data['synced_events']} 项日程。{data.get('notice', '')}" if 'synced_events' in data else '待办状态已更新。')
            else:
                results.append('未执行：' + result['error']['message'])
        return '\n'.join(results)

    async def refresh(request: gr.Request):
        gateway = MCPGateway(engine.config)
        todos = await gateway.call('list_todos', {'user_id': user(request)})
        events = await gateway.call('list_calendar_events', {'user_id': user(request)})
        if not todos.get('ok') or not events.get('ok'):
            raise gr.Error('个人事项服务暂不可用，请稍后刷新。')
        rows = todos.get('data') or []
        choices = [(f"{r['title']} · {'已完成' if r['status'] == 'completed' else '待完成'}", r['id']) for r in rows]
        todo_rows = [[r['title'], '已完成' if r['status'] == 'completed' else '待完成',
                      '、'.join(r['materials'])] for r in rows]
        event_rows = [[r['title'], r['start'][:16].replace('T', ' '), r['end'][11:16],
                       r.get('location_name') or r['location']] for r in (events.get('data') or [])]
        return todo_rows, event_rows, gr.update(choices=choices, value=choices[0][1] if choices else None)

    async def complete(todo_id, sid, request: gr.Request):
        if not todo_id:
            return '请先刷新并选择待办。'
        data = await MCPGateway(engine.config).call('list_todos', {'user_id': user(request)})
        row = next((r for r in (data.get('data') or []) if r['id'] == todo_id), None)
        if not row:
            return '待办不存在，请刷新。'
        if not sid:
            return '请先开始一个会话。'
        require('sessions', sid, request)
        # Clicking this explicit button authorizes this exact status change.
        action = engine.propose_action(sid, 'update_todo_status', {'todo_id': todo_id,
                                       'status': 'completed', 'expected_version': row['version']})
        result = await engine.confirm(action['id'])
        return '已标记为完成，请刷新个人事项。' if result.get('ok') else '未更新：' + result['error']['message']

    def export():
        # Authenticate every download at the API instead of exposing shared
        # Gradio filesystem URLs to other signed-in users.
        return '[下载我的日历（.ics）](/v1/calendar.ics)'

    async def cancel(task_id, request: gr.Request):
        if not task_id:
            return '当前没有规划任务。'
        require('task_owners', task_id, request)
        try:
            result = await engine.planner.cancel(task_id)
            return '规划已取消。' if result['status']['state'] == 'canceled' else '取消请求已提交。'
        except Exception as exc:
            return f'取消失败：{type(exc).__name__}'

    def restore(sid, request: gr.Request):
        require('sessions', sid, request)
        turns = [t for t in engine.db.all('turns') if t['session_id'] == sid]
        history = []
        for turn in turns:
            history.extend([{'role': 'user', 'content': turn['message']},
                            {'role': 'assistant', 'content': turn.get('reply') or turn['status']}])
        if not turns:
            return history, plan_markdown(None), None, [], '', '', ''
        latest = turns[-1]
        plan = engine.db.get('plans', latest['plan_id']) if latest.get('plan_id') else None
        source_text = '\n\n'.join(f"**[{e['evidence_id']}] {e['title']}**\n\n{e['text']}"
                                   for e in latest.get('evidence', []))
        return history, plan_markdown(plan), plan, latest.get('trace', []), source_text, latest['id'], latest.get('a2a_task_id', '')

    async def connections():
        result = await MCPGateway(engine.config).call('get_integration_status', {})
        if not result.get('ok'):
            return result['error']['message']
        data = result['data']
        return ('**地图与天气：** ' + data['maps_weather']['message'] + '\n\n**个人日历：** ' +
                data['calendar']['message'] + '\n\n**学校通知与个人课表：** 仅预留接口，尚未连接学校系统。')

    async def places(keywords, city):
        result = await MCPGateway(engine.config).call('search_places', {'keywords': keywords, 'city': city})
        if not result.get('ok'):
            return [], result['error']['message']
        rows = result['data']['places']
        return [[r['name'], r['address'], r['coordinate'], r['city_adcode']] for r in rows], (
            '请确认具体地点，再将对应坐标填入起点或终点。' if rows else '没有找到匹配地点。')

    async def route(origin, destination, mode):
        result = await MCPGateway(engine.config).call('get_route', {'origin': origin, 'destination': destination, 'mode': mode})
        if not result.get('ok'):
            return result['error']['message']
        data = result['data']
        return (f"高德查询结果：约 {data['duration_minutes']} 分钟，{data['distance_meters'] / 1000:.1f} 公里。\n\n" +
                '\n'.join(s['instruction'] for s in data['steps']) + '\n\n' + data['notice'])

    async def weather(adcode, day):
        result = await MCPGateway(engine.config).call('get_weather_forecast', {'city_adcode': adcode, 'target_date': day or None})
        if not result.get('ok'):
            return result['error']['message']
        data = result['data']
        lines = [f"高德天气 · {data['city']} · 发布时间：{data['report_time']}"]
        lines.extend(f"{r['date']}：白天{r['dayweather']} {r['daytemp']}℃，夜间{r['nightweather']} {r['nighttemp']}℃。" for r in data['forecasts'])
        return '\n\n'.join(lines + [data['notice']])

    async def propose_sync(sid, minutes, request: gr.Request):
        require('sessions', sid, request)
        result = await engine.propose_calendar_sync(sid, int(minutes))
        if not result.get('ok'):
            return '', result['error']['message']
        data = result['data']
        return data['action_id'], f"目标平台：{data['provider']}；计划版本：{data['plan_version']}；提前 {data['reminder_minutes']} 分钟提醒。尚未同步，请确认。"

    async def confirm_sync(action, request: gr.Request):
        if not action:
            return '请先生成同步预览。未绑定平台时不能执行同步。'
        require('actions', action, request)
        result = await engine.confirm(action)
        return (f"已同步 {result['data']['synced_events']} 项日程。" + result['data'].get('notice', '')) if result.get('ok') else result['error']['message']

    css = '.gradio-container {max-width: 1360px !important;} .hero {padding: 18px 0;} footer {display:none !important;}'
    def my_sessions(request: gr.Request):
        sessions = [s for s in engine.db.all('sessions') if s.get('user_id', 'demo-user') == user(request)]
        return '\n'.join('- `' + s['id'] + '`' for s in sessions) or '暂无会话，发送第一条消息后自动创建。'

    def feedback(turn_id, rating, comment, request: gr.Request):
        if not turn_id:
            return '请先完成一次对话。'
        require('turns', turn_id, request)
        uid = user(request)
        key = uid + ':' + turn_id
        old = engine.db.get('feedback', key)
        engine.db.put('feedback', key, {'id': old['id'] if old else str(uuid.uuid4()), 'user_id': uid,
                      'turn_id': turn_id, 'rating': rating, 'comment': (comment or '')[:2000], 'updated_at': time.time()})
        return '反馈已保存，感谢参与体验。'

    with gr.Blocks(title='CampusPilot · 新生入学助手', theme=gr.themes.Soft(primary_hue='teal'), css=css) as ui:
        gr.Markdown('# CampusPilot\n新生入学事务助手 · 查询规定 · 安排入学 · 管理待办', elem_classes='hero')
        data_label = '武汉理工大学2026级本科公开资料 · 快照2026-10-11 · 独立项目，无校方合作' if engine.config.data_profile == 'whut-2026' else '合成校园测试数据'
        gr.Markdown(f"{data_label} · 参考日期：{engine.config.demo_date} · {'确定性测试替身（非模型实测）' if engine.config.mode == 'fixture' else '真实模型模式：' + engine.config.model}")
        if engine.config.data_profile == 'whut-2026':
            gr.Markdown('办理清单按公开资料整理；个人地点和截止政策以学校通知为准。精确时间只用于你明确指定的个人提醒。')
        if engine.config.auth_mode == 'pilot':
            gr.Markdown('邀请体验版：个人会话、待办与日程按账号隔离。校园资料仅用于演示，请勿提交敏感材料。')
            gr.HTML('<form method="post" action="/v1/auth/logout"><button type="submit">退出登录</button></form>')
        turn_id = gr.State('')
        plan_state = gr.State(None)
        with gr.Row():
            sid = gr.Textbox(label='会话编号（自动保存，可粘贴旧编号恢复）', scale=5)
            restore_button = gr.Button('恢复会话', scale=1)
        with gr.Accordion('我的会话', open=False):
            sessions_button = gr.Button('查看我的会话编号')
            sessions_text = gr.Markdown('会话可在退出登录或服务重启后恢复。')
        with gr.Row():
            with gr.Column(scale=6):
                chat = gr.Chatbot(type='messages', label='入学助手', height=520)
                message = gr.Textbox(label='你的问题或安排需求', placeholder='我是2026级本科生，帮我规划报到和入住的办理清单。', lines=2)
                send = gr.Button('发送', variant='primary')
                gr.Examples(['2026级本科生报到需要带哪些材料？', '我是2026级本科生，还没缴费，帮我规划报到和入住清单。',
                             '帮我安排入学流程', '查看我的待办'], inputs=message)
            with gr.Column(scale=5):
                with gr.Tab('计划与确认'):
                    preview = gr.Markdown(plan_markdown(None))
                    with gr.Row():
                        save_button = gr.Button('确认执行本轮操作', variant='primary')
                        reject_button = gr.Button('拒绝本轮操作')
                    action_status = gr.Textbox(label='执行结果', lines=4, interactive=False)
                    task_id = gr.Textbox(label='规划任务编号', interactive=False)
                    cancel_button = gr.Button('取消当前规划')
                with gr.Tab('引用来源'):
                    sources = gr.Markdown('回答引用的原文会显示在这里。')
                with gr.Tab('个人事项'):
                    refresh_button = gr.Button('刷新待办与日程')
                    todos = gr.Dataframe(headers=['事项', '状态', '材料'], datatype=['str', 'str', 'str'],
                                         label='已保存待办', interactive=False)
                    events = gr.Dataframe(headers=['事项', '开始', '结束', '地点'], datatype=['str'] * 4,
                                          label='已保存日程', interactive=False)
                    todo_choice = gr.Dropdown(label='选择待办', choices=[])
                    done_button = gr.Button('确认标记为完成')
                    export_button = gr.Button('导出日历文件')
                    calendar_file = gr.Markdown('导出本人的已保存日程。')
                with gr.Tab('运行详情'):
                    trace = gr.JSON(label='可观察的工具调用与A2A状态（不含内部思维链）')
                with gr.Tab('外部工具'):
                    connection_text = gr.Markdown('点击检查连接状态；地图和天气需要配置高德密钥，个人日历暂未绑定平台。')
                    connection_button = gr.Button('检查连接状态')
                    with gr.Accordion('地点与路线', open=False):
                        city = gr.Textbox(label='城市（必须明确）')
                        keywords = gr.Textbox(label='地点关键词')
                        place_button = gr.Button('查询候选地点')
                        place_rows = gr.Dataframe(headers=['地点', '地址', '坐标', '行政区代码'], interactive=False)
                        place_status = gr.Textbox(label='地点查询结果', interactive=False)
                        origin = gr.Textbox(label='已确认起点坐标（经度,纬度）')
                        destination = gr.Textbox(label='已确认终点坐标（经度,纬度）')
                        mode = gr.Dropdown(choices=[('步行', 'walking'), ('驾车', 'driving')], value='walking', label='出行方式')
                        route_button = gr.Button('查询路线')
                        route_result = gr.Textbox(label='路线结果', lines=5, interactive=False)
                        gr.Markdown('个人报到地点需核实。未配置真实坐标时，不提供默认校门或报到点。')
                    with gr.Accordion('天气预报', open=False):
                        adcode = gr.Textbox(label='6位城市行政区代码（可从地点查询获得）')
                        forecast_day = gr.Textbox(label='目标日期（YYYY-MM-DD；留空显示当前可用预报）')
                        weather_button = gr.Button('查询天气')
                        weather_result = gr.Textbox(label='天气结果', lines=5, interactive=False)
                    with gr.Accordion('个人日历适配', open=False):
                        gr.Markdown('已建立平台适配接口，暂不绑定账户。本地日程与ICS导出可正常使用。')
                        reminder = gr.Number(value=30, precision=0, minimum=0, maximum=10080, label='提前提醒分钟数')
                        sync_propose = gr.Button('预览当前会话计划的日历同步')
                        sync_action = gr.State('')
                        sync_confirm = gr.Button('确认同步到外部日历')
                        sync_status = gr.Textbox(label='同步结果', interactive=False)
        with gr.Accordion('体验反馈', open=False):
            rating = gr.Radio(choices=[('有帮助', 'helpful'), ('需要改进', 'unhelpful')], value='helpful', label='本轮回答')
            comment = gr.Textbox(label='问题或建议（请勿填写敏感信息）', max_lines=4)
            feedback_button = gr.Button('提交本轮反馈')
            feedback_status = gr.Textbox(label='反馈结果', interactive=False)
        outputs = [message, sid, chat, preview, plan_state, trace, sources, turn_id, task_id]
        send.click(submit, [message, sid, chat], outputs)
        message.submit(submit, [message, sid, chat], outputs)
        restore_button.click(restore, sid, [chat, preview, plan_state, trace, sources, turn_id, task_id])
        save_button.click(save, turn_id, action_status)
        async def reject_current(value, request: gr.Request):
            return await save(value, request, True)
        reject_button.click(reject_current, turn_id, action_status)
        refresh_button.click(refresh, outputs=[todos, events, todo_choice])
        done_button.click(complete, [todo_choice, sid], action_status)
        export_button.click(export, outputs=calendar_file)
        cancel_button.click(cancel, task_id, action_status, queue=False)
        connection_button.click(connections, outputs=connection_text)
        place_button.click(places, [keywords, city], [place_rows, place_status])
        route_button.click(route, [origin, destination, mode], route_result)
        weather_button.click(weather, [adcode, forecast_day], weather_result)
        sync_propose.click(propose_sync, [sid, reminder], [sync_action, sync_status])
        sync_confirm.click(confirm_sync, sync_action, sync_status)
        sessions_button.click(my_sessions, outputs=sessions_text)
        feedback_button.click(feedback, [turn_id, rating, comment], feedback_status)
    return ui.queue(max_size=engine.config.max_pending_turns, default_concurrency_limit=engine.config.max_active_turns)
