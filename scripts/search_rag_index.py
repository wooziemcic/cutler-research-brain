#!/usr/bin/env python3
# =============================================================================
# Cutler Research Brain - Phase 5A
# search_rag_index.py
#
# RETRIEVAL TEST ONLY. Embeds your query with the same embedding model used to
# build the index, searches the local Chroma collection, and prints the chunks
# that came back.
#
# IT DOES NOT ANSWER THE QUESTION.
#   - no chat, completions or responses call is made
#   - no answer, summary or paraphrase is generated
#   - no thesis, sentiment, rating, recommendation or investment view
#   - the only API call is ONE embeddings call, for the query text
#
# What comes back is raw text that was already extracted from documents you
# already have. Reading it and deciding what it means is a human's job.
#
# The API key is read from .env and is never printed or logged.
#
# Usage:
#   python scripts\search_rag_index.py ^
#       --project-root "C:\Users\AI Admin\Documents\CutlerResearchBrain" ^
#       --ticker MSFT --query "Q2 FY26 earnings guidance" --top-k 5
# =============================================================================

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Console output (Phase 7A.1)
# A Windows console defaults to a legacy code page - usually cp1252 - which
# cannot encode characters that appear in archive text and in model answers
# (the non-breaking hyphen U+2011, curly quotes, dashes). Without this, a
# report could die halfway through printing. Ask for UTF-8 up front; anything
# still unprintable is replaced rather than raised.
# ---------------------------------------------------------------------------

def configure_console_encoding() -> None:
    for _name in ("stdout", "stderr"):
        _stream = getattr(sys, _name, None)
        if _stream is None or not hasattr(_stream, "reconfigure"):
            continue
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass


configure_console_encoding()


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
PREVIEW_CHARS = 700


def collection_name_for(ticker: str) -> str:
    return f"cutler_research_brain_{ticker.lower()}_2026"


