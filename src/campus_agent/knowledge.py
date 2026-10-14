from __future__ import annotations

import hashlib
import os
from datetime import date
from types import SimpleNamespace

from .baseline.chunking import build_chunks
from .baseline.retrievers import MemoryHybridRetriever, MilvusBGERetriever
from .config import Settings
from .models import Evidence, SearchArgs, failure, success
from .seed import load_seed


class Knowledge:
    def __init__(self, config: Settings):
        self.config = config
        self.data = load_seed(config.runtime / 'data', config.data_profile)
        self.documents = {d['source']: d for d in self.data['documents']}
        self.chunks = build_chunks(config.runtime / 'data' / 'knowledge')
        self.indexes = {}

    def search(self, **kwargs):
        args = SearchArgs.model_validate(kwargs)
        date.fromisoformat(args.as_of_date)
        allowed = {name: d for name, d in self.documents.items()
                   if (args.campus_id is None or d['campus_id'] == args.campus_id
                       or (d['campus_id'] == 'whut' and args.campus_id in self.data['campuses']))
                   and (args.school_id is None or d.get('school_id', 'synthetic') == args.school_id)
                   and d.get('student_type', 'undergraduate') == args.student_type
                   and d.get('cohort_year', 2026) == args.cohort_year
                   and d['valid_from'] <= args.as_of_date <= d['valid_to']}
        if not allowed:
            return success({'evidence': [], 'notice': '该校区、学生类型、入学年份或日期没有有效资料；不得沿用其他届规定'})
        signature = tuple(sorted(allowed))
        if signature not in self.indexes:
            chunks = [c for c in self.chunks if c.parent.source in allowed]
            if self.config.backend == 'memory':
                self.indexes[signature] = MemoryHybridRetriever(chunks)
            elif self.config.backend == 'milvus':
                # A metadata-filtered partition collection is built BEFORE recall.
                content = '\n'.join(name + allowed[name]['text'] for name in signature)
                digest = hashlib.sha256(content.encode()).hexdigest()[:12]
                options = SimpleNamespace(bge_m3_path=os.getenv('BGE_M3_PATH'),
                                          bge_reranker_path=os.getenv('BGE_RERANKER_PATH'),
                                          milvus_uri=os.getenv('MILVUS_URI', 'http://127.0.0.1:19530'),
                                          milvus_database='default', milvus_collection=f'campus_{digest}')
                retriever = MilvusBGERetriever(options)
                retriever.add(chunks)
                self.indexes[signature] = retriever
            else:
                return failure('configuration', '未知检索后端')
        hits = self.indexes[signature].search(args.query, args.top_k)
        evidence = [Evidence(**{**allowed[h.chunk.source], 'text': h.chunk.text,
                                'score': round(h.score, 6), 'backend': self.config.backend}).model_dump()
                    for h in hits]
        return success({'evidence': evidence, 'notice': '分数是排序信号，不是答案正确概率'})
