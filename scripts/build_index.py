"""Build the committed retrieval index.

Creates index/chunks.json (text chunks with provenance) and
index/embeddings.npy (deterministic local vectors from src.utils.text_hash so the
app works with zero external API calls), plus index/index_meta.json recording the
embedding method + dimension. Runtime retrieval re-derives the same vectors.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from datetime import datetime

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import DOCS_DIR, INDEX_DIR, log
from src.utils import text_hash

HEADING_RE = re.compile(r"^##\s+(.+)$")


def chunk_doc(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    chunks: list[dict] = []
    title = Path(path).name
    current_heading = None
    body: list[str] = []
    doc_idx = None

    def flush() -> None:
        nonlocal current_heading, body, chunks
        if current_heading and body:
            chunks.append({
                "chunk_id": title.replace(".md", "") + f"-{len(chunks) + 1:03d}",
                "title": current_heading,
                "section": current_heading,
                "source": f"documents/{title}",
                "doc_idx": doc_idx,
                "text": "\n".join(body).strip(),
            })
        current_heading = None
        body = []

    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            flush()
            current_heading = m.group(1).strip()
        elif current_heading:
            body.append(line.rstrip())
    flush()

    # Doc-level heading appears implicit; tag doc_idx
    for i, c in enumerate(chunks):
        c["doc_idx"] = i
    return chunks


def main() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    all_chunks: list[dict] = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        all_chunks.extend(chunk_doc(path))

    texts = [c["text"] for c in all_chunks]
    # text_hash is already L2-normalized (see src/utils.py) -> direct matrix
    mat = np.vstack([text_hash(t) for t in texts])

    meta = {
        "embedding_method": "text_hash",
        "model": "internal-text-hash-v1",
        "embedding_dim": int(mat.shape[1]),
        "chunks": len(all_chunks),
        "documents": [p.name for p in sorted(DOCS_DIR.glob("*.md"))],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    with (INDEX_DIR / "chunks.json").open("w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=1)
    np.save(INDEX_DIR / "embeddings.npy", mat)
    with (INDEX_DIR / "index_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    log.info("Built index: %d chunks, %d dims -> %s",
             len(all_chunks), mat.shape[1], INDEX_DIR)


if __name__ == "__main__":
    main()