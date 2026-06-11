import json
from pathlib import Path

from scripts.prepare_spanner_rag_json import convert_source


def test_convert_source_writes_documents_and_chunks(tmp_path) -> None:
    source_csv = tmp_path / "oil.csv"
    source_csv.write_text(
        "\n".join(
            [
                "datetime_utc,source,text,sentiment,confidence,source_category",
                "2026-01-01T00:00:00Z,TestWire,Brent crude and sulfur logistics note,1,0.9,macro",
            ]
        )
    )
    docs_path = tmp_path / "documents.jsonl"
    chunks_path = tmp_path / "chunks.jsonl"

    with docs_path.open("w", encoding="utf-8") as docs_file, chunks_path.open(
        "w",
        encoding="utf-8",
    ) as chunks_file:
        stats = convert_source(
            source={
                "dataset": "oil_sentiment",
                "path": source_csv.as_posix(),
                "format": "csv",
                "default_commodities": ["oil"],
            },
            docs_file=docs_file,
            chunks_file=chunks_file,
            chunk_chars=200,
            chunk_overlap=20,
            limit=None,
        )

    document = json.loads(Path(docs_path).read_text().splitlines()[0])
    chunk = json.loads(Path(chunks_path).read_text().splitlines()[0])

    assert stats.documents == 1
    assert stats.chunks == 1
    assert document["doc_id"]
    assert document["published_at"] == "2026-01-01T00:00:00Z"
    assert "crude_oil" in document["commodities"]
    assert "sulfur" in document["commodities"]
    assert document["sentiment"]["label"] == "1"
    assert chunk["doc_id"] == document["doc_id"]
    assert chunk["token_estimate"] > 0
