from __future__ import annotations

import asyncio
import time
import uuid

import gradio as gr

from .protocols import MCPGateway
from .access import owned, request_user
from .models import ChatRequest
from .personal import action_result
from .ui_theme import BRAND, CSS, FEATURES, WELCOME


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

    def action_preview(turn, plan):
        if plan:
            return plan_markdown(plan)
        actions = [engine.db.get('actions', aid) for aid in turn.get('pending_actions', [])]
        lines = []
        for action in actions:
            if not action:
                continue
            if action['tool'] == 'create_personal_todo':
                args = action['arguments']
                lines += ['### 个人待办', args['title'], '截止日期：' + (args.get('due_date') or '未设置'),
                          '个人提醒：' + (args['start'] + ' → ' + args['end'] if args.get('start') else '未设置'),
                          '用户指定的个人事项，不是校方预约或截止政策。']
            lines.append('操作状态：' + action['status'])
        return '\n\n'.join(lines) or plan_markdown(None)

    async def submit(message, sid, history, request: gr.Request):
        if not message.strip():
            raise gr.Error('请输入问题或需要安排的事项')
        body = ChatRequest(message=message, session_id=sid or None)
        turn = engine.enqueue(body.message, body.session_id, user(request))
        history = list(history or []) + [{'role': 'user', 'content': message}]
        yield '', turn['session_id'], history + [{'role': 'assistant', 'content': '正在处理…'}], '', None, [], '', turn['id'], '', gr.update(visible=False), ''
        while True:
            current = engine.db.get('turns', turn['id'])
            trace = current.get('trace', [])
            if current['status'] not in ('queued', 'working'):
                break
            progress = trace[-1] if trace else {}
            labels = {'submitted': '规划请求已接收', 'working': '正在核实条件并安排日程',
                      'input-required': '需要补充信息', 'completed': '计划已生成'}
            status_text = labels.get(progress.get('state'), '正在查询所需信息')
            yield '', turn['session_id'], history + [{'role': 'assistant', 'content': status_text + '…'}], '', None, trace, '', turn['id'], current.get('a2a_task_id', ''), gr.update(visible=False), ''
            await asyncio.sleep(0.5)
        plan = engine.db.get('plans', current['plan_id']) if current.get('plan_id') else None
        sources = '\n\n'.join(f"**[{e['evidence_id']}] {e['title']}**\n\n{e['text']}\n\n[查看来源](/v1/evidence/{e['evidence_id']})" for e in current.get('evidence', []))
        yield '', turn['session_id'], history + [{'role': 'assistant', 'content': current['reply']}], action_preview(current, plan), plan, current.get('trace', []), sources, turn['id'], current.get('a2a_task_id', ''), confirmation_visibility(current), ''

    async def save(turn_id, request: gr.Request, reject=False):
        if not turn_id:
            return '请先生成计划。', gr.update(visible=False)
        turn = require('turns', turn_id, request)
        if not turn or not turn.get('pending_actions'):
            return '本轮没有待确认操作。', gr.update(visible=False)
        results = []
        for action_id in turn['pending_actions']:
            require('actions', action_id, request)
            result = await (engine.reject(action_id) if reject else engine.confirm(action_id))
            results.append(action_result(result, reject))
        return '\n'.join(results), confirmation_visibility(turn)

    async def refresh(request: gr.Request):
        gateway = MCPGateway(engine.config)
        todos = await gateway.call('list_todos', {'user_id': user(request)})
        events = await gateway.call('list_calendar_events', {'user_id': user(request)})
        if not todos.get('ok') or not events.get('ok'):
            raise gr.Error('个人事项服务暂不可用，请稍后刷新。')
        rows = todos.get('data') or []
        choices = [(f"{r['title']} · {'已完成' if r['status'] == 'completed' else '待完成'}", r['id']) for r in rows]
        todo_rows = [[r['title'], '已完成' if r['status'] == 'completed' else '待完成', r.get('due_date') or '未设置',
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
            return history, plan_markdown(None), None, [], '', '', '', gr.update(visible=False), ''
        latest = turns[-1]
        plan = engine.db.get('plans', latest['plan_id']) if latest.get('plan_id') else None
        source_text = '\n\n'.join(f"**[{e['evidence_id']}] {e['title']}**\n\n{e['text']}"
                                   for e in latest.get('evidence', []))
        return history, action_preview(latest, plan), plan, latest.get('trace', []), source_text, latest['id'], latest.get('a2a_task_id', ''), confirmation_visibility(latest), ''

    async def connections():
        result = await MCPGateway(engine.config).call('get_integration_status', {})
        if not result.get('ok'):
            return result['error']['message']
        data = result['data']
        return ('**地图与天气：** ' + data['maps_weather']['message'] + '\n\n**个人日历：** ' +
                data['calendar']['message'] + '\n\n**学校通知、课程与宿舍：** 只读接口已预留，尚未连接学校授权数据源。')

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

    def confirmation_visibility(turn):
        actions = [engine.db.get('actions', aid) for aid in turn.get('pending_actions', [])]
        return gr.update(visible=turn.get('workflow_outcome') != 'guard_rejected' and
                         any(a and a['status'] in ('pending', 'retryable') for a in actions))

    def new_conversation():
        # Reset this browser view only. Saved sessions and personal items remain.
        return '', '', [], '', None, [], '', '', '', gr.update(visible=False), '', ''

    theme = gr.themes.Base(primary_hue='slate', neutral_hue='slate',
                           font=['Segoe UI', 'Microsoft YaHei UI', 'sans-serif'])
    with gr.Blocks(title='CampusPilot · 新生入学助手', theme=theme, css=CSS) as ui:
        plan_state = gr.State(None)
        with gr.Row(elem_id='cp-header'):
            gr.HTML(BRAND, elem_classes='cp-header-brand')
            new_button = gr.Button('新对话', size='sm', elem_id='cp-new')
            if engine.config.auth_mode == 'pilot':
                gr.HTML('<form method="post" action="/v1/auth/logout"><button type="submit">退出</button></form>', elem_id='cp-logout')
        with gr.Row(elem_id='cp-layout'):
            with gr.Column(scale=8, min_width=0, elem_id='cp-main'):
                chat = gr.Chatbot(type='messages', label='对话', show_label=False, container=False,
                                  height=430, placeholder=WELCOME, show_copy_button=True,
                                  show_share_button=False, elem_id='cp-chat')
                with gr.Row(elem_id='cp-composer'):
                    message = gr.Textbox(label='你的问题', show_label=False, container=False,
                        placeholder='说说你想了解或要办的事…', lines=1, max_lines=5, scale=9, elem_id='cp-message')
                    send = gr.Button('发送', variant='primary', scale=0, elem_id='cp-send')
                gr.Markdown('Enter 发送，Shift + Enter 换行。保存前会请你确认。', elem_id='cp-hint')
                with gr.Group(elem_id='cp-examples'):
                    gr.Examples(['报到需要哪些材料？', '我是2026级本科生，帮我整理报到清单。', '查看我的待办'],
                                inputs=message, label='试着问问', examples_per_page=3)
                with gr.Group(visible=False, elem_id='cp-confirm') as confirmation:
                    gr.Markdown('内容已生成，核对后再保存。')
                    with gr.Accordion('查看待保存内容', open=False):
                        preview = gr.Markdown('')
                    with gr.Row():
                        save_button = gr.Button('确认保存', variant='primary')
                        reject_button = gr.Button('先不保存')
                action_status = gr.Markdown('')
                with gr.Accordion('我的待办与日程', open=False, elem_classes='cp-fold'):
                    refresh_button = gr.Button('查看最新事项', size='sm')
                    todos = gr.Dataframe(headers=['事项', '状态', '截止日期', '材料'], datatype=['str'] * 4,
                                         label='我的待办', interactive=False)
                    with gr.Row():
                        todo_choice = gr.Dropdown(label='选择待办', choices=[], scale=3)
                        done_button = gr.Button('标记为完成', scale=1)
                    todo_status = gr.Textbox(label='事项操作结果', interactive=False)
                    with gr.Accordion('日程与导出', open=False):
                        events = gr.Dataframe(headers=['事项', '开始', '结束', '地点'], datatype=['str'] * 4,
                                              label='个人日程', interactive=False)
                        export_button = gr.Button('导出日历文件', size='sm')
                        calendar_file = gr.Markdown('仅导出已保存日程，不主动推送提醒。')
                with gr.Accordion('历史对话', open=False, elem_classes='cp-fold'):
                    gr.Markdown('新对话不会删除历史。输入会话编号可继续之前的对话。')
                    with gr.Row():
                        sid = gr.Textbox(label='会话编号', placeholder='发送消息后自动生成', scale=4)
                        restore_button = gr.Button('恢复对话', scale=1)
                    sessions_button = gr.Button('查看我的历史编号', size='sm')
                    sessions_text = gr.Markdown('历史对话仅本人可见。')
                with gr.Accordion('回答来源', open=False, elem_classes='cp-fold'):
                    sources = gr.Markdown('有引用的回答会在这里显示资料原文。')
                with gr.Accordion('反馈与运行详情', open=False, elem_classes='cp-fold'):
                    rating = gr.Radio(choices=[('有帮助', 'helpful'), ('需要改进', 'unhelpful')], value='helpful', label='这次回答')
                    comment = gr.Textbox(label='建议', placeholder='哪里需要改进？请勿填写敏感信息。', lines=2)
                    feedback_button = gr.Button('提交反馈', size='sm')
                    feedback_status = gr.Textbox(label='反馈结果', interactive=False)
                    with gr.Accordion('调试信息', open=False):
                        turn_id = gr.Textbox(label='本轮请求编号', interactive=False)
                        task_id = gr.Textbox(label='规划任务编号', interactive=False)
                        cancel_button = gr.Button('取消当前规划', size='sm')
                        cancel_status = gr.Textbox(label='取消结果', interactive=False)
                        trace = gr.JSON(label='工具调用记录')
            with gr.Column(scale=3, min_width=0, elem_id='cp-sidebar'):
                gr.HTML(FEATURES)
                with gr.Accordion('更多工具', open=False, elem_classes='cp-fold'):
                    gr.Markdown('地图与天气按连接状态提供；未接入学校个人平台。')
                    connection_button = gr.Button('查看可用状态', size='sm')
                    connection_text = gr.Markdown('尚未检查连接。')
                    with gr.Accordion('地点与路线', open=False):
                        city = gr.Textbox(label='城市')
                        keywords = gr.Textbox(label='地点关键词')
                        place_button = gr.Button('查询地点')
                        place_rows = gr.Dataframe(headers=['地点', '地址', '坐标', '行政区代码'], interactive=False)
                        place_status = gr.Textbox(label='地点查询结果', interactive=False)
                        origin = gr.Textbox(label='已确认起点坐标（经度,纬度）')
                        destination = gr.Textbox(label='已确认终点坐标（经度,纬度）')
                        mode = gr.Dropdown(choices=[('步行', 'walking'), ('驾车', 'driving')], value='walking', label='方式')
                        route_button = gr.Button('查询路线')
                        route_result = gr.Textbox(label='路线结果', lines=5, interactive=False)
                    with gr.Accordion('天气', open=False):
                        adcode = gr.Textbox(label='城市行政区代码')
                        forecast_day = gr.Textbox(label='日期（YYYY-MM-DD，留空显示可用预报）')
                        weather_button = gr.Button('查询天气')
                        weather_result = gr.Textbox(label='天气结果', lines=5, interactive=False)
                    with gr.Accordion('外部日历', open=False):
                        gr.Markdown('暂未绑定外部日历，可先使用个人日程和日历文件导出。')
                        reminder = gr.Number(value=30, precision=0, minimum=0, maximum=10080, label='提前提醒分钟数')
                        sync_propose = gr.Button('预览同步')
                        sync_action = gr.State('')
                        sync_confirm = gr.Button('确认同步')
                        sync_status = gr.Textbox(label='同步结果', interactive=False)
                scope = ('武汉理工大学 · 2026级本科<br>资料快照：2026-10-11<br>非校方系统，以学校最新通知为准。'
                         if engine.config.data_profile == 'whut-2026' else '合成校园测试数据，不作为真实学校规定。')
                mode_label = '流程测试模式，非真实模型' if engine.config.mode == 'fixture' else '真实模型模式'
                gr.Markdown(scope + f'<br>{mode_label} · 参考日期 {engine.config.demo_date}<br>请勿提交敏感材料。', elem_id='cp-scope')
        outputs = [message, sid, chat, preview, plan_state, trace, sources, turn_id, task_id, confirmation, action_status]
        send_event = send.click(submit, [message, sid, chat], outputs)
        enter_event = message.submit(submit, [message, sid, chat], outputs)
        new_button.click(new_conversation, outputs=outputs + [sync_action], cancels=[send_event, enter_event], queue=False)
        chat.clear(new_conversation, outputs=outputs + [sync_action], cancels=[send_event, enter_event], queue=False,
                   api_name='clear_conversation')
        restore_button.click(restore, sid, [chat, preview, plan_state, trace, sources, turn_id, task_id, confirmation, action_status])
        save_button.click(save, turn_id, [action_status, confirmation])
        async def reject_current(value, request: gr.Request):
            return await save(value, request, True)
        reject_button.click(reject_current, turn_id, [action_status, confirmation])
        refresh_button.click(refresh, outputs=[todos, events, todo_choice])
        done_button.click(complete, [todo_choice, sid], todo_status)
        export_button.click(export, outputs=calendar_file)
        cancel_button.click(cancel, task_id, cancel_status, queue=False)
        connection_button.click(connections, outputs=connection_text)
        place_button.click(places, [keywords, city], [place_rows, place_status])
        route_button.click(route, [origin, destination, mode], route_result)
        weather_button.click(weather, [adcode, forecast_day], weather_result)
        sync_propose.click(propose_sync, [sid, reminder], [sync_action, sync_status])
        sync_confirm.click(confirm_sync, sync_action, sync_status)
        sessions_button.click(my_sessions, outputs=sessions_text)
        feedback_button.click(feedback, [turn_id, rating, comment], feedback_status)
    return ui.queue(max_size=engine.config.max_pending_turns, default_concurrency_limit=engine.config.max_active_turns)
