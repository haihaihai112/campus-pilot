from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .config import Settings
from .schemas import ChildChunk, ParentChunk, SearchHit


class Retriever(Protocol):
    def search(self, query: str, top_k: int = 5) -> list[SearchHit]: ...


def tokenize(text: str) -> list[str]:
    latin = re.findall(r"[a-z0-9_+#.-]+", text.lower())
    chinese: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        chinese.extend(run[index : index + 2] for index in range(max(1, len(run) - 1)))
    return latin + chinese


class MemoryHybridRetriever:
    def __init__(self, chunks: list[ChildChunk], dense_weight: float = 0.55):
        if not chunks:
            raise ValueError("Knowledge chunks cannot be empty")
        self.chunks = chunks
        self.dense_weight = dense_weight
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=1)
        self.matrix = normalize(self.vectorizer.fit_transform(chunk.text for chunk in chunks))
        self.tokens = [tokenize(chunk.text) for chunk in chunks]
        self.doc_freq = Counter(token for values in self.tokens for token in set(values))
        self.avg_length = sum(map(len, self.tokens)) / len(self.tokens)

    def _bm25(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self.chunks), dtype=float)
        for index, values in enumerate(self.tokens):
            counts = Counter(values)
            for token in tokenize(query):
                frequency = counts[token]
                if not frequency:
                    continue
                df = self.doc_freq[token]
                idf = math.log(1 + (len(self.chunks) - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.5 * (0.25 + 0.75 * len(values) / self.avg_length)
                scores[index] += idf * frequency * 2.5 / denominator
        maximum = scores.max(initial=0)
        return scores / maximum if maximum else scores

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        vector = normalize(self.vectorizer.transform([query]))
        dense = (self.matrix @ vector.T).toarray().ravel()
        sparse = self._bm25(query)
        score = self.dense_weight * dense + (1 - self.dense_weight) * sparse
        best_parent: dict[str, SearchHit] = {}
        for index in np.argsort(score)[::-1]:
            if score[index] <= 0:
                break
            child = self.chunks[int(index)]
            hit = SearchHit(child.parent, float(score[index]), float(dense[index]), float(sparse[index]))
            previous = best_parent.get(child.parent.id)
            if previous is None or hit.score > previous.score:
                best_parent[child.parent.id] = hit
        return sorted(best_parent.values(), key=lambda item: item.score, reverse=True)[:top_k]


class MilvusBGERetriever:
    """BGE-M3 dense/sparse retrieval and BGE cross-encoder reranking on Milvus."""

    def __init__(self, settings: Settings):
        if not settings.bge_m3_path or not settings.bge_reranker_path:
            raise RuntimeError("BGE_M3_PATH and BGE_RERANKER_PATH are required for Milvus mode")
        from milvus_model.hybrid import BGEM3EmbeddingFunction
        from pymilvus import MilvusClient
        from sentence_transformers import CrossEncoder

        self.settings = settings
        self.client = MilvusClient(uri=settings.milvus_uri, db_name=settings.milvus_database)
        self.embedder = BGEM3EmbeddingFunction(model_name_or_path=settings.bge_m3_path, device="cpu", use_fp16=False)
        self.reranker = CrossEncoder(settings.bge_reranker_path, device="cpu")
        self.collection = settings.milvus_collection

    @staticmethod
    def _sparse_row(row) -> dict[int, float]:
        if hasattr(row, "col"):
            return {int(index): float(value) for index, value in zip(row.col, row.data)}
        return {int(index): float(value) for index, value in zip(row.indices, row.data)}

    def create_collection(self) -> None:
        from pymilvus import DataType

        if self.client.has_collection(self.collection):
            return
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=32)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=self.embedder.dim["dense"])
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("parent_id", DataType.VARCHAR, max_length=32)
        schema.add_field("parent_text", DataType.VARCHAR, max_length=65535)
        schema.add_field("source", DataType.VARCHAR, max_length=512)
        schema.add_field("title", DataType.VARCHAR, max_length=512)
        schema.add_field("category", DataType.VARCHAR, max_length=128)
        indexes = self.client.prepare_index_params()
        indexes.add_index("dense", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 200})
        indexes.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
        self.client.create_collection(self.collection, schema=schema, index_params=indexes)

    def add(self, chunks: list[ChildChunk]) -> None:
        self.create_collection()
        encoded = self.embedder([chunk.text for chunk in chunks])
        rows = []
        for index, chunk in enumerate(chunks):
            rows.append(
                {
                    "id": chunk.id,
                    "text": chunk.text,
                    "dense": encoded["dense"][index],
                    "sparse": self._sparse_row(encoded["sparse"][index]),
                    "parent_id": chunk.parent.id,
                    "parent_text": chunk.parent.text,
                    "source": chunk.parent.source,
                    "title": chunk.parent.title,
                    "category": chunk.parent.category,
                }
            )
        if rows:
            self.client.upsert(self.collection, rows)

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        from pymilvus import AnnSearchRequest, WeightedRanker

        encoded = self.embedder([query])
        requests = [
            AnnSearchRequest([encoded["dense"][0]], "dense", {"metric_type": "COSINE", "params": {"ef": 64}}, 20),
            AnnSearchRequest([self._sparse_row(encoded["sparse"][0])], "sparse", {"metric_type": "IP", "params": {}}, 20),
        ]
        results = self.client.hybrid_search(
            self.collection,
            requests,
            WeightedRanker(0.55, 0.45),
            limit=20,
            output_fields=["parent_id", "parent_text", "source", "title", "category"],
        )[0]
        parents: dict[str, ParentChunk] = {}
        for hit in results:
            entity = hit["entity"]
            parents[entity["parent_id"]] = ParentChunk(
                entity["parent_id"], entity["parent_text"], entity["source"], entity["title"], entity["category"]
            )
        values = list(parents.values())
        scores = self.reranker.predict([[query, parent.text] for parent in values]) if values else []
        ranked = sorted(zip(scores, values), key=lambda item: float(item[0]), reverse=True)
        return [SearchHit(parent, float(score)) for score, parent in ranked[:top_k]]

