from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

CAMPUSES = {'mingde': '明德校区', 'zhiyuan': '致远校区'}
LOCATIONS = {'gate': '校门', 'finance': '财务服务窗口', 'gym': '体育馆报到点',
             'dormitory': '宿舍服务台', 'card_center': '校园卡服务中心'}
SERVICES = [
    {'id': 'payment', 'title': '缴费或绿色通道核验', 'location': 'finance', 'minutes': 30,
     'dependencies': [], 'materials': ['录取通知书', '身份证', '缴费记录或绿色通道申请材料'],
     'rule': '家庭经济困难的新生可申请绿色通道，先办理入学手续，再按要求提交资助材料。个人学费金额未在本资料中列明，应以缴费通知为准。'},
    {'id': 'registration', 'title': '新生报到', 'location': 'gym', 'minutes': 40,
     'dependencies': ['payment'], 'materials': ['录取通知书', '身份证', '缴费或绿色通道核验记录'],
     'rule': '先完成缴费或绿色通道核验，再到报到点办理入学登记。'},
    {'id': 'dorm', 'title': '宿舍入住', 'location': 'dormitory', 'minutes': 30,
     'dependencies': ['registration'], 'materials': ['身份证', '报到凭证'],
     'rule': '完成报到后，持报到凭证到宿舍服务台核验身份并领取钥匙。宿舍门禁为23:00，晚归需登记。'},
    {'id': 'card', 'title': '校园卡办理', 'location': 'card_center', 'minutes': 20,
     'dependencies': ['registration'], 'materials': ['身份证', '报到凭证'],
     'rule': '完成报到后可办理校园卡；遗失需及时挂失。'},
]


def build_seed():
    services, documents = [], []
    for campus, campus_name in CAMPUSES.items():
        for spec in SERVICES:
            evidence_id = f"E-{campus}-{spec['id']}"
            windows = {'weekday': [['09:00', '12:00'], ['13:00', '17:00']],
                       'saturday': [['09:00', '12:00'], ['13:00', '16:00']], 'sunday': []}
            if spec['id'] == 'dorm':
                windows = {k: [['09:00', '21:00']] for k in windows}
            record = {**spec, 'campus_id': campus, 'location_name': LOCATIONS[spec['location']], 'windows': windows,
                      'exceptions': {'2026-09-06': []} if spec['id'] != 'dorm' else {},
                      'deadline': '2026-12-31T17:00:00+08:00', 'evidence_id': evidence_id}
            services.append(record)
            hours = '每日09:00—21:00' if spec['id'] == 'dorm' else '工作日09:00—12:00、13:00—17:00；周六09:00—12:00、13:00—16:00；周日不开放'
            text = (f"{campus_name}{spec['title']}。办理地点：{LOCATIONS[spec['location']]}。{spec['rule']}\n"
                    f"所需材料：{'、'.join(spec['materials'])}。开放时间：{hours}。"
                    f"演示办理时长：{spec['minutes']}分钟。此数据为合成演示，不对应真实高校。")
            documents.append({'evidence_id': evidence_id, 'text': text,
                              'title': f"{campus_name} {spec['title']}", 'campus_id': campus,
                              'valid_from': '2026-01-01', 'valid_to': '2026-12-31', 'version': 'demo-2026.1',
                              'source': f"{evidence_id}.md"})
    locations = ['gate', 'finance', 'gym', 'dormitory', 'card_center']
    travel = {f'{a}:{b}': (0 if a == b else 10 + 5 * abs(locations.index(a) - locations.index(b)))
              for a in locations for b in locations}
    return {'campuses': CAMPUSES, 'services': services, 'documents': documents, 'travel': travel}


def seed(root: Path, profile='synthetic'):
    root.mkdir(parents=True, exist_ok=True)
    existing = root / 'campus.json'
    if existing.exists():
        old = json.loads(existing.read_text(encoding='utf-8'))
        if old.get('profile', 'synthetic') != profile:
            raise ValueError('数据配置不一致；请选择独立 CAMPUS_RUNTIME，禁止覆盖旧资料')
    data = build_seed() if profile == 'synthetic' else json.loads(
        (Path(__file__).parent / 'data' / 'whut-2026.json').read_text(encoding='utf-8'))
    data['profile'] = profile
    docs = root / 'knowledge'
    docs.mkdir(exist_ok=True)
    for doc in data['documents']:
        temporary = docs / (doc['source'] + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(f"# {doc['title']}\n\n{doc['text']}\n", encoding='utf-8')
        os.replace(temporary, docs / doc['source'])
    temporary = root / ('campus.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, root / 'campus.json')
    return data


def load_seed(root: Path, profile='synthetic'):
    path = root / 'campus.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else seed(root, profile)
    if data.get('profile', 'synthetic') != profile:
        raise ValueError('数据配置不一致；请选择独立 CAMPUS_RUNTIME')
    if profile == 'whut-2026':
        packaged = json.loads((Path(__file__).parent / 'data' / 'whut-2026.json').read_text(encoding='utf-8'))
        if data.get('dataset_revision') != packaged['dataset_revision']:
            raise ValueError('资料结构版本过旧；请执行campus seed更新来源，不会修改个人事项数据库')
    return data
