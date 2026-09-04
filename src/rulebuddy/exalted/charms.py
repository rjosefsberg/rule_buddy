#!/usr/bin/env python3
"""charms.py - a library of one mechanic, pulled out of the indexed books.

Exalted writes every Charm to the same skeleton: a name, then Cost, Mins, Type,
Keywords, Duration, and Prerequisite Charms, then the prose. That skeleton is
regular enough to read back out of the index, so the library is built from the
sections already stored, and no PDF is opened again.

Aeon calls the same thing a Power and writes it the same way, so the reader here
is not named after Exalted.

The rows live in their own file, exalted-charms.db, separate from any one
collection. A collection's sections are only read to build the library; once
built, deleting or reimporting that collection does not touch it. Each row
carries its own book title and PDF path, since it can no longer join back to
a collection's books table.
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
SENTENCE_END = (".", "!", "?", "”", "’")


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


def parse(text, group="", lead="", trail_source=""):
    """Read every Charm in one section's text. Returns a list of dicts.

    `lead` is the tail of the section before this one. A long section is stored
    in chunks, and a chunk can open on a block of statistics whose name was cut
    off by the boundary. The tail gives that name back.

    `trail_source` is the raw text of the section after this one, in full. The
    boundary can also land inside a Charm's prose, right after its stat block,
    which leaves the last Charm in a chunk with no body at all. `lead_trail`
    pulls that body back out of it, for the chunk-final Charm only.
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
        elif trail_source and not body.strip().endswith(SENTENCE_END):
            trail = lead_trail(trail_source, block.group(0))
            # The overlap repeats the stat block's last field at the head of
            # the trail. Drop that line; only the prose after it is new.
            head, _, rest = trail.partition("\n")
            if "Prerequisite Charms:" in head:
                trail = rest
            body = f"{body}\n{trail}" if body.strip() else trail
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


def lead_trail(text, own_header=""):
    """The prose at the head of a section, before its first heading or Charm.

    Sister to `last_line`: that gives a cut-off name back to the chunk that
    follows it, this gives a cut-off body back to the chunk before it.

    A book that never marks a Charm's name as a heading (its font carries no
    bold or size signal the indexer can see) never triggers the `## ` stop
    below. Stopping at the next full stat block too catches that case: a
    complete Cost/Mins/Type/.../Prerequisite Charms run is as reliable a
    "a new Charm starts here" signal as a heading mark, heading or not.

    `own_header` is the calling Charm's own matched stat block. The overlap
    that cut its body off can also repeat that whole block verbatim at the
    head of the next chunk, not just its last field - that is not a new
    Charm starting, so a block matching it is skipped rather than stopped at.
    """
    text = text or ""
    if text.startswith(HEADING_MARK):
        return ""
    heading_at = text.find("\n" + HEADING_MARK)
    own_header = tidy(own_header)
    block_at, search_from = -1, 0
    while True:
        block = BLOCK.search(text, search_from)
        if not block:
            break
        if own_header and tidy(block.group(0)) == own_header:
            search_from = block.end()
            continue
        block_at = block.start()
        break
    stops = [p for p in (heading_at, block_at) if p != -1]
    return text[:min(stops)] if stops else text


def group_of(path):
    """The tree a Charm belongs to: the last part of its section path."""
    tail = (path or "").split(" > ")[-1].strip()
    return re.sub(r"\s+Charms$", "", tail)


# ------------------------------------------------------------------ storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS charms (
    id INTEGER PRIMARY KEY,
    collection TEXT, book_title TEXT, book_source TEXT,
    name TEXT, tree TEXT, ability TEXT, rating INTEGER, essence INTEGER,
    cost TEXT, mins TEXT, type TEXT, keywords TEXT, duration TEXT,
    prereqs TEXT, page INTEGER, text TEXT, simple_text TEXT);
