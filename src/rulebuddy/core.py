#!/usr/bin/env python3
"""core.py - retrieval and the model call, shared by the app.

Not meant to be run on its own. Build an index with the indexer, then open
the window:

    python -m rulebuddy.indexer index yourbook.pdf
    copy config.example.json config.json   # then put your key in it
    python -m rulebuddy

Settings come from config.json in the application folder: the project root when
running from source, the folder holding the exe when packaged. ANTHROPIC_API_KEY
in the environment overrides the key there, and command line flags override the
rest.
"""

import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
RULE_NUMBER = re.compile(r"\b(?:[A-Z]|\d{1,3})(?:\.\d{1,3}){1,3}\b")
WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]+|\d+(?:\.\d+)+")
STOP = set("""a an the and or but if then than that this these those of in on at to for from by with
without into over under about above below is are was were be been being do does did doing have has
had having i you he she it we they me him her them my your his its our their what which who whom
when where why how can could may might must shall should will would there here as not no nor only
own same so too very just also any each few more most other some such only does can do get got""".split())

DB = {"path": "rulebook.db"}
CONFIG = {"model": DEFAULT_MODEL, "key": os.environ.get("ANTHROPIC_API_KEY", ""),
          "books_dir": "books"}

def app_dir():
    """The folder holding config.json and books/.

    Packaged, that is the folder the exe sits in, which is what the user sees.
    From source it is the project root, three levels up from this file:
    src/rulebuddy/core.py -> src/rulebuddy -> src -> the root.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DEFAULT_CONFIG = os.path.join(app_dir(), "config.json")
DEFAULT_BOOKS = os.path.join(app_dir(), "books")


def load_config(path=None):
    """Read api_key, model and db from a JSON file. The environment still wins.

    Returns the parsed file so callers can pick out settings of their own.
    """
    path = path or DEFAULT_CONFIG
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as err:
        sys.exit(f"Cannot read config {path}: {err}")
    if not isinstance(data, dict):
        sys.exit(f"Config {path} must hold a JSON object.")

    key = str(data.get("api_key", "")).strip()
    if key and not os.environ.get("ANTHROPIC_API_KEY"):
        CONFIG["key"] = key
    model = str(data.get("model", "")).strip()
    if model:
        CONFIG["model"] = model
    db_path = str(data.get("db", "")).strip()
    if db_path:
        DB["path"] = db_path
    books = str(data.get("books_dir", "")).strip()
    if books:
        CONFIG["books_dir"] = books
    return data


# ----------------------------------------------------------------- retrieval

def connect():
    if not os.path.exists(DB["path"]):
        sys.exit(f"No index at {DB['path']}."
                 " Run: python -m rulebuddy.indexer index yourbook.pdf")
    db = sqlite3.connect(DB["path"], check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def query_terms(question):
    """Turn a plain question into an FTS5 query. Keeps phrases, drops filler."""
    words = [w.lower() for w in WORD.findall(question)]
    kept = [w for w in words if w not in STOP and len(w) > 2][:14]
    if not kept:
        kept = words[:6]
    if not kept:
        return None
    parts = [f'"{w}"' for w in dict.fromkeys(kept)]
    for a, b in zip(kept, kept[1:]):
        parts.append(f'"{a} {b}"')
    return " OR ".join(parts)


def retrieve(db, question, limit=10):
    """Find the sections that answer a question, then pull in what they cite."""
    query = query_terms(question)
    if not query:
        return []
    rows = db.execute("""
        SELECT s.id, s.path, s.title, s.number, s.page_start, s.page_end, s.text, s.styles
        FROM sections_fts f JOIN sections s ON s.id = f.rowid
        WHERE sections_fts MATCH ?
        ORDER BY bm25(sections_fts, 4.0, 2.0, 1.0) LIMIT ?""", (query, limit)).fetchall()

    found = {r["id"]: dict(r) for r in rows}
    for row in list(found.values()):
        row["cited"] = False
        numbers = list(dict.fromkeys(RULE_NUMBER.findall(row["text"])))[:4]
        for number in numbers:
            if number == row["number"]:
                continue
            for extra in db.execute(
                "SELECT id,path,title,number,page_start,page_end,text,styles FROM sections"
                " WHERE number=? AND part=0 LIMIT 1", (number,)):
                if extra["id"] not in found:
                    item = dict(extra)
                    item["cited"] = True
                    found[item["id"]] = item
    return list(found.values())[:limit + 6]


# --------------------------------------------------------------- the model

def build_prompt(question, sources, outline):
    lines = ["Excerpts from the rulebook:\n"]
    budget = 60000
    for src in sources:
        text = src["text"][:6000]
        block = (f"[#{src['id']} p.{src['page_start']}] {src['path']}"
                 f"{' (cross-reference)' if src.get('cited') else ''}\n{text}\n")
        if budget - len(block) < 0:
            break
        budget -= len(block)
        lines.append(block)
    if outline:
        lines.append("\nBook outline (top levels):\n" + outline)
    lines.append(f"\nQuestion: {question}")
    return "\n".join(lines)


SYSTEM = """You answer questions about one rulebook. The user gives you excerpts from it.

Rules for your answer:
- Use only the excerpts. Do not add outside knowledge about the game or system.
- Cite every claim. Put the marker [#ID p.PAGE] at the end of the sentence it supports, using the ID and page from the excerpt header.
- If the excerpts do not settle the question, say so plainly, then name the terms or sections the user should search next.
- Paraphrase. Quote at most one short line, and only when the exact wording decides the answer.
- Be short. Lead with the ruling, then the conditions and exceptions.
- If two excerpts conflict, say which one is more specific and note the conflict."""


def ask_model(question, sources, outline, history):
    if not CONFIG["key"]:
        return {"error": "no_key",
                "text": "No API key. Put one in config.json or set ANTHROPIC_API_KEY, "
                        "then restart, to get written answers. The sections listed are "
                        "the search result."}
    messages = []
    for turn in history[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"][:4000]})
    messages.append({"role": "user", "content": build_prompt(question, sources, outline)})

    body = json.dumps({"model": CONFIG["model"], "max_tokens": 1200,
                       "system": SYSTEM, "messages": messages}).encode()
    request = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": CONFIG["key"],
        "anthropic-version": API_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        return {"error": "http", "text": f"The API returned {err.code}. {detail}"}
    except Exception as err:  # network, timeout, bad JSON
        return {"error": "network", "text": f"The request failed: {err}"}
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return {"text": text or "The model returned nothing."}


if __name__ == "__main__":
    sys.exit("This module holds shared code. Run: python -m rulebuddy")