#!/usr/bin/env python3
"""charms.py - a library of one mechanic, pulled out of the indexed books.

Exalted writes every Charm to the same skeleton: a name, then Cost, Mins, Type,
Keywords, Duration, and Prerequisite Charms, then the prose. That skeleton is
regular enough to read back out of the index, so the library is built from the
sections already stored, and no PDF is opened again.

Aeon calls the same thing a Power and writes it the same way, so the reader here
is not named after Exalted.

The rows live in the collection file beside the sections, which keeps a library
with the books it came from.
"""

import re
import sqlite3
import unicodedata

FIELDS = ("cost", "mins", "type", "keywords", "duration", "prereqs")


# One block of statistics. The fields always come in this order, and each one
# ends where the next begins, so the pattern does not care whether the book put
# them on six lines or ran them all onto one.
#
# No field may cross a line. The separator between two fields can be a space or
# a line break, because a book sets the block either way, but a field that ate a
# line break would swallow the sidebar underneath it.
BLOCK = re.compile(
    r"Cost:[ \t]*(?P<cost>[^\n]*?)[;\s]*"
    r"Mins:[ \t]*(?P<mins>[^\n]*?)\s*"
    r"Type:[ \t]*(?P<type>[^\n]*?)\s*"
    r"Keywords:[ \t]*(?P<keywords>[^\n]*?)\s*"
    r"Duration:[ \t]*(?P<duration>[^\n]*?)\s*"
    r"Prerequisite Charms:[ \t]*(?P<prereqs>[^\n]*)")

# A field holds a cost or a word or two. Anything longer means the book broke
# the skeleton, and the block is not a Charm.
MAX_FIELD = 90

# "Archery 4, Essence 1" and "Essence 2" both appear. Essence is its own rating,
# and whatever else is named is the Ability the Charm is bought with.
RATING = re.compile(r"([A-Za-z][A-Za-z /’'-]*?)\s+(\d+)")
HEADING_MARK = "## "
NONE_WORDS = {"none", "—", "-", "–", ""}


def split_list(value):
    """'Decisive-only, Mute' or 'None' into a clean list.

    A keyword can carry a bracket of its own, as in 'Keystone (Perception,
    Wits)', so a comma inside brackets does not separate anything.
    """
    value = tidy(value)
    if value.lower() in NONE_WORDS:
        return []
    parts, depth, current = [], 0, []
    for char in value:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char in ",;" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    parts = [p.strip(" .;") for p in parts]
    return [p for p in parts if p and p.lower() not in NONE_WORDS]


def read_mins(mins):
    """Pull the Ability and the Essence rating out of a Mins line."""
    ability, rating, essence = "", 0, 0
    for name, number in RATING.findall(mins or ""):
        name = name.strip()
        if name.lower() == "essence":
            essence = int(number)
        elif not ability:
            ability, rating = name, int(number)
    return ability, rating, essence


MAX_NAME = 60


def name_before(text, at):
    """The Charm name that sits in front of a block of statistics.

    A book that gives the name its own line leaves the line above. A book that
    runs the whole thing together leaves the name at the head of the same line.
    The last two lines are read, because a name can be broken across a column
    and arrive as the tail of one line and the head of the next.
    """
    head = tidy_text(text[:at]).rstrip()
    if not head:
        return ""
    lines = [line.strip() for line in head.split("\n")[-2:]]
    line = lines[-1]
    if line.startswith(HEADING_MARK):
        line = line[len(HEADING_MARK):].strip()
    # A run-on line carries the tail of the sentence before it, so start the
    # name after the last full stop.
    if len(line) > MAX_NAME:
        line = re.split(r"(?<=[.!?])\s+", line)[-1].strip()
    # A name is a title, so it opens with a capital. Anything in front of the
    # first capital belongs to the sentence before it.
    words = line.split()
    while words and not words[0][:1].isupper():
        words.pop(0)
    if not words and len(lines) > 1:              # the name broke across a line
        return name_of(lines[-2] + " " + line)
    return name_of(" ".join(words))


def name_of(line):
    """Hold a candidate name to what a name can look like."""
    words = line.split()
    while words and not words[0][:1].isupper():
        words.pop(0)
    name = " ".join(words)
    return name if 1 < len(name) <= MAX_NAME else ""


def parse(text, group="", lead=""):
    """Read every Charm in one section's text. Returns a list of dicts.

    `lead` is the tail of the section before this one. A long section is stored
    in chunks, and a chunk can open on a block of statistics whose name was cut
    off by the boundary. The tail gives that name back.
    """
    text = (lead + "\n" + text) if lead else (text or "")
    found = []
    blocks = list(BLOCK.finditer(text))
    for i, block in enumerate(blocks):
        name = name_before(text, block.start())
        if not name:
            continue
        if any(len(block.group(f) or "") > MAX_FIELD for f in FIELDS):
            continue
        body_from = block.end()
        body_to = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        body = text[body_from:body_to]
        # The next Charm's name is the last line of this stretch, so give it
        # back. Strip first: a trailing line break would hide that line.
        if i + 1 < len(blocks):
            body = body.rstrip()
            body = body[:body.rfind("\n")] if "\n" in body else ""
        ability, rating, essence = read_mins(block.group("mins"))
        found.append({
            "name": name,
            "group": group,
            "cost": tidy(block.group("cost")),
            "mins": tidy(block.group("mins")),
            "ability": ability or group,
            "rating": rating,
            "essence": essence,
            "type": tidy(block.group("type")),
            "keywords": ", ".join(split_list(block.group("keywords"))),
            "duration": tidy(block.group("duration")),
            "prereqs": ", ".join(split_list(block.group("prereqs"))),
            "text": tidy(body),
        })
    return found