CREATE INDEX IF NOT EXISTS charms_name ON charms(name);
CREATE INDEX IF NOT EXISTS charms_collection ON charms(collection);
CREATE VIRTUAL TABLE IF NOT EXISTS charms_fts USING fts5(
    name, keywords, text, content='charms', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2");
"""


def ensure_schema(db):
    db.executescript(SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(charms)")}
    if "simple_text" not in columns:
        db.execute("ALTER TABLE charms ADD COLUMN simple_text TEXT")
    db.commit()


def build(source_db, charms_db, collection, progress=None):
    """Read every section of one collection and rebuild its slice of the library.

    `collection` identifies the collection this build is for, usually its file
    path. Only that collection's charms are replaced, so building one
    collection leaves every other collection's charms as they were.

    A long section is stored as overlapping chunks, so the same Charm can be
    read twice. The longer body wins, because the short one lost its tail to
    the chunk boundary.
    """
    ensure_schema(charms_db)
    # A simplified rewrite is paid-for work, done outside this build. Carry it
    # forward by name so a rebuild - a parser fix, a reindex - does not throw
    # it away; only a Charm whose name actually changes loses its match.
    simplified = {row["name"].lower(): row["simple_text"] for row in charms_db.execute(
        "SELECT name, simple_text FROM charms"
        " WHERE collection = ? AND simple_text IS NOT NULL", (collection,))}
    charms_db.execute("DELETE FROM charms WHERE collection = ?", (collection,))

    books = {row["id"]: (row["title"], row["source"])
             for row in source_db.execute("SELECT id, title, source FROM books")}
    rows = source_db.execute("SELECT id, book_id, path, page_start, text"
                             " FROM sections ORDER BY id").fetchall()
    best, tail = {}, {}
    for i, row in enumerate(rows):
        if progress and i % 50 == 0:
            progress("Reading the books", i, len(rows))
        lead = tail.get(row["book_id"], "")
        tail[row["book_id"]] = last_line(row["text"])
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        trail_source = (nxt["text"]
                        if nxt and nxt["book_id"] == row["book_id"] else "")
        for charm in parse(row["text"], group_of(row["path"]),
                            lead=lead, trail_source=trail_source):
            key = (row["book_id"], charm["name"].lower())
            if key in best and len(best[key]["text"]) >= len(charm["text"]):
                continue
            title, source = books.get(row["book_id"], ("", ""))
            charm["collection"] = collection
            charm["book_title"] = title
            charm["book_source"] = source
            charm["page"] = row["page_start"]
            charm["simple_text"] = simplified.get(charm["name"].lower())
            best[key] = charm

    charms_db.executemany(
        "INSERT INTO charms (collection, book_title, book_source, name, tree,"
        " ability, rating, essence, cost, mins, type, keywords, duration,"
        " prereqs, page, text, simple_text)"
        " VALUES (:collection,:book_title,:book_source,:name,:group,:ability,"
        ":rating,:essence,:cost,:mins,:type,:keywords,:duration,:prereqs,"
        ":page,:text,:simple_text)",
        list(best.values()))
    charms_db.execute("INSERT INTO charms_fts(charms_fts) VALUES('rebuild')")
    charms_db.commit()
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
        rows = db.execute("SELECT DISTINCT book_title FROM charms"
                          " WHERE book_title <> '' ORDER BY book_title")
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
    joins = "FROM charms c"
    if terms.strip():
        joins += " JOIN charms_fts f ON f.rowid = c.id"
        where.append("charms_fts MATCH ?")
        args.append(fts_query(terms))
    for column, value in (("c.tree", tree), ("c.type", type_), ("c.book_title", book)):
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

    sql = ("SELECT c.*, c.book_title AS book, c.book_source AS source " + joins
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY c.tree, c.essence, c.rating, c.name")
    return db.execute(sql, args).fetchall()


def fts_query(terms):
    """Plain words, quoted, so a stray quote or hyphen cannot break the search.

    Each word is a prefix match, so "revolv" finds "Revolving" without the
    user typing the whole word out.
    """
    words = re.findall(r"[\w’']+", terms)
    return " ".join(f'"{w}"*' for w in words) or '""'
