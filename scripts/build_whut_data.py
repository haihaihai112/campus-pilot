"""Rebuild curated data from reviewed, hash-checked public source snapshots.

No external API calls. Image-only tuition tables and travel routes are excluded.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'docs' / 'school-selection'


def build():
    manifest = json.loads((SOURCES / 'manifest.json').read_text(encoding='utf-8'))
    entries = {Path(x['file']).name: x for x in manifest['snapshots']}
    documents = []
    selections = [
        ('registration', 'whut-registration.json', '## 新生报到注意事项', None),
        ('fees', 'whut-fees.json', '## 新生收费项目及标准', None),
        ('green', 'whut-green-channel.json', '## 新生入学“绿色通道”介绍', None),
        ('timeline', 'whut-undergraduate-full.json', '## 2026迎新事项', '入学教育!'),
    ]
    for topic, filename, begin, end in selections:
        meta = entries[filename]
        path = SOURCES / meta['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']:
            raise ValueError(f'Snapshot changed: {filename}')
        obj = json.loads(path.read_text(encoding='utf-8'))
        body = obj['markdown'].split(begin, 1)[1]
        if end:
            body = body.split(end, 1)[0]
        # Keep verbatim textual content; remove image markup, never infer image facts.
        import re
        body = re.sub(r'!\[[^\]]*\]\([^\n]*\)', '[图片内容未转录，不能据此回答表格或路线信息]', body)
        notes = ['仅适用于2026级本科生；来源快照不代表政策持续有效，个人通知优先。']
        if topic == 'registration':
            notes.append('网页description仍含2025字样，正文明确2026；本资料以正文为准并保留差异。')
        if topic in {'green', 'fees'}:
            notes.append('年份依据本科入口及页面2026标识；不得推及2027年或研究生。')
        if topic == 'fees':
            notes.append('专业学费表为图片未转录；医保按最新文件，个人总费用未知。')
        documents.append(dict(evidence_id=f'E-whut-2026-{topic}', text=begin + '\n\n' + body.strip(),
            title=meta['title'], source=f'whut-2026-{topic}.md', campus_id='whut',
            school_id='whut', student_type='undergraduate', cohort_year=2026,
            valid_from='2026-01-01', valid_to='2026-12-31', version='whut-2026-snapshot-20261011',
            source_url=meta['source_url'], snapshot_sha256=meta['sha256'],
            fetched_at='2026-10-11', source_notes=notes))

    def service(sid, title, evidence, materials=(), dependencies=(), due_date=None, guidance='', additional_topics=()):
        return dict(id=sid, title=title, campus_id='whut', location='unconfirmed',
            location_name='以个人迎新系统、录取通知书或学院通知为准', minutes=None, windows=None,
            dependencies=list(dependencies), materials=list(materials), due_date=due_date,
            evidence_id=f'E-whut-2026-{evidence}', guidance=guidance,
            evidence_ids=[f'E-whut-2026-{topic}' for topic in (evidence, *additional_topics)],
            dependency_kind='assistant_suggested_order', appointment_available=False)

    services = [
        service('materials', '准备报到备查材料', 'registration', ['录取通知书', '高考准考证', '身份证']),
        service('volunteer', '联系学院学生志愿者，确认个人报到地点', 'registration',
                guidance='在学校迎新系统查看志愿者信息；本助手未连接校内账号。'),
        service('online_registration', '完成学校网上自助报到', 'timeline', due_date='2026-09-11',
                guidance='需在学校平台办理，本助手只能记录待办。'),
        service('education', '完成新生入学教育及安全微课', 'timeline', due_date='2026-09-11'),
        service('payment', '核对个人缴费通知和资助路径', 'fees',
                guidance='不能计算个人学费总额；缴费不是所有新生报到的统一前置条件。', additional_topics=['green']),
        service('green_channel', '准备绿色通道材料并向学校核实', 'green',
                guidance='申请资格及审批由学校决定；贷款合同和回执或校园贷预办理材料按个人路径准备。'),
        service('registration', '按录取通知书到校报到', 'registration',
                dependencies=['materials', 'volunteer'],
                materials=['录取通知书', '高考准考证', '身份证'],
                guidance='官网总时间线列9月12日；个人录取通知书日期优先，迟到须联系招生办请假。', additional_topics=['timeline']),
        service('dorm', '联系志愿者协助宿舍入住', 'registration', dependencies=['volunteer'],
                guidance='宿舍分配、钥匙领取及办理时间未知，不生成校方预约。'),
        service('card', '联系志愿者协助领取校园卡', 'registration', dependencies=['volunteer']),
        service('archives', '自带档案者报到后交给所在学院', 'registration', dependencies=['registration']),
        service('league', '团员按学院要求办理团组织关系转接', 'registration',
                dependencies=['registration'], due_date='2026-09-30',
                materials=['团员证', '团员档案（含入团志愿书）', '转出组织开具时携带介绍信']),
    ]
    result = dict(profile='whut-2026', dataset_revision='whut-2026-20261011-r2', school_id='whut', school_name='武汉理工大学',
        student_type='undergraduate', cohort_year=2026, fetched_at='2026-10-11',
        synthetic=False, campuses={'whut': '武汉理工大学（个人报到校区待核实）',
        'mafangshan': '马房山校区', 'yujiatou': '余家头校区', 'nanhu': '南湖校区'},
        documents=documents, services=services, travel={},
        exclusions=['研究生政策', '图片中的专业学费表', '图片中的交通路线',
                    '校内账号和个人宿舍分配', '未公开的窗口时间、办理时长与坐标'])
    target = ROOT / 'src' / 'campus_agent' / 'data' / 'whut-2026.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Built {len(documents)} reviewed source documents, {len(services)} checklist templates')


if __name__ == '__main__':
    build()
