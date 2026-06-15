"""Convert strategy/playbook sources into Spanner RAG JSONL ingest records."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.prepare_spanner_rag_json import (
    chunk_document,
    infer_commodities,
    stable_id,
    unique,
)

DEFAULT_QUANTPEDIA_DIR = (
    "/Users/pauldudko/VSProjects/commodity_signal/ShinkaEvolve/shinka/examples/"
    "oil_improm/quantpedia_commodities_strategies/quantpedia.com/strategies"
)
DEFAULT_AGENT_MARKDOWN = [
    "/Users/pauldudko/Downloads/agents/commodity-strategist.md",
    "/Users/pauldudko/Downloads/agents/commodity-researcher.md",
    "/Users/pauldudko/Downloads/agents/brent-strategist.md",
]

NOISE_LINE_PATTERNS = (
    "google tag manager",
    "monsterinsights",
    "subscribe for newsletter",
    "subscription form",
    "share on",
    "refer to a friend",
    "back to list of strategies",
    "browse next strategies",
    "already analyzed",
    "logout",
    "account",
    "privacy overview",
    "cookie",
    "quantpedia is the encyclopedia",
)
NOISE_LINE_EXACT = {
    "about",
    "accept",
    "affiliate",
    "awards",
    "blog",
    "charts",
    "clients & references",
    "consulting",
    "contact",
    "enable all",
    "faq",
    "follow us",
    "how it works",
    "links & tools",
    "pricing",
    "privacy policy",
    "product",
    "quantpedia awards",
    "resources",
    "save settings",
    "screener",
    "support",
    "terms of service",
}
HTML_TERMINAL_LINES = (
    "related picture",
    "related video",
    "other papers",
    "be first to know",
    "select your profession",
    "i agree that quantpedia",
    "the encyclopedia of quantitative trading strategies",
    "risk disclosure:",
    "testimonial disclosure:",
    "© 2024 quantpedia.com",
)

STRATEGY_TAG_PATTERNS = {
    "arbitrage": r"\b(arb|arbitrage|spread)\b",
    "basis": r"\b(basis|basis momentum)\b",
    "calendar_spread": r"\b(calendar spread|term structure|curve|backwardation|contango)\b",
    "carry": r"\b(carry|roll yield|convenience yield)\b",
    "cot_positioning": r"\b(cot|commitments of traders|hedging pressure|speculative pressure)\b",
    "crack_spread": r"\b(crack spread|rbob|heating oil|gasoline|distillate)\b",
    "energy": r"\b(oil|brent|wti|crude|gasoline|heating oil|natural gas|rbob|ulsd)\b",
    "machine_learning": r"\b(machine learning|neural|lasso|ridge|xgboost|random forest|lstm|pca)\b",
    "mean_reversion": r"\b(mean reverting|mean reversion|reverted|cointegration|cointegrated)\b",
    "momentum": r"\b(momentum|trend following|trend-following|macd|sma)\b",
    "options": r"\b(option|straddle|put|call|delta hedge|variance swap)\b",
    "pairs_trading": r"\b(pairs trading|pair trading|long-short|long short)\b",
    "seasonality": r"\b(seasonal|seasonality|pre-holiday|holiday|turn of the month)\b",
    "sentiment": r"\b(sentiment|news|media emotion)\b",
    "volatility": r"\b(volatility|variance|semivariance|drawdown|risk premium)\b",
}

COMMODITY_ALIASES = {
    "ho/rb": ("heating_oil", "rbob"),
    "ho rb": ("heating_oil", "rbob"),
    "heating oil": ("heating_oil",),
    "rbob": ("rbob",),
    "rbo b": ("rbob",),
    "wti": ("wti",),
    "distillate": ("distillates",),
    "gasoline": ("gasoline",),
    "corn": ("corn",),
    "wheat": ("wheat",),
    "soybean": ("soybeans",),
    "cotton": ("cotton",),
    "coffee": ("coffee",),
    "cocoa": ("cocoa",),
    "silver": ("silver",),
    "palladium": ("palladium",),
    "platinum": ("platinum",),
}


@dataclass(frozen=True)
class ConvertStats:
    dataset: str
    source_path: str
    documents: int = 0
    chunks: int = 0
    skipped: int = 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quantpedia-dir",
        default=DEFAULT_QUANTPEDIA_DIR,
        help="Directory containing quantpedia.com/strategies/*/index.html.",
    )
    parser.add_argument(
        "--agent-markdown",
        action="append",
        default=None,
        help="Agent/playbook markdown file. Can be repeated.",
    )
    parser.add_argument("--out-dir", default="data/spanner_rag_ingest")
    parser.add_argument("--chunk-chars", type=int, default=3200)
    parser.add_argument("--chunk-overlap", type=int, default=300)
    parser.add_argument("--limit-quantpedia", type=int, default=None)
    parser.add_argument("--limit-agent-sections", type=int, default=None)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_path = out_dir / "strategy_documents.jsonl"
    chunks_path = out_dir / "strategy_chunks.jsonl"
    manifest_path = out_dir / "strategy_manifest.json"
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    stats: list[ConvertStats] = []
    with docs_path.open("w", encoding="utf-8") as docs_file, chunks_path.open(
        "w",
        encoding="utf-8",
    ) as chunks_file:
        stats.append(
            write_documents(
                documents=iter_quantpedia_documents(
                    Path(args.quantpedia_dir),
                    created_at=created_at,
                    limit=args.limit_quantpedia,
                ),
                docs_file=docs_file,
                chunks_file=chunks_file,
                dataset="quantpedia_commodity_strategies",
                source_path=args.quantpedia_dir,
                chunk_chars=args.chunk_chars,
                chunk_overlap=args.chunk_overlap,
            )
        )
        markdown_paths = [
            Path(item) for item in (args.agent_markdown or DEFAULT_AGENT_MARKDOWN)
        ]
        stats.append(
            write_documents(
                documents=iter_agent_markdown_documents(
                    markdown_paths,
                    created_at=created_at,
                    limit=args.limit_agent_sections,
                ),
                docs_file=docs_file,
                chunks_file=chunks_file,
                dataset="commodity_strategy_playbooks",
                source_path=", ".join(path.as_posix() for path in markdown_paths),
                chunk_chars=args.chunk_chars,
                chunk_overlap=args.chunk_overlap,
            )
        )

    manifest = {
        "created_at": created_at,
        "schema_version": 1,
        "output": {
            "documents": docs_path.as_posix(),
            "chunks": chunks_path.as_posix(),
        },
        "sources": {
            "quantpedia_dir": args.quantpedia_dir,
            "agent_markdown": [path.as_posix() for path in markdown_paths],
        },
        "stats": [stat.__dict__ for stat in stats],
        "totals": {
            "documents": sum(stat.documents for stat in stats),
            "chunks": sum(stat.chunks for stat in stats),
            "skipped": sum(stat.skipped for stat in stats),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["totals"], indent=2))
    print(f"Wrote {docs_path}")
    print(f"Wrote {chunks_path}")
    print(f"Wrote {manifest_path}")
    return 0


def write_documents(
    *,
    documents: Iterable[dict[str, Any]],
    docs_file: Any,
    chunks_file: Any,
    dataset: str,
    source_path: str,
    chunk_chars: int,
    chunk_overlap: int,
) -> ConvertStats:
    docs = chunks = skipped = 0
    for document in documents:
        if not str(document.get("text_for_embedding") or "").strip():
            skipped += 1
            continue
        docs_file.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
        docs_file.write("\n")
        docs += 1
        for chunk in strategy_chunks(
            document,
            chunk_chars=chunk_chars,
            chunk_overlap=chunk_overlap,
        ):
            chunks_file.write(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
            chunks_file.write("\n")
            chunks += 1
    return ConvertStats(
        dataset=dataset,
        source_path=source_path,
        documents=docs,
        chunks=chunks,
        skipped=skipped,
    )


def iter_quantpedia_documents(
    root: Path,
    *,
    created_at: str,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    files = sorted(root.glob("*/index.html"))
    for source_row, path in enumerate(files, start=1):
        if limit is not None and source_row > limit:
            break
        raw_html = path.read_text(encoding="utf-8", errors="replace")
        yield quantpedia_document(
            path=path,
            source_row=source_row,
            raw_html=raw_html,
            created_at=created_at,
        )


def quantpedia_document(
    *,
    path: Path,
    source_row: int,
    raw_html: str,
    created_at: str,
) -> dict[str, Any]:
    slug = path.parent.name
    title = extract_html_title(raw_html) or slug.replace("-", " ").title()
    source_url = extract_meta(raw_html, "og:url") or (
        f"https://quantpedia.com/strategies/{slug}/"
    )
    published_at = (
        extract_meta(raw_html, "article:published_time")
        or extract_meta(raw_html, "article:modified_time")
        or created_at
    )
    metrics = extract_quantpedia_metrics(raw_html)
    keywords = extract_quantpedia_keywords(raw_html)
    text = extract_strategy_html_text(raw_html, title=title)
    body = build_quantpedia_body(
        title=title,
        source_url=source_url,
        metrics=metrics,
        keywords=keywords,
        text=text,
    )
    tags = infer_strategy_tags(
        title=title,
        body=body,
        base=["trading_strategy", "quantpedia", "strategy_rules"],
        keywords=keywords,
    )
    commodities = infer_strategy_commodities(title, body)
    metadata = {
        "kind": "quantpedia_strategy",
        "source_url": source_url,
        "strategy_slug": slug,
        "metrics": metrics,
        "keywords": keywords,
        "has_code": has_code(body),
        "has_formula": has_formula(body),
    }
    doc_id = stable_id(["quantpedia_commodity_strategies", slug, source_url])
    return {
        "doc_id": doc_id,
        "source_dataset": "quantpedia_commodity_strategies",
        "source_file": path.as_posix(),
        "source_row": source_row,
        "external_id": slug,
        "title": title,
        "body": body,
        "text_for_embedding": build_strategy_embedding_text(
            title=title,
            source="quantpedia_strategy_mirror",
            published_at=published_at,
            commodities=commodities,
            tags=tags,
            body=body,
        ),
        "published_at": published_at,
        "source": "quantpedia_strategy_mirror",
        "commodities": commodities,
        "tags": tags,
        "sentiment": None,
        "metadata": metadata,
    }


def iter_agent_markdown_documents(
    paths: Sequence[Path],
    *,
    created_at: str,
    limit: int | None,
) -> Iterator[dict[str, Any]]:
    emitted = 0
    for path in paths:
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for source_row, (title, section_body, kind) in enumerate(
            markdown_strategy_sections(body, path=path),
            start=1,
        ):
            if limit is not None and emitted >= limit:
                return
            emitted += 1
            yield agent_markdown_document(
                path=path,
                source_row=source_row,
                title=title,
                section_body=section_body,
                kind=kind,
                created_at=created_at,
            )


def markdown_strategy_sections(
    body: str,
    *,
    path: Path,
) -> Iterator[tuple[str, str, str]]:
    doc_title = title_from_markdown(body) or path.stem.replace("-", " ").replace("_", " ")
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", body, flags=re.MULTILINE))
    if not matches:
        yield doc_title, body.strip(), "agent_strategy_playbook"
        return

    overview = body[: matches[0].start()].strip()
    if overview:
        yield f"{doc_title} overview", overview, "agent_strategy_playbook_overview"
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        title = match.group(1).strip()
        section = body[match.start() : end].strip()
        kind = (
            "trader_research_context"
            if "researcher" in path.stem.lower()
            else "agent_strategy_rule"
        )
        yield title, section, kind


def agent_markdown_document(
    *,
    path: Path,
    source_row: int,
    title: str,
    section_body: str,
    kind: str,
    created_at: str,
) -> dict[str, Any]:
    base_tags = ["trading_strategy", "agent_playbook", kind]
    if "researcher" in path.stem.lower():
        base_tags.append("trader_research")
    tags = infer_strategy_tags(
        title=title,
        body=section_body,
        base=base_tags,
        keywords=[],
    )
    commodities = infer_strategy_commodities(title, section_body)
    metadata = {
        "kind": kind,
        "agent_file": path.name,
        "has_code": has_code(section_body),
        "has_formula": has_formula(section_body),
    }
    doc_id = stable_id(
        ["commodity_strategy_playbooks", path.as_posix(), str(source_row), title]
    )
    return {
        "doc_id": doc_id,
        "source_dataset": "commodity_strategy_playbooks",
        "source_file": path.as_posix(),
        "source_row": source_row,
        "external_id": f"{path.stem}:{source_row}",
        "title": title,
        "body": section_body,
        "text_for_embedding": build_strategy_embedding_text(
            title=title,
            source="agent_strategy_playbook",
            published_at=created_at,
            commodities=commodities,
            tags=tags,
            body=section_body,
        ),
        "published_at": created_at,
        "source": "agent_strategy_playbook",
        "commodities": commodities,
        "tags": tags,
        "sentiment": None,
        "metadata": metadata,
    }


def extract_html_title(raw_html: str) -> str:
    og_title = extract_meta(raw_html, "og:title")
    if og_title:
        return clean_title(og_title)
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    return clean_title(strip_tags(match.group(1))) if match else ""


def clean_title(value: str) -> str:
    return re.sub(r"\s+-\s+QuantPedia\s*$", "", html_lib.unescape(value)).strip()


def extract_meta(raw_html: str, property_name: str) -> str:
    pattern = (
        r'<meta[^>]+(?:property|name)=["\']'
        + re.escape(property_name)
        + r'["\'][^>]+content=["\']([^"\']+)["\']'
    )
    match = re.search(pattern, raw_html, flags=re.IGNORECASE)
    if match:
        return html_lib.unescape(match.group(1)).strip()
    pattern = (
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']'
        + re.escape(property_name)
        + r'["\']'
    )
    match = re.search(pattern, raw_html, flags=re.IGNORECASE)
    return html_lib.unescape(match.group(1)).strip() if match else ""


def extract_quantpedia_metrics(raw_html: str) -> dict[str, str]:
    pairs = re.findall(
        r'<div[^>]*class=["\'][^"\']*\bfirst\b[^"\']*["\'][^>]*>(.*?)</div>\s*'
        r'<div[^>]*class=["\'][^"\']*\bsecond\b[^"\']*["\'][^>]*>(.*?)</div>',
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    metrics: dict[str, str] = {}
    for label, value in pairs:
        clean_label = normalize_space(strip_tags(label))
        clean_value = normalize_space(strip_tags(value))
        if clean_label and clean_value:
            metrics[clean_label] = clean_value
    return metrics


def extract_quantpedia_keywords(raw_html: str) -> list[str]:
    keywords = re.findall(
        r'<a[^>]*class=["\'][^"\']*\bkeyword\b[^"\']*["\'][^>]*>(.*?)</a>',
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return unique(normalize_space(strip_tags(item)) for item in keywords)


def extract_strategy_html_text(raw_html: str, *, title: str) -> str:
    text = html_to_text(raw_html)
    raw_lines = [line for line in text.splitlines() if keep_strategy_line(line, title=title)]
    lines = slice_strategy_content(raw_lines, title=title)
    compact: list[str] = []
    previous = ""
    for line in lines:
        if line == previous:
            continue
        previous = line
        compact.append(line)
    return "\n".join(compact).strip()


def slice_strategy_content(lines: list[str], *, title: str) -> list[str]:
    if not lines:
        return []
    title_key = clean_title(title).lower()
    title_indexes = [
        index
        for index, line in enumerate(lines)
        if clean_title(line).lower() == title_key
    ]
    start = title_indexes[1] if len(title_indexes) > 1 else title_indexes[0] if title_indexes else 0
    end = len(lines)
    for index, line in enumerate(lines[start + 1 :], start=start + 1):
        lower = line.lower()
        if any(lower.startswith(marker) for marker in HTML_TERMINAL_LINES):
            end = index
            break
    return lines[start:end]


def html_to_text(raw_html: str) -> str:
    cleaned = re.sub(
        r"(?is)<(script|style|svg|noscript).*?</\1>",
        " ",
        raw_html,
    )
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(
        r"(?i)</(p|div|h[1-6]|li|tr|td|th|section|article|header)>",
        "\n",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)<(p|div|h[1-6]|li|tr|td|th|section|article|header)[^>]*>",
        "\n",
        cleaned,
    )
    text = strip_tags(cleaned)
    lines = [normalize_space(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def strip_tags(value: str) -> str:
    return html_lib.unescape(re.sub(r"(?is)<[^>]+>", " ", value))


def keep_strategy_line(line: str, *, title: str) -> bool:
    normalized = normalize_space(line)
    lower = normalized.lower()
    if len(normalized) < 3:
        return False
    if lower in NOISE_LINE_EXACT:
        return False
    if any(pattern in lower for pattern in NOISE_LINE_PATTERNS):
        return False
    if lower.startswith(("http://", "https://")):
        return False
    if title and normalized == title:
        return True
    return True


def build_quantpedia_body(
    *,
    title: str,
    source_url: str,
    metrics: Mapping[str, str],
    keywords: Sequence[str],
    text: str,
) -> str:
    parts = [
        f"Strategy: {title}",
        f"Source URL: {source_url}",
        "Source type: Quantpedia commodity strategy mirror",
    ]
    if metrics:
        parts.append("\n## Strategy Metrics")
        parts.extend(f"- {key}: {value}" for key, value in sorted(metrics.items()))
    if keywords:
        parts.append("\n## Keywords")
        parts.append(", ".join(keywords))
    if text:
        parts.append("\n## Extracted Strategy Text")
        parts.append(text)
    return "\n".join(parts).strip()


def infer_strategy_tags(
    *,
    title: str,
    body: str,
    base: Sequence[str],
    keywords: Sequence[str],
) -> list[str]:
    tags = list(base) + [slugify_tag(keyword) for keyword in keywords]
    lower = f"{title}\n{body}".lower()
    for tag, pattern in STRATEGY_TAG_PATTERNS.items():
        if re.search(pattern, lower):
            tags.append(tag)
    if has_code(body):
        tags.append("code")
    if has_formula(body):
        tags.append("formula")
    return sorted(unique(tag for tag in tags if tag))


def infer_strategy_commodities(title: str, body: str) -> list[str]:
    text = f"{title}\n{body}"
    values = infer_commodities(text, defaults=[], row={})
    lower = text.lower()
    for needle, commodities in COMMODITY_ALIASES.items():
        if needle in lower:
            values.extend(commodities)
    return sorted(unique(values or ["commodities"]))


def build_strategy_embedding_text(
    *,
    title: str,
    source: str,
    published_at: str,
    commodities: Sequence[str],
    tags: Sequence[str],
    body: str,
) -> str:
    return "\n".join(
        part
        for part in [
            f"Title: {title}",
            f"Published: {published_at}" if published_at else "",
            f"Source: {source}",
            f"Commodities: {', '.join(commodities)}" if commodities else "",
            f"Tags: {', '.join(tags[:60])}" if tags else "",
            body,
        ]
        if part
    ).strip()


def strategy_chunks(
    document: Mapping[str, Any],
    *,
    chunk_chars: int,
    chunk_overlap: int,
) -> Iterable[dict[str, Any]]:
    prefix = "\n".join(
        part
        for part in [
            f"Title: {document.get('title')}",
            f"SourceDataset: {document.get('source_dataset')}",
            f"Commodities: {', '.join(document.get('commodities') or [])}",
            f"Tags: {', '.join((document.get('tags') or [])[:20])}",
        ]
        if part and not str(part).endswith("None")
    )
    for chunk in chunk_document(
        document,
        chunk_chars=chunk_chars,
        chunk_overlap=chunk_overlap,
    ):
        text = str(chunk["chunk_text"])
        if not text.startswith("Title:"):
            text = f"{prefix}\n{text}"
        chunk["chunk_text"] = text
        chunk["text_for_embedding"] = text
        chunk["token_estimate"] = max(1, len(text) // 4)
        chunk["metadata"] = {
            **dict(chunk.get("metadata") or {}),
            "source_dataset": document.get("source_dataset"),
            "source": document.get("source"),
            "strategy_metadata": document.get("metadata"),
        }
        yield chunk


def title_from_markdown(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return ""


def has_code(text: str) -> bool:
    return bool(
        re.search(r"```|^\s*(import|from)\s+\w+|^\s*def\s+\w+\(", text, re.MULTILINE)
    )


def has_formula(text: str) -> bool:
    return bool(
        re.search(
            r"(equation|formula|sharpe|cagr|max drawdown|\bSMA\b|\bMACD\b|"
            r"\bRSI\b|=|λ|sigma|volatility|rank\(|sqrt\()",
            text,
            flags=re.IGNORECASE,
        )
    )


def slugify_tag(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def normalize_space(value: str) -> str:
    text = decode_unicode_escapes(html_lib.unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def decode_unicode_escapes(value: str) -> str:
    return re.sub(
        r"\\?u([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        value,
    )


if __name__ == "__main__":
    raise SystemExit(main())
