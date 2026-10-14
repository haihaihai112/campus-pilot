from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .loaders import discover, read_document
from .schemas import ChildChunk, ParentChunk


def _sections(text: str, default_title: str) -> list[tuple[str, str]]:
    title = default_title
    buffer: list[str] = []
    values: list[tuple[str, str]] = []
    for line in text.splitlines():
        value = line.strip()
        if re.match(r"^#{1,6}\s+\S", value):
            if buffer:
                values.append((title, "\n".join(buffer)))
                buffer = []
            title = re.sub(r"^#{1,6}\s+", "", value)
        elif value:
            buffer.append(value)
    if buffer:
        values.append((title, "\n".join(buffer)))
    return values


def _split(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text.strip()]
    values: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("。", start, end), text.rfind("\n", start, end))
            if boundary > start + size // 2:
                end = boundary + 1
        values.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return [value for value in values if value]


def build_chunks(directory: Path, parent_size: int = 1200, child_size: int = 320, overlap: int = 60) -> list[ChildChunk]:
    children: list[ChildChunk] = []
    for path in discover(directory):
        category = path.parent.name
        for section_number, (title, body) in enumerate(_sections(read_document(path), path.stem)):
            for parent_number, parent_text in enumerate(_split(body, parent_size, overlap)):
                rendered = f"标题：{title}\n{parent_text}"
                parent_id = hashlib.sha1(
                    f"{path.name}:{section_number}:{parent_number}:{rendered}".encode("utf-8")
                ).hexdigest()[:16]
                parent = ParentChunk(parent_id, rendered, path.name, title, category)
                for child_number, child_text in enumerate(_split(parent_text, child_size, overlap)):
                    child_id = hashlib.sha1(f"{parent_id}:{child_number}:{child_text}".encode("utf-8")).hexdigest()[:16]
                    children.append(ChildChunk(child_id, child_text, parent))
    return children