def load_settings(project_root: Path) -> dict:
    """Read .env. The key is used, never shown."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        env_path = project_root / ".env"
        load_dotenv(env_path if env_path.exists() else None)
    except ImportError:
        pass

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "ERROR: OPENAI_API_KEY is not set.\n"
            "  1. Copy .env.example to .env in the project root\n"
            "  2. Put your key in OPENAI_API_KEY\n"
            "  (the key is never printed or logged by these scripts)"
        )
    return {
        "api_key": api_key,
        "model": os.environ.get("OPENAI_EMBEDDING_MODEL", "").strip() or DEFAULT_EMBEDDING_MODEL,
        "base_url": os.environ.get("OPENAI_BASE_URL", "").strip(),
        "timeout": float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "60") or 60),
    }


def embed_query(settings: dict, query: str) -> list:
    """One embeddings call for the query. No other endpoint is touched."""
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit("ERROR: the openai package is not installed.\n"
                         "  pip install -r requirements.txt\n"
                         f"  ({exc})") from exc

    kwargs = {"api_key": settings["api_key"], "timeout": settings["timeout"]}
    if settings["base_url"]:
        kwargs["base_url"] = settings["base_url"]
    client = OpenAI(**kwargs)

    try:
        response = client.embeddings.create(model=settings["model"], input=[query])
    except Exception as exc:  # noqa: BLE001 - network, auth, rate limit
        raise SystemExit(f"ERROR: could not embed the query ({type(exc).__name__}: {exc})") from exc
    return response.data[0].embedding


def open_collection(project_root: Path, ticker: str, collection_name: str):
    try:
        import chromadb  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit("ERROR: chromadb is not installed.\n"
                         "  pip install -r requirements.txt\n"
                         f"  ({exc})") from exc

    path = project_root / "06_RAG_Index" / "chroma" / ticker
    if not path.exists():
        raise SystemExit(
            f"ERROR: no Chroma store at {path}\n"
            f"  Build it first: python scripts\\build_rag_index.py --ticker {ticker} ..."
        )
    client = chromadb.PersistentClient(path=str(path))
    try:
        return client.get_collection(name=collection_name)
    except Exception as exc:  # noqa: BLE001 - missing collection
        available = ""
        try:
            names = [c.name for c in client.list_collections()]
            available = f"\n  Collections present: {', '.join(names) or '(none)'}"
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(f"ERROR: collection '{collection_name}' not found in {path}"
                         f"{available}\n  ({type(exc).__name__})") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cutler Research Brain Phase 5A - retrieval test. Prints matching chunks; "
                    "it never generates an answer.",
    )
    parser.add_argument("--project-root", required=True,
                        help=r'Project root, e.g. "C:\Users\AI Admin\Documents\CutlerResearchBrain"')
    parser.add_argument("--ticker", default="MSFT", help="Ticker index to search (default MSFT).")
    parser.add_argument("--query", required=True, help="The search text.")
    parser.add_argument("--top-k", type=int, default=5, help="How many chunks to return.")
    parser.add_argument("--collection", default="", help="Override the collection name.")
    parser.add_argument("--full-text", action="store_true",
                        help="Print the whole chunk instead of a preview.")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    ticker = args.ticker.strip().upper()
    collection_name = args.collection.strip() or collection_name_for(ticker)

    settings = load_settings(project_root)
    collection = open_collection(project_root, ticker, collection_name)

    print("=" * 78)
    print(f"RETRIEVAL TEST - {ticker}")
    print(f"Query      : {args.query}")
    print(f"Collection : {collection_name}")
    print(f"Model      : {settings['model']}  (embeddings only)")
    print("=" * 78)

    vector = embed_query(settings, args.query)

    try:
        results = collection.query(
            query_embeddings=[vector],
            n_results=max(1, args.top_k),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"ERROR: Chroma query failed ({type(exc).__name__}: {exc})") from exc

    ids = (results.get("ids") or [[]])[0]
    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]

    if not ids:
        print("\nNo matching chunks. Is the index built for this ticker?")
        return 0

    for rank, chunk_id in enumerate(ids, start=1):
        meta = metadatas[rank - 1] or {}
        distance = distances[rank - 1] if rank - 1 < len(distances) else None
        text = documents[rank - 1] if rank - 1 < len(documents) else ""

        similarity = (1 - distance) if isinstance(distance, (int, float)) else None
        page_start = meta.get("page_start_guess", -1)
        page_end = meta.get("page_end_guess", -1)
        pages = f"pages {page_start}-{page_end}" if isinstance(page_start, int) and page_start > 0 \
            else "no page info"

        print()
        print("-" * 78)
        print(f"RANK {rank}   distance {distance:.4f}" if distance is not None else f"RANK {rank}")
        if similarity is not None:
            print(f"  cosine similarity     ~{similarity:.4f}")
        print(f"  chunk_id              {chunk_id}")
        print(f"  document_id           {meta.get('document_id', '(unknown)')}")
        print(f"  file_name             {meta.get('file_name', '(unknown)')}")
        print(f"  folder_date_guess     {meta.get('folder_date_guess', '(none)')}")
        print(f"  source_type_guess     {meta.get('source_type_guess', '(none)')}")
        print(f"  document_type_guess   {meta.get('document_type_guess', '(none)')}")
        print(f"  chunk / {pages:<24} #{meta.get('chunk_index', '?')}")
        print(f"  relative_path         {meta.get('relative_path', '(none)')}")
        print()

        body = " ".join(str(text or "").split())
        if not args.full_text and len(body) > PREVIEW_CHARS:
            body = body[:PREVIEW_CHARS].rstrip() + " ..."
        for wrapped in textwrap.wrap(body, width=74) or ["(empty chunk)"]:
            print(f"    {wrapped}")

    print()
    print("=" * 78)
    print("Retrieval only. These are verbatim extracted-text chunks, ranked by embedding")
    print("similarity to your query. No answer was generated, nothing was summarized or")
    print("interpreted, and no chat/completions call was made. A chunk being returned says")
    print("only that its wording is close to the query - it is not evidence of anything.")
    print("Read the documents themselves before drawing any conclusion.")
    print("AI drafts. Humans verify. Cutler decides.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
