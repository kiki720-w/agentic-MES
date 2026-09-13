"""Persistent local document chunks and deterministic retrieval."""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomous_mes.domain.errors import NotFound, ValidationError

DOCUMENT_ID = re.compile(r"^doc_[a-f0-9]{32}$")
WORD = re.compile(r"[A-Za-z0-9_.-]+|[\u3400-\u9fff]")
HEADING = re.compile(r"^#{1,4}\s+(.+)$")
SUMMARY_INTENT = re.compile(r"概述|总结|摘要|主要内容|这(?:个|份)(?:文件|附件)|有哪些")
MAX_CHUNK_CHARACTERS = 1_600
CHUNK_OVERLAP_CHARACTERS = 180


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    ordinal: int
    location: str
    text: str

    def as_dict(self, score: float | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "chunkId": self.chunk_id,
            "documentId": self.document_id,
            "ordinal": self.ordinal,
            "location": self.location,
            "text": self.text,
            "characterCount": len(self.text),
        }
        if score is not None:
            result["score"] = round(score, 4)
        return result


def _split_text(document_id: str, text: str) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    buffer = ""
    location = "正文"

    def emit(value: str, source: str) -> None:
        value = value.strip()
        while value:
            part = value[:MAX_CHUNK_CHARACTERS]
            if len(value) > MAX_CHUNK_CHARACTERS:
                boundary = max(part.rfind("\n"), part.rfind("。"), part.rfind("；"))
                if boundary >= MAX_CHUNK_CHARACTERS // 2:
                    part = value[: boundary + 1]
            ordinal = len(chunks)
            chunks.append(DocumentChunk(
                f"{document_id}_c{ordinal:05d}", document_id, ordinal, source, part.strip()
            ))
            if len(value) <= len(part):
                break
            value = value[max(0, len(part) - CHUNK_OVERLAP_CHARACTERS):].lstrip()

    for line in text.splitlines():
        heading = HEADING.match(line.strip())
        if heading:
            if buffer.strip():
                emit(buffer, location)
            location = heading.group(1).strip()[:240]
            buffer = f"{line}\n"
            continue
        candidate = f"{buffer}{line}\n"
        if len(candidate) > MAX_CHUNK_CHARACTERS and buffer.strip():
            emit(buffer, location)
            buffer = f"## {location}\n{line}\n" if location != "正文" else f"{line}\n"
        else:
            buffer = candidate
    if buffer.strip():
        emit(buffer, location)
    return chunks


def _terms(value: str) -> set[str]:
    normalized = value.casefold()
    terms = {token for token in WORD.findall(normalized) if token.strip()}
    compact_cjk = "".join(ch for ch in normalized if "\u3400" <= ch <= "\u9fff")
    terms.update(compact_cjk[index:index + 2] for index in range(max(0, len(compact_cjk) - 1)))
    return terms


class LocalDocumentStore:
    """Stores extracted text under content-addressed IDs on the local machine."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def ingest(self, parsed: dict[str, Any], source: bytes | None = None) -> dict[str, Any]:
        digest = str(parsed.get("sha256", ""))
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValidationError("parsed attachment has no valid sha256")
        document_id = f"doc_{digest[:32]}"
        text = str(parsed.get("text", ""))
        chunks = _split_text(document_id, text)
        record = {
            "documentId": document_id,
            "name": str(parsed.get("name", "attachment"))[:255],
            "sha256": digest,
            "kind": str(parsed.get("kind", "DOCUMENT"))[:40],
            "parser": str(parsed.get("parser", "LOCAL"))[:80],
            "status": str(parsed.get("status", "NO_TEXT")),
            "summary": str(parsed.get("summary", ""))[:20_000],
            "characterCount": len(text),
            "chunkCount": len(chunks),
            "truncated": bool(parsed.get("truncated")),
            "metadata": parsed.get("metadata", {}),
            "warnings": list(parsed.get("warnings", [])),
            "localOnly": True,
            "sourceAvailable": source is not None,
            "chunks": [chunk.as_dict() for chunk in chunks],
        }
        target = self._path(document_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
        if source is not None:
            self._source_path(document_id).write_bytes(source)
        return {key: value for key, value in record.items() if key != "chunks"}

    def get(self, document_id: str, *, include_chunks: bool = False) -> dict[str, Any]:
        target = self._path(document_id)
        if not target.exists():
            raise NotFound("document not found")
        try:
            record = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NotFound("document record is unreadable") from exc
        return record if include_chunks else {
            key: value for key, value in record.items() if key != "chunks"
        }

    def search(
        self, document_ids: list[str], query: str, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 20:
            raise ValidationError("document search limit must be between 1 and 20")
        query_terms = _terms(query)
        summary_request = bool(SUMMARY_INTENT.search(query))
        candidates: list[tuple[DocumentChunk, set[str], set[str]]] = []
        for document_id in dict.fromkeys(document_ids):
            record = self.get(document_id, include_chunks=True)
            name_terms = _terms(str(record.get("name", "")))
            for item in record.get("chunks", []):
                chunk = DocumentChunk(
                    str(item["chunkId"]), str(item["documentId"]), int(item["ordinal"]),
                    str(item.get("location", "正文")), str(item.get("text", "")),
                )
                chunk_terms = _terms(f"{chunk.location}\n{chunk.text}")
                candidates.append((chunk, chunk_terms, name_terms))
        term_frequency = {
            term: sum(term in chunk_terms for _, chunk_terms, _ in candidates)
            for term in query_terms
        }
        ranked: list[tuple[float, int, DocumentChunk]] = []
        for chunk, chunk_terms, name_terms in candidates:
            overlap = query_terms & chunk_terms
            weighted_overlap = sum(
                math.log((len(candidates) + 1) / (term_frequency[term] + 1)) + 1
                for term in overlap
            )
            exact_bonus = 6.0 if query.casefold() in chunk.text.casefold() else 0.0
            name_bonus = 1.0 if query_terms & name_terms else 0.0
            coverage = len(overlap) / max(1, len(query_terms))
            score = exact_bonus + name_bonus + weighted_overlap * 1.5 + coverage * 4
            if summary_request:
                score += max(0.0, 4.0 - chunk.ordinal * 0.2)
            ranked.append((score, -chunk.ordinal, chunk))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [chunk.as_dict(score) for score, _, chunk in ranked[:limit]]

    def image_data_url(self, document_id: str) -> str | None:
        record = self.get(document_id)
        if record.get("kind") != "IMAGE_OCR" or not record.get("sourceAvailable"):
            return None
        source = self._source_path(document_id)
        if not source.exists():
            return None
        name = str(record.get("name", "")).lower()
        mime = "image/png" if name.endswith(".png") else "image/jpeg"
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _path(self, document_id: str) -> Path:
        if not DOCUMENT_ID.fullmatch(document_id):
            raise ValidationError("document id is invalid")
        return self._root / f"{document_id}.json"

    def _source_path(self, document_id: str) -> Path:
        if not DOCUMENT_ID.fullmatch(document_id):
            raise ValidationError("document id is invalid")
        return self._root / f"{document_id}.source"
