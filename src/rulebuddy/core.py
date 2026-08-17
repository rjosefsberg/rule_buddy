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


# ---------------------------------------------------------------- the key

def has_key():
    return bool(CONFIG["key"])


def looks_like_key(key):
    """Catch an obvious paste error before spending a request on it."""
    key = (key or "").strip()
    return key.startswith("sk-ant-") and len(key) > 30


def verify_key(key):
    """Ask the API whether this key works. Returns (ok, message).

    The smallest call the endpoint accepts, so a typo costs a round trip rather
    than a real request.
    """
    body = json.dumps({"model": CONFIG["model"], "max_tokens": 1,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    request = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key.strip(),
        "anthropic-version": API_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True, "The key works."
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            return False, "The API rejected that key."
        detail = err.read().decode("utf-8", "replace")[:200]
        if err.code == 429:
            return False, f"The key is rate limited right now. {detail}"
        return False, f"The API returned {err.code}. {detail}"
    except Exception as err:
        return False, f"Could not reach the API: {err}"


def save_setting(key, value, path=None):
    """Write one setting into config.json, leaving the rest of it alone.

    Returns (ok, message). The packaged app may sit on read-only media, so a
    refusal here is ordinary and the caller is expected to carry on.
    """
    path = path or DEFAULT_CONFIG
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
    except OSError as err:
        return False, f"Could not write {path}: {err}"
    return True, path


def set_key(key, persist=False):
    """Use this key from now on, optionally writing it to config.json."""
    CONFIG["key"] = (key or "").strip()
    if persist:
        return save_setting("api_key", CONFIG["key"])
    return True, "Kept for this session only."


def clear_key(forget=True):
    """Stop using the key, and take it out of config.json unless told not to."""
    CONFIG["key"] = ""
    if forget:
        return save_setting("api_key", None)
    return True, ""


# ------------------------------------------------------------------- schema

SCHEMA_VERSION = 2


def ensure_schema(db):
    """Bring an index up to the current shape, in place.

    Version 1 held one book per file: the source PDF lived in meta, and there
    was a single cover. Version 2 makes the file a collection, with a books
    table and every section owned by one of them. A version 1 index is migrated
    by reading its meta into a single book row, which is what it always was.
    """
    have = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    db.execute("""CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY,
        title TEXT, source TEXT, pages INTEGER, added TEXT, cover BLOB)""")
    if "meta" not in have:
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    columns = {row[1] for row in db.execute("PRAGMA table_info(sections)")}
    if columns and "book_id" not in columns:
        db.execute("ALTER TABLE sections ADD COLUMN book_id INTEGER")

    empty = db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    orphans = 0
    if columns:
        orphans = db.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    # Only a version 1 index needs the migration: sections already written, but
    # nothing owning them. A file just created is empty, not old.
    if empty and orphans:
        source = db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
        pages = db.execute("SELECT value FROM meta WHERE key='pages'").fetchone()
        cover = None
        if "cover" in have:
            row = db.execute("SELECT png FROM cover WHERE id=1").fetchone()
            cover = row[0] if row else None
        path = source[0] if source else ""
        db.execute("INSERT INTO books (id,title,source,pages,added,cover)"
                   " VALUES (1,?,?,?,?,?)",
                   (title_from_path(path), path,
                    int(pages[0]) if pages and str(pages[0]).isdigit() else 0,
                    "", cover))
        db.execute("UPDATE sections SET book_id=1 WHERE book_id IS NULL")

    db.execute("INSERT OR REPLACE INTO meta VALUES ('schema',?)", (str(SCHEMA_VERSION),))
    db.commit()


# A small-caps font can hold its lowercase letters in a Private Use Area, at the
# ASCII code plus this offset. The text then carries no letter a reader knows:
# "Wicked" comes out as "W" and five code points from plane 15. Tk cannot draw a
# character above the BMP either, so such a title reaches the window as "W".
PUA_BASE = 0xF0000
PUA_MAP = {PUA_BASE + code: chr(code) for code in range(0x20, 0x7F)}


def unpua(text):
    """Put Private Use letters back to ASCII, one character for one character.

    The length does not change, so the style codes still line up with the text.
    """
    return text.translate(PUA_MAP) if text else text


def title_from_path(path):
    """A readable book name from a PDF filename."""
    stem = os.path.splitext(os.path.basename(path or ""))[0]
    return stem.replace("_", " ").strip() or "Untitled"


def collection_name(db):
    """What to call the whole file: its given name, else its first book."""
    row = db.execute("SELECT value FROM meta WHERE key='name'").fetchone()
    if row and row[0]:
        return row[0]
    first = db.execute("SELECT title FROM books ORDER BY id LIMIT 1").fetchone()
    return first[0] if first else os.path.splitext(os.path.basename(DB["path"]))[0]


# ----------------------------------------------------------------- retrieval

def connect():
    if not os.path.exists(DB["path"]):
        sys.exit(f"No index at {DB['path']}."
                 " Run: python -m rulebuddy.indexer index yourbook.pdf")
    db = sqlite3.connect(DB["path"], check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_schema(db)
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


def retrieve(db, question, limit=25):
    """Find the sections that answer a question, then pull in what they cite."""
    query = query_terms(question)
    if not query:
        return []
    rows = db.execute("""
        SELECT s.id, s.book_id, s.path, s.title, s.number, s.page_start,
               s.page_end, s.text, s.styles, b.title AS book, b.source AS source
        FROM sections_fts f JOIN sections s ON s.id = f.rowid
        LEFT JOIN books b ON b.id = s.book_id
        WHERE sections_fts MATCH ?
        ORDER BY bm25(sections_fts, 4.0, 2.0, 1.0) LIMIT ?""", (query, limit)).fetchall()

    found = {r["id"]: dict(r) for r in rows}
    # The window lists results by page, so the match order has to travel with
    # the rows. Without it the best match cannot be found again after sorting.
    for rank, row in enumerate(found.values()):
        row["rank"] = rank
    for row in list(found.values()):
        row["cited"] = False
        numbers = list(dict.fromkeys(RULE_NUMBER.findall(row["text"])))[:4]
        for number in numbers:
            if number == row["number"]:
                continue
            for extra in db.execute(
                "SELECT s.id,s.book_id,s.path,s.title,s.number,s.page_start,"
                " s.page_end,s.text,"
                " s.styles, b.title AS book, b.source AS source FROM sections s"
                " LEFT JOIN books b ON b.id = s.book_id"
                " WHERE s.number=? AND s.part=0 LIMIT 1", (number,)):
                if extra["id"] not in found:
                    item = dict(extra)
                    item["cited"] = True
                    item["rank"] = len(found)
                    found[item["id"]] = item
    return join_chunks(db, list(found.values())[:limit + 6])


# A long section is stored as several overlapping chunks, and a search that hits
# a whole chapter returns each chunk as its own result. Seven rows of "Socialize"
# are one passage, so they go back together before anything sees them.

def join_chunks(db, rows, gap=40):
    """Fold the chunks of one section into a single result.

    Chunks of a section share a book and a path. The stretch between the first
    and the last chunk that matched is read back in full, so the joined text has
    no hole where a chunk in the middle failed to match.
    """
    groups = {}
    for row in rows:
        groups.setdefault((row["book_id"], row["path"]), []).append(row)

    out = []
    for (book_id, path), members in groups.items():
        if len(members) < 2:
            out += members
            continue
        ids = sorted(m["id"] for m in members)
        if ids[-1] - ids[0] > gap:      # too far apart to be one passage
            out += members
            continue
        full = db.execute(
            "SELECT id, page_start, page_end, text, styles FROM sections"
            " WHERE book_id IS ? AND path = ? AND id BETWEEN ? AND ?"
            " ORDER BY id", (book_id, path, ids[0], ids[-1])).fetchall()
        out.append(joined(members, full or members))
    out.sort(key=lambda r: r["rank"])
    return out


def overlap(before, after):
    """How many opening lines of `after` repeat the tail of `before`.

    The indexer overlaps its chunks so a passage is never cut in half. Joined
    back together, that overlap would print the same paragraphs twice.
    """
    most = min(len(before), len(after))
    for size in range(most, 0, -1):
        if before[-size:] == after[:size]:
            return size
    return 0


def joined(members, pieces):
    """One result out of several chunks, keeping the styles lined up.

    The chunks overlap on purpose, so the shared paragraphs are dropped as each
    piece goes on. The style runs move with the text they mark.
    """
    best = min(members, key=lambda r: r["rank"])
    merged = dict(best)
    texts, runs, lines, at = [], [], [], 0
    for piece in pieces:
        text = piece["text"] or ""
        own = text.split("\n")
        drop = overlap(lines, own)
        cut = len("\n".join(own[:drop])) + (1 if 0 < drop < len(own) else 0)
        for start, end, code in json.loads(piece["styles"] or "[]"):
            if end > cut:
                runs.append([max(0, start - cut) + at, end - cut + at, code])
        texts.append(text[cut:])
        lines += own[drop:]
        at += len(text) - cut + 1               # the newline that joins them
    merged["text"] = "\n".join(texts)
    merged["styles"] = json.dumps(runs, separators=(",", ":")) if runs else None
    merged["page_start"] = min(p["page_start"] for p in pieces)
    merged["page_end"] = max(p["page_end"] for p in pieces)
    # Every chunk keeps its id, so a citation to any of them still resolves.
    merged["members"] = [p["id"] for p in pieces]
    merged["cited"] = all(m.get("cited") for m in members)
    return merged


# --------------------------------------------------------------- the model

def build_prompt(question, sources, outline):
    lines = ["Excerpts from the rulebook:\n"]
    budget = 60000
    for src in sources:
        # A joined section carries a whole Charm tree, so one excerpt is much
        # longer than a chunk was. There are fewer of them now, and the budget
        # below still holds the whole prompt down.
        text = src["text"][:15000]
        # The book has to be in the header: page 87 means nothing on its own once
        # a collection holds more than one book.
        book = f"{src['book']} — " if src.get("book") else ""
        block = (f"[#{src['id']} p.{src['page_start']}] {book}{src['path']}"
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
- The excerpts may come from several books in one collection. Name the book in the sentence when it matters, and never merge page numbers from different books.
- A supplement that revises the core book wins where they overlap. Say so when the two disagree.
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