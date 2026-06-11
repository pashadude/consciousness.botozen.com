"""Opoint API MCP Server

This module exposes the functionality of the internal ``OpointAPI`` wrapper
as Model Context Protocol (MCP) tools via the **FastMCP** helper that ships
with the official `modelcontextprotocol/python-sdk` package.

Environment
-----------
The underlying ``OpointAPI`` requires an API key.  Make sure the environment
variable ``OPOINT_API_KEY`` is set before starting the server.  For example:

```bash
export OPOINT_API_KEY="YOUR_SECRET_KEY"
uvx opoint-mcp
```
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from .api import OpointAPI

# ---------------------------------------------------------------------------
# MCP server initialisation
# ---------------------------------------------------------------------------

mcp = FastMCP("Opoint API Server")

# ---------------------------------------------------------------------------
# Internal helper - lazily (re-)create the wrapper so each tool call is fresh
# and we avoid accidental state-sharing across concurrent invocations.
# ---------------------------------------------------------------------------

def _get_api() -> OpointAPI:
    """Instantiate ``OpointAPI`` using the key from ``$OPOINT_API_KEY``."""
    api_key = os.getenv("OPOINT_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Environment variable 'OPOINT_API_KEY' must be set for Opoint "
            "tools to work."
        )
    return OpointAPI(api_key=api_key)

# ---------------------------------------------------------------------------
# Tools - thin wrappers around the three public methods of ``OpointAPI``
# ---------------------------------------------------------------------------


@mcp.tool()
def search_site(site_name: str) -> dict[str, Any]:
    """Return the raw site search response from Opoint.

    Parameters
    ----------
    site_name:
        Partial or full name of the outlet, e.g. ``"Reuters"``.
    """
    api = _get_api()
    return api.search_site(site_name)


@mcp.tool()
def search_articles(
    site_id: str | None = None,
    search_text: str | None = None,
    language: str | None = None,
    num_articles: int = 20,
    min_score: float | None = None,
    source: str | None = None,
    topic_ids: list[str] | None = None,
    media_topic_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Search the Opoint index for matching articles.

    Most arguments map 1-to-1 onto the parameters accepted by
    :py:meth:`opoint_mcp.api.OpointAPI.search_articles`.
    Date parameters are accepted as ISO-8601 strings (``YYYY-MM-DD``).  The
    result is converted to a list of dictionaries so it can be serialised as
    JSON by the MCP transport.
    """

    api = _get_api()

    # Convert date strings (if provided) to ``datetime`` objects expected by
    # the wrapper.
    sd_dt = datetime.fromisoformat(start_date) if start_date else None
    ed_dt = datetime.fromisoformat(end_date) if end_date else None

    df = api.search_articles(
        site_id=site_id,
        search_text=search_text,
        language=language,
        num_articles=num_articles,
        min_score=min_score,
        source=source,
        topic_ids=topic_ids,
        media_topic_ids=media_topic_ids,
        start_date=sd_dt,
        end_date=ed_dt,
    )

    return df.to_dict(orient="records")


@mcp.tool()
def search_site_and_articles(
    site_name: str | None = None,
    search_text: str | None = None,
    language: str | None = None,
    num_articles: int = 20,
    min_score: float | None = None,
    source: str | None = None,
    topic_ids: list[str] | None = None,
    media_topic_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Convenience wrapper: first resolve *site_name* then fetch articles.

    Falls back to a global search (all outlets) if *site_name* is omitted or
    ``None``.
    """

    api = _get_api()

    sd_dt = datetime.fromisoformat(start_date) if start_date else None
    ed_dt = datetime.fromisoformat(end_date) if end_date else None

    df = api.search_site_and_articles(
        site_name=site_name,
        search_text=search_text,
        language=language,
        num_articles=num_articles,
        min_score=min_score,
        source=source,
        topic_ids=topic_ids,
        media_topic_ids=media_topic_ids,
        start_date=sd_dt,
        end_date=ed_dt,
    )

    return df.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Main entry point for console script
# ---------------------------------------------------------------------------

def main():
    """Main entry point for the MCP server."""
    # Transport defaults to stdio (works great with `mcp dev` / Claude).
    # Set host/port/path here to run as SSE/HTTP server instead.
    mcp.run()


# ---------------------------------------------------------------------------
# Entrypoint for direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