def tidy_text(value):
    """Put ligatures back to letters, so 'Reﬂexive' filters as 'Reflexive'.

    The indexer cannot do this. A ligature stands for two letters, so the text
    would grow, and the styles column addresses that text by offset.
    """
    return unicodedata.normalize("NFKC", value or "")


def tidy(value):
    return " ".join(tidy_text(value).split()).strip(" ;:")


def last_line(text):
    """The final line of a section, which may be the next Charm's name."""
    lines = (text or "").rstrip().split("\n")
    return lines[-1].strip() if lines else ""


def group_of(path):
    """The tree a Charm belongs to: the last part of its section path."""
    tail = (path or "").split(" > ")[-1].strip()
    return re.sub(r"\s+Charms$", "", tail)


# ------------------------------------------------------------------ storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS charms (
    id INTEGER PRIMARY KEY,
    book_id INTEGER, section_id INTEGER,
    name TEXT, tree TEXT, ability TEXT, rating INTEGER, essence INTEGER,
    cost TEXT, mins TEXT, type TEXT, keywords TEXT, duration TEXT,
    prereqs TEXT, page INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS charms_name ON charms(name);
CREATE VIRTUAL TABLE IF NOT EXISTS charms_fts USING fts5(
    name, keywords, text, content='charms', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2");
"""


def ensure_schema(db):
    db.executescript(SCHEMA)
    db.commit()


def build(db, progress=None):
    """Read every section in the collection and rebuild the library.

    A long section is stored as overlapping chunks, so the same Charm can be
    read twice. The longer body wins, because the short one lost its tail to
    the chunk boundary.
    """
    ensure_schema(db)
    db.execute("DELETE FROM charms_fts")
    db.execute("DELETE FROM charms")

    rows = db.execute("SELECT id, book_id, path, page_start, text FROM sections"
                      " ORDER BY id").fetchall()
    best, tail = {}, {}
    for i, row in enumerate(rows):
        if progress and i % 50 == 0:
            progress("Reading the books", i, len(rows))
        lead = tail.get(row["book_id"], "")
        tail[row["book_id"]] = last_line(row["text"])
        for charm in parse(row["text"], group_of(row["path"]), lead=lead):
            key = (row["book_id"], charm["name"].lower())
            if key in best and len(best[key]["text"]) >= len(charm["text"]):
                continue
            charm["book_id"] = row["book_id"]
            charm["section_id"] = row["id"]
            charm["page"] = row["page_start"]
            best[key] = charm

    db.executemany(
        "INSERT INTO charms (book_id, section_id, name, tree, ability, rating,"
        " essence, cost, mins, type, keywords, duration, prereqs, page, text)"
        " VALUES (:book_id,:section_id,:name,:group,:ability,:rating,:essence,"
        ":cost,:mins,:type,:keywords,:duration,:prereqs,:page,:text)",
        list(best.values()))
    db.execute("INSERT INTO charms_fts (rowid, name, keywords, text)"
               " SELECT id, name, keywords, text FROM charms")
    db.commit()
    if progress:
        progress("Done", len(rows), len(rows))
    return len(best)


def counted(db):
    """How many Charms the library holds, or 0 when it was never built."""
    try:
        return db.execute("SELECT COUNT(*) FROM charms").fetchone()[0]
    except sqlite3.Error:
        return 0


def choices(db, column):
    """Every value a column takes, for a filter menu."""
    if column not in {"tree", "ability", "type", "book"}:
        return []
    if column == "book":
        rows = db.execute("SELECT DISTINCT b.title FROM charms c"
                          " JOIN books b ON b.id = c.book_id"
                          " WHERE b.title IS NOT NULL ORDER BY b.title")
        return [r[0] for r in rows]
    rows = db.execute(f"SELECT DISTINCT {column} FROM charms"
                      f" WHERE {column} <> '' ORDER BY {column}")
    return [r[0] for r in rows]


def keywords_in(db):
    """Every keyword used, split back out of the stored list."""
    seen = set()
    for row in db.execute("SELECT keywords FROM charms WHERE keywords <> ''"):
        seen.update(split_list(row[0]))
    return sorted(seen)


def search(db, terms="", tree="", type_="", keyword="", book="", essence=0):
    """Find Charms. Every filter is optional, and they narrow together."""
    where, args = [], []
    joins = ("FROM charms c LEFT JOIN books b ON b.id = c.book_id")
    if terms.strip():
        joins += " JOIN charms_fts f ON f.rowid = c.id"
        where.append("charms_fts MATCH ?")
        args.append(fts_query(terms))
    for column, value in (("c.tree", tree), ("c.type", type_), ("b.title", book)):
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    if keyword:
        # The column holds a list, so match the word between its separators.
        where.append("(', ' || c.keywords || ', ') LIKE ?")
        args.append(f"%, {keyword}, %")
    if essence:
        where.append("c.essence <= ?")
        args.append(int(essence))

    sql = ("SELECT c.*, b.title AS book " + joins
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY c.tree, c.essence, c.rating, c.name")
    return db.execute(sql, args).fetchall()


def fts_query(terms):
    """Plain words, quoted, so a stray quote or hyphen cannot break the search."""
    words = re.findall(r"[\w’']+", terms)
    return " ".join(f'"{w}"' for w in words) or '""'
