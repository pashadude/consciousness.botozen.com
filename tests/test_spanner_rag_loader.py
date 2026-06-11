from scripts.load_spanner_rag_json import (
    DOCUMENT_COLUMNS,
    iter_chunk_rows,
    iter_document_rows,
)


def test_spanner_loader_maps_documents_and_chunks(tmp_path) -> None:
    docs = tmp_path / "documents.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    docs.write_text(
        '{"doc_id":"d1","source_dataset":"test","source_file":"x","source_row":1,'
        '"external_id":"e1","title":"T","body":"B","text_for_embedding":"T\\nB",'
        '"published_at":"2026-01-01T00:00:00Z","source":"unit",'
        '"commodities":["sulfur"],"tags":["trade"],"sentiment":null,'
        '"metadata":{"kind":"unit"}}\n',
        encoding="utf-8",
    )
    chunks.write_text(
        '{"doc_id":"d1","chunk_id":"c1","source_dataset":"test","chunk_index":0,'
        '"char_start":0,"chunk_text":"T B","text_for_embedding":"T B",'
        '"token_estimate":2,"published_at":"2026-01-01T00:00:00Z",'
        '"source":"unit","commodities":["sulfur"],"tags":["trade"],'
        '"metadata":{"title":"T"}}\n',
        encoding="utf-8",
    )

    doc_row = next(iter_document_rows([docs.as_posix()]))
    chunk_row = next(iter_chunk_rows([chunks.as_posix()]))

    assert DOCUMENT_COLUMNS[0] == "DocId"
    assert doc_row[0] == "d1"
    assert doc_row[10] == ["sulfur"]
    assert doc_row[13] == '{"kind":"unit"}'
    assert chunk_row[1] == "c1"
    assert chunk_row[13] is None
