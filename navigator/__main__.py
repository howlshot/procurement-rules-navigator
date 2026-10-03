"""Command line.

    python -m navigator build                 # read sources/, embed passages, write index/index.json
    python -m navigator ask "What is the NYC micropurchase limit?"
    python -m navigator serve                 # local web page on 127.0.0.1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import search
from .answer import ask
from .llm import Cached, OpenAICompatible, make_provider
from .sources import load_manifest, load_passages

ROOT = Path.cwd()
INDEX = ROOT / "index/index.json"


def embedder(base_url: str | None):
    """Query embeddings from the local server: one request at a time, retried, and remembered.

    LM Studio can refuse an embedding request while the chat model is busy
    with parallel work, so calls are serialized and retried with a pause.
    """
    import threading
    import time

    from .llm import ModelError

    local = OpenAICompatible("unused", base_url or os.environ.get("NAVIGATOR_EMBED_URL", "http://localhost:1234/v1"))
    lock = threading.Lock()
    memo: dict[str, list[float]] = {}

    def embed(texts: list[str]) -> list[list[float]]:
        with lock:
            missing = [t for t in texts if t not in memo]
            for attempt in range(4):
                if not missing:
                    break
                try:
                    for t, vec in zip(missing, local.embed(missing, search.EMBED_MODEL)):
                        memo[t] = vec
                    missing = []
                except ModelError:
                    if attempt == 3:
                        raise
                    time.sleep(2 * (attempt + 1))
            return [memo[t] for t in texts]

    return embed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="navigator", description="Answers about New York and federal procurement rules, quoted from the rules.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="Read the sources and build the search index.")
    b.add_argument("--no-embeddings", action="store_true", help="Keyword search only.")
    b.add_argument("--embed-url", help="Embedding server. Default $NAVIGATOR_EMBED_URL or http://localhost:1234/v1.")
    for name in ("ask", "serve"):
        p = sub.add_parser(name)
        if name == "ask":
            p.add_argument("question")
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--port", type=int, default=8766)
        p.add_argument("--provider", default="local", choices=["local", "anthropic"])
        p.add_argument("--model")
        p.add_argument("--base-url", help="Chat server. Default http://localhost:1234/v1.")
        p.add_argument("--embed-url", help="Embedding server. Default $NAVIGATOR_EMBED_URL or the chat server's default.")
        p.add_argument("--cache", default="runs/cache")
    args = parser.parse_args(argv)
    docs = load_manifest(ROOT)

    if args.command == "build":
        passages = load_passages(ROOT, docs)
        vectors = None if args.no_embeddings else search.embed_passages(passages, embedder(args.embed_url), ROOT / "index/embeddings-cache.json")
        search.save(INDEX, passages, vectors)
        print(f"{len(passages)} passages from {len({p.doc for p in passages})} documents -> {INDEX.relative_to(ROOT)}")
        return 0

    if not INDEX.exists():
        print("No index yet. Run: python -m navigator build", file=sys.stderr)
        return 1
    index = search.load(INDEX, docs)
    provider = Cached(make_provider(args.provider, args.model, args.base_url), args.cache)
    embed = embedder(args.embed_url) if index.vectors else None

    if args.command == "serve":
        from .server import serve

        serve(index, provider, embed, args.port)
        return 0

    result = ask(args.question, index, provider, embed)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    print(f"[{result.status}] {result.answer}\n")
    for c in result.citations:
        print(f"  {c.label} {c.cite} ({c.verification}): \"{c.quote}\"")
    for d in result.disagreements:
        print(f"  ! {d['summary']} ({'; '.join(s['cite'] for s in d['sources'])})")
    for w in result.warnings:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
