import json
from pathlib import Path

from scripts.prepare_spanner_rag_json import convert_source
from scripts.prepare_user_talks_rag_json import talk_record_document


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


def test_user_talk_ingest_marks_human_review_records(tmp_path) -> None:
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "kind": "mutual_spec_human_review",
                "created_at": "2026-06-12T00:00:00Z",
                "ledger_id": "ledger-1",
                "expressed_query": "Sulfur FOB Umm Qasr offer",
                "operator": "desk_operator",
                "review_action": "request_changes",
                "operator_note": "Need seller KYC and inspection docs.",
                "review_message": "Operator requested changes or more evidence.",
                "decision_gate": "needs_more_info",
                "status": "clarifying",
                "human_review": {"status": "changes_requested"},
            }
        ),
        encoding="utf-8",
    )

    document = talk_record_document(source_path=review_path, source_row=1)

    assert document["source_dataset"] == "mutual_spec_user_talks"
    assert document["source"] == "console_human_review_log"
    assert "human_review" in document["tags"]
    assert "Need seller KYC" in document["body"]
    assert document["metadata"]["kind"] == "mutual_spec_human_review"
