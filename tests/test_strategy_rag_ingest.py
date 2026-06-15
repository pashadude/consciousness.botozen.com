import json

from scripts.load_spanner_rag_json import default_chunks, default_documents
from scripts.prepare_strategy_rag_json import (
    agent_markdown_document,
    markdown_strategy_sections,
    quantpedia_document,
    strategy_chunks,
)


def test_quantpedia_document_extracts_metrics_and_strategy_tags(tmp_path) -> None:
    html = """
    <html><head>
      <title>Trading WTI/BRENT Spread - QuantPedia</title>
      <meta property="og:url" content="https://quantpedia.com/strategies/trading-wti-brent-spread/" />
      <meta property="article:published_time" content="2019-08-22T19:55:32+00:00" />
    </head><body>
      <p>As both oils are very similar, their spread shows signs of predictability.</p>
      <p>A 20-day moving average of WTI/Brent spread is calculated each day.</p>
      <div class="first">Sharpe Ratio</div><div class="second">0.88</div>
      <div class="first">Period of Rebalancing</div><div class="second">Daily</div>
      <a class="keyword">spread trading</a><a class="keyword">pairs trading</a>
    </body></html>
    """
    path = tmp_path / "trading-wti-brent-spread" / "index.html"
    path.parent.mkdir()
    path.write_text(html, encoding="utf-8")

    document = quantpedia_document(
        path=path,
        source_row=1,
        raw_html=html,
        created_at="2026-06-16T00:00:00Z",
    )

    assert document["source_dataset"] == "quantpedia_commodity_strategies"
    assert document["title"] == "Trading WTI/BRENT Spread"
    assert document["metadata"]["metrics"]["Sharpe Ratio"] == "0.88"
    assert "arbitrage" in document["tags"]
    assert "pairs_trading" in document["tags"]
    assert "brent" in document["commodities"]
    assert "wti" in document["commodities"]
    assert "20-day moving average" in document["body"]


def test_agent_markdown_sections_preserve_formulas_and_code(tmp_path) -> None:
    markdown = """# Commodity Strategist Agent

## Strategy Database

### HO/RB Crack Spread Mean Reversion
Sharpe: 1.2 | Return: 12% | Rebalancing: Daily

**Rules:** compute spread = HO - RB and trade z-score reversion.

```python
spread = ho_price - rb_price
signal = -spread.rolling(20).mean()
```
"""
    source = tmp_path / "commodity-strategist.md"
    source.write_text(markdown, encoding="utf-8")

    sections = list(markdown_strategy_sections(markdown, path=source))
    document = agent_markdown_document(
        path=source,
        source_row=2,
        title=sections[1][0],
        section_body=sections[1][1],
        kind=sections[1][2],
        created_at="2026-06-16T00:00:00Z",
    )
    chunks = list(strategy_chunks(document, chunk_chars=200, chunk_overlap=20))

    assert document["metadata"]["has_code"] is True
    assert document["metadata"]["has_formula"] is True
    assert "crack_spread" in document["tags"]
    assert "rbob" in document["commodities"]
    assert "heating_oil" in document["commodities"]
    assert "spread = ho_price - rb_price" in document["body"]
    assert chunks
    assert chunks[0]["chunk_text"].startswith("Title: HO/RB Crack Spread Mean Reversion")


def test_loader_defaults_include_strategy_jsonl(monkeypatch) -> None:
    monkeypatch.delenv("SPANNER_RAG_GCS_BUCKET", raising=False)

    assert "data/spanner_rag_ingest/strategy_documents.jsonl" in default_documents()
    assert "data/spanner_rag_ingest/strategy_chunks.jsonl" in default_chunks()

    monkeypatch.setenv("SPANNER_RAG_GCS_BUCKET", "bucket")

    assert "gs://bucket/strategy_documents.jsonl" in default_documents()
    assert "gs://bucket/strategy_chunks.jsonl" in default_chunks()


def test_strategy_json_rows_are_loader_compatible(tmp_path) -> None:
    markdown = """# Brent Strategist Agent

### Trading WTI/BRENT Spread
**Rules:** A 20-day moving average of WTI/Brent spread is calculated each day.
"""
    source = tmp_path / "brent-strategist.md"
    source.write_text(markdown, encoding="utf-8")
    title, body, kind = list(markdown_strategy_sections(markdown, path=source))[1]
    document = agent_markdown_document(
        path=source,
        source_row=2,
        title=title,
        section_body=body,
        kind=kind,
        created_at="2026-06-16T00:00:00Z",
    )
    row = json.loads(json.dumps(document))

    assert row["doc_id"]
    assert row["source"] == "agent_strategy_playbook"
    assert row["metadata"]["kind"] == "agent_strategy_rule"
