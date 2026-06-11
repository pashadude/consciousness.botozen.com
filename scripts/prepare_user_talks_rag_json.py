"""Convert Mutual Specification user-talk markdown into Spanner RAG JSONL."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.prepare_spanner_rag_json import (
    chunk_document,
    infer_commodities,
    stable_id,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        default=["docs/design/mutual_specification_user_talk_seed.md"],
    )
    parser.add_argument(
        "--talk-log-dir",
        action="append",
        default=["app/.adk/user_talks"],
        help="Directory containing persisted console/user talk JSON records.",
    )
    parser.add_argument("--out-dir", default="data/spanner_rag_ingest")
    parser.add_argument("--chunk-chars", type=int, default=3200)
    parser.add_argument("--chunk-overlap", type=int, default=300)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_path = out_dir / "user_talks_documents.jsonl"
    chunks_path = out_dir / "user_talks_chunks.jsonl"
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    doc_count = chunk_count = 0
    with docs_path.open("w", encoding="utf-8") as docs_file, chunks_path.open(
        "w",
        encoding="utf-8",
    ) as chunks_file:
        for source_path in [Path(item) for item in args.source]:
            body = source_path.read_text(encoding="utf-8")
            title = title_from_markdown(body) or source_path.stem.replace("_", " ")
            document = {
                "doc_id": stable_id(["mutual_spec_user_talks", source_path.as_posix()]),
                "source_dataset": "mutual_spec_user_talks",
                "source_file": source_path.as_posix(),
                "source_row": 1,
                "external_id": source_path.stem,
                "title": title,
                "body": body,
                "text_for_embedding": "\n".join(
                    [
                        f"Title: {title}",
                        "Commodities: trader_workflow, human_ai_coordination",
                        "Tags: mutual_specification_game, user_talk, trader_agent",
                        body,
                    ]
                ),
                "published_at": created_at,
                "source": "user_talk_seed",
                "commodities": ["trader_workflow", "human_ai_coordination"],
                "tags": [
                    "mutual_specification_game",
                    "user_talk",
                    "trader_agent",
                    "specification_game",
                ],
                "sentiment": None,
                "metadata": {
                    "kind": "mutual_spec_user_talk_seed",
                    "source_path": source_path.as_posix(),
                },
            }
            docs_file.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
            docs_file.write("\n")
            doc_count += 1
            for chunk in chunk_document(
                document,
                chunk_chars=args.chunk_chars,
                chunk_overlap=args.chunk_overlap,
            ):
                chunks_file.write(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
                chunks_file.write("\n")
                chunk_count += 1
        for talk_log_dir in [Path(item) for item in args.talk_log_dir]:
            if not talk_log_dir.exists():
                continue
            for source_row, source_path in enumerate(
                sorted(talk_log_dir.glob("*.json")),
                start=1,
            ):
                document = talk_record_document(
                    source_path=source_path,
                    source_row=source_row,
                )
                docs_file.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
                docs_file.write("\n")
                doc_count += 1
                for chunk in chunk_document(
                    document,
                    chunk_chars=args.chunk_chars,
                    chunk_overlap=args.chunk_overlap,
                ):
                    chunks_file.write(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
                    chunks_file.write("\n")
                    chunk_count += 1
    print(json.dumps({"documents": doc_count, "chunks": chunk_count}, indent=2))
    print(f"Wrote {docs_path}")
    print(f"Wrote {chunks_path}")
    return 0


def title_from_markdown(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return ""


def talk_record_document(*, source_path: Path, source_row: int) -> dict[str, Any]:
    record = json.loads(source_path.read_text(encoding="utf-8"))
    ledger_id = text_value(record.get("ledger_id")) or source_path.stem
    expressed_query = text_value(record.get("expressed_query") or record.get("raw_text"))
    title = expressed_query[:180] if expressed_query else f"User talk {ledger_id}"
    body = render_talk_record(record)
    tags = sorted(
        {
            "conversation_trace",
            "mutual_specification_game",
            "proof_carrying_response",
            "specification_ledger",
            "trader_agent",
            "user_talk",
            text_value(record.get("decision_gate")),
            text_value(record.get("response_channel")),
            text_value(record.get("status")),
        }
        - {""}
    )
    commodities = infer_commodities(
        " ".join([title, body]),
        defaults=["trader_workflow", "human_ai_coordination"],
        row={},
    )
    published_at = text_value(record.get("created_at")) or None
    text_for_embedding = "\n".join(
        [
            f"Title: {title}",
            f"Published: {published_at}" if published_at else "",
            f"Ledger: {ledger_id}",
            f"Commodities: {', '.join(commodities)}",
            f"Tags: {', '.join(tags)}",
            body,
        ]
    ).strip()
    return {
        "doc_id": stable_id(
            [
                "mutual_spec_user_talks",
                ledger_id,
                source_path.as_posix(),
                expressed_query,
            ]
        ),
        "source_dataset": "mutual_spec_user_talks",
        "source_file": source_path.as_posix(),
        "source_row": source_row,
        "external_id": ledger_id,
        "title": title,
        "body": body,
        "text_for_embedding": text_for_embedding,
        "published_at": published_at,
        "source": "console_user_talk_log",
        "commodities": commodities,
        "tags": tags,
        "sentiment": None,
        "metadata": {
            "kind": "mutual_spec_user_talk",
            "ledger_id": ledger_id,
            "source_path": source_path.as_posix(),
            "response_channel": record.get("response_channel"),
            "decision_gate": record.get("decision_gate"),
            "status": record.get("status"),
            "raw_record": record,
        },
    }


def render_talk_record(record: Mapping[str, Any]) -> str:
    sections = [
        ("User query", record.get("expressed_query") or record.get("raw_text")),
        ("Speech text", record.get("speech_text")),
        ("Goal", record.get("goal")),
        ("Audience", record.get("audience")),
        ("Output format", record.get("output_format")),
        ("Decision gate", record.get("decision_gate")),
        ("Status", record.get("status")),
        ("Latent intent hypotheses", record.get("latent_intent_hypotheses")),
        ("Ambiguities", record.get("ambiguities")),
        ("Search plan", record.get("search_plan")),
        ("Evidence contract", record.get("evidence_contract")),
        ("Verification conditions", record.get("verification_conditions")),
        ("Assumptions", record.get("assumptions")),
        ("Artifact refs", record.get("artifact_refs")),
        ("Route history", record.get("route_history")),
        ("Source layer", record.get("source_layer")),
        ("Verification passed", record.get("verification_passed")),
        ("Draft response", record.get("draft")),
    ]
    parts = [
        f"Created at: {text_value(record.get('created_at'))}",
        f"Ledger ID: {text_value(record.get('ledger_id'))}",
        "Kind: mutual_spec_user_talk",
    ]
    for label, value in sections:
        rendered = render_value(value)
        if rendered:
            parts.append(f"\n## {label}\n{rendered}")
    return "\n".join(parts).strip()


def render_value(value: Any) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, list):
        return "\n".join(f"- {render_value(item)}" for item in value if render_value(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def text_value(value: Any) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


if __name__ == "__main__":
    raise SystemExit(main())
