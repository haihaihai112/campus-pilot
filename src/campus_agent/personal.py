"""Small, explicit conversation controls. Tool text never authorizes a write."""
import re


def decision(message):
    text = re.sub(r'[\s，,。.!！：:]', '', message)
    if text in {'保存', '确认保存', '确认执行', '保存为待办', '确认保存为待办',
                '就按刚才的清单确认保存为待办'}:
        return 'confirm'
    if text in {'先不保存', '不保存', '拒绝保存', '拒绝本轮操作', '先不保存我再看看'}:
        return 'reject'
    return None


def todo_query(message):
    text = re.sub(r'[\s，,。.!！?？]', '', message)
    return bool(re.fullmatch(r'(?:请|帮我)?(?:查看|查询|列出|列一下)(?:一下)?(?:我|我的)?(?:已保存的)?(?:待办|事项)(?:列表)?', text)
        or re.fullmatch(r'(?:我|我的)?(?:现在|当前|之前存的|已保存的)?(?:有|都有哪些|有哪些|的)?待办(?:有哪些|是什么)?(?:帮我列一下|请列出)?', text))


def todo_deletion(message):
    return '待办' in message and any(word in message for word in ('删除', '删掉', '移除'))


def checklist_request(message):
    return ('待办' not in message and '清单' in message and
            any(word in message for word in ('入学', '报到', '入住')) and
            any(word in message for word in ('帮我', '规划', '整理', '生成', '安排')))


def render_todos(result):
    if not result.get('ok'):
        return '待办查询失败，不能判断是否为空，请稍后重试。'
    rows = result['data']
    if not rows:
        return '刚刚查询：你当前没有已保存的待办。'
    known = {e['evidence_id'] for e in result.get('evidence', [])}
    lines = [f'刚刚查询：你有 {len(rows)} 条已保存待办：']
    for row in rows:
        status = '已完成' if row['status'] == 'completed' else '待完成'
        timing = f"；截止 {row['due_date']}" if row.get('due_date') else ''
        refs = ' '.join(f'[{eid}]' for eid in row.get('evidence_ids', []) if eid in known)
        # Titles are user/tool data: don't render arbitrary E IDs as verified citations.
        title = re.sub(r'\[(E-[^\]\s]+)\]', r'（\1，未作为来源核验）', row['title'])
        lines.append(f'- {title}（{status}{timing}） {refs}'.rstrip())
    lines.append('这是项目内保存的个人事项，资料引用仍为快照；不代表学校事务已办结。')
    return '\n'.join(lines)


def action_result(result, reject=False):
    if not result.get('ok'):
        return '未执行：' + result['error']['message']
    if reject:
        return '已拒绝本次操作，未执行写入。'
    data = result['data']
    if 'todos_created' in data:
        return f"已保存 {data['todos_created']} 项待办和 {data['events_created']} 项日程。"
    if 'synced_events' in data:
        return f"已同步 {data['synced_events']} 项日程。"
    return '待办状态已更新。'
