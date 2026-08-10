#!/usr/bin/env python3
"""rulebook.py - index a bookmarked PDF rulebook and search it from the command line.

Requires: pip install pymupdf

    python rulebook.py index book.pdf
    python rulebook.py search "sustained action"
    python rulebook.py show 42
    python rulebook.py page 271
    python rulebook.py refs 14.3
    python rulebook.py toc
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import textwrap

try:
    import pymupdf
except ImportError:  # older installs expose the same module as fitz
    try:
        import fitz as pymupdf
    except ImportError:
        sys.exit("PyMuPDF is missing. Run: pip install pymupdf")

DEFAULT_DB = "rulebook.db"
MAX_CHUNK_WORDS = 450
FTS_OPERATORS = re.compile(r'["*(]|\b(?:AND|OR|NOT|NEAR)\b')
# Matches rule numbers such as 14, 14.3, 14.3.2, A.4
RULE_NUMBER = re.compile(r"\b(?:[A-Z]|\d{1,3})(?:\.\d{1,3}){1,3}\b")
LEADING_NUMBER = re.compile(r"^\s*(?:[A-Z]|\d{1,3})(?:\.\d{1,3})*\.?\s+")


# ---------------------------------------------------------------- extraction

def flow_key(page, x0, y, width_of_block=0.0):
    """Reading order on a two-column page: all of the left column, then the right.

    Ordering by y alone zips the two columns together and cuts sentences in half.
    """
    width = page.rect.width or 1
    height = page.rect.height or 1
    column = 0 if (width_of_block > 0.6 * width or x0 < 0.5 * width) else 1
    return column * (height + 1000) + y


def heading_y(page, title, floor):
    """Return the reading-order position of a heading on a page, or None."""
    candidates = []
    for probe in (title, LEADING_NUMBER.sub("", title)):
        probe = probe.strip()
        if len(probe) < 3:
            continue
        try:
            candidates = page.search_for(probe)
        except Exception:
            candidates = []
        if candidates:
            break
    tops = sorted(flow_key(page, r.x0, r.y0) for r in candidates)
    for y in tops:
        if floor is None or y > floor + 1:
            return y
    return None


def read_outline(doc):
    """Return outline entries with a page and a vertical position."""
    raw = doc.get_toc(simple=True)
    if not raw:
        sys.exit("This PDF has no bookmarks. Index by page range instead, or add bookmarks.")
    entries = []
    for level, title, page_1based in raw:
        page_ix = max(0, page_1based - 1)
        entries.append({"level": level, "title": " ".join(title.split()), "page": page_ix})

    last_page, floor = None, None
    for entry in entries:
        if entry["page"] != last_page:
            floor = None
        page = doc[entry["page"]]
        entry["y"] = heading_y(page, entry["title"], floor)
        floor = entry["y"] if entry["y"] is not None else floor
        last_page = entry["page"]
    return entries


# "Sto-\n\t ryteller" and "Storytell- er" both mean one word split by the column.
# The second form is also how a suspended hyphen looks ("two- or three-dot"), so
# leave those joined to the word after them.
# The book breaks words with a plain hyphen or a soft hyphen (U+00AD), and the
# halves may be split by a newline, by tabs, or by nothing but a space.
HYPHENS = "‐‑­-"   # the plain hyphen stays last so it cannot form a range
SPLIT_WORD = re.compile(r"([\w%s]*\w)[%s]\s*\n\s*(?=\w)" % (HYPHENS, HYPHENS))
SPLIT_LOOSE = re.compile(
    r"([\w%s]*\w)[%s] +(?!or\b|and\b|to\b|nor\b|but\b)(?=[a-z])" % (HYPHENS, HYPHENS))
SPLIT_TAIL = re.compile(r"(\w)­(?=\w)")


def rejoin(match):
    """Drop the break hyphen, unless the word is a compound like 'head-to-head'."""
    head = match.group(1)
    return head + ("-" if any(h in head for h in HYPHENS) else "")


def clean(raw, marks=None):
    """Join words broken across lines or columns, then collapse whitespace.

    `marks` is one style code per character of `raw`. It is carried through the
    same edits so the codes still line up with the text that comes out, which is
    what lets the styles column address the stored text by offset.
    """
    if marks is None:
        text = SPLIT_WORD.sub(rejoin, raw)   # join before stripping, or the gap remains
        text = SPLIT_LOOSE.sub(rejoin, text)
        text = SPLIT_TAIL.sub(r"\1", text)   # a soft hyphen inside an unbroken word
        return " ".join(text.split())

    for pattern in (SPLIT_WORD, SPLIT_LOOSE):
        raw, marks = cut(raw, marks, pattern, keeps_hyphen=True)
    raw, marks = cut(raw, marks, SPLIT_TAIL)
    return squeeze(raw, marks)


def cut(text, marks, pattern, keeps_hyphen=False):
    """Apply one join to text and marks together, dropping the same characters."""
    out, kept, at = [], [], 0
    for match in pattern.finditer(text):
        head_end = match.end(1)
        out.append(text[at:head_end])
        kept.append(marks[at:head_end])
        if keeps_hyphen and any(h in match.group(1) for h in HYPHENS):
            out.append("-")                  # a compound keeps its own hyphen
            kept.append(marks[head_end])
        at = match.end()
    out.append(text[at:])
    kept.append(marks[at:])
    return "".join(out), "".join(kept)


def squeeze(text, marks):
    """Collapse runs of whitespace to one space, keeping the marks aligned."""
    out, kept = [], []
    for match in re.finditer(r"\S+|\s+", text):
        piece = match.group(0)
        if piece.isspace():
            if out:                          # never open with a space
                out.append(" ")
                kept.append(" ")
            continue
        out.append(piece)
        kept.append(marks[match.start():match.end()])
    while out and out[-1] == " ":             # nor close with one
        out.pop()
        kept.pop()
    return "".join(out), "".join(kept)


def spaced_caps(text):
    """True for letter-spaced banners like 'M A R T I A L  A R T S'.

    Counting words cannot catch these: every letter reads as its own word, so a
    running head of two words looks like twenty and survives a length test.
    """
    tokens = text.split()
    if len(tokens) < 5:
        return False
    singles = sum(1 for t in tokens if len(t) == 1)
    return singles / len(tokens) >= 0.6


BOLD_FONT = re.compile(r"bold|semibold|black|heavy", re.I)
ITALIC_FONT = re.compile(r"italic|oblique", re.I)
BULLET_START = re.compile(r"^\s*[•▪◦]")
HEADING_MARK = "## "
PLAIN = " "


def style_code(font):
    """One character naming a span's style: b, i, x for both, or a space."""
    bold, italic = bool(BOLD_FONT.search(font)), bool(ITALIC_FONT.search(font))
    return "x" if bold and italic else "b" if bold else "i" if italic else PLAIN


def runs_of(marks):
    """Turn per-character codes into [[start, end, code]] for the styled stretches.

    The single space between two words is unstyled, so a styled phrase arrives
    here as one run per word. Runs of the same style are rejoined across it.
    """
    runs, start = [], None
    for i, code in enumerate(marks + PLAIN):
        if start is not None and (i == len(marks) or marks[i] != marks[start]):
            if marks[start] != PLAIN:
                runs.append([start, i, marks[start]])
            start = None
        if start is None and i < len(marks) and marks[i] != PLAIN:
            start = i

    merged = []
    for run in runs:
        if merged and merged[-1][2] == run[2] and run[0] - merged[-1][1] <= 1:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    return merged


def line_of(line):
    """One laid-out line: its text, a style code per character, and whether it is bold."""
    text = "".join(span["text"] for span in line["spans"])
    marks = "".join(style_code(span["font"]) * len(span["text"]) for span in line["spans"])
    bold = all(BOLD_FONT.search(span["font"]) for span in line["spans"] if span["text"].strip())
    return text, marks, bold


def is_heading(text, bold, size, body_size):
    """Headings here share the body size and differ only by weight, so use both."""
    stripped = text.strip()
    if not stripped or len(stripped.split()) > 10:
        return False
    if BULLET_START.match(stripped):
        return False
    if size > body_size * 1.25:            # chapter and section openers
        return True
    return bold and not stripped.endswith((".", ",", ":", ";"))


def body_font_size(doc, sample=40):
    """The most common span size in the book, i.e. the size of ordinary text."""
    counts = {}
    step = max(1, doc.page_count // sample)
    for page_ix in range(0, doc.page_count, step):
        for block in doc[page_ix].get_text("dict")["blocks"]:
            for line in block.get("lines", ()):
                for span in line["spans"]:
                    if span["text"].strip():
                        key = round(span["size"], 1)
                        counts[key] = counts.get(key, 0) + len(span["text"])
    return max(counts, key=counts.get) if counts else 10.0


def merge_heading_runs(items):
    """Fold a run of three or more headings back into prose.

    Sidebars are set in small caps, so line after line looks like a heading. A
    real heading stands alone or runs to two lines; a longer run is body text.
    Its first line is kept as the sidebar's title.
    """
    out, run = [], []
    mark_len = len(HEADING_MARK)

    def settle():
        if len(run) >= 3:
            out.append(run[0])
            body, marks = clean("\n".join(t[mark_len:] for _, t, _ in run[1:]),
                                "\n".join(m[mark_len:] for _, _, m in run[1:]))
            out.append((run[1][0], body, marks))
        else:
            out.extend(run)
        run.clear()

    for y, text, marks in items:
        if text.startswith(HEADING_MARK):
            run.append((y, text, marks))
            continue
        settle()
        out.append((y, text, marks))
    settle()
    return out


def join_split_paragraphs(items):
    """Rejoin a word broken across two blocks, e.g. 'Will-' then 'power rating'.

    Nothing in prose ends a paragraph on a hyphen, so this is safe to assume.
    """
    out = []
    for y, text, marks in items:
        if (out and not text.startswith(HEADING_MARK)
                and not out[-1][1].startswith(HEADING_MARK)
                and out[-1][1].endswith(tuple(HYPHENS)) and text[:1].islower()):
            joined, kept = clean(out[-1][1] + "\n" + text, out[-1][2] + PLAIN + marks)
            out[-1] = (out[-1][0], joined, kept)
            continue
        out.append((y, text, marks))
    # A hyphen still dangling at the end had nothing to join to.
    return [(y, t.rstrip(HYPHENS), m[:len(t.rstrip(HYPHENS))]) for y, t, m in out]


def extract_pages(doc, drop_running_heads=True, progress=None):
    """Return {page_index: [(y, text)]} for the whole book, top to bottom.

    One entry per paragraph. Headings keep their own entry, marked with '## ',
    so they do not run into the sentence that follows them.
    """
    body_size = body_font_size(doc)
    pages, zones = {}, {}
    for page_ix in range(doc.page_count):
        if progress and page_ix % 10 == 0:
            progress("Reading pages", page_ix, doc.page_count)
        page = doc[page_ix]
        height = page.rect.height or 1
        out = []

        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block:
                continue
            x0, top, x1, bottom = block["bbox"]
            running = top < 0.08 * height or bottom > 0.92 * height
            pending, pending_y = [], flow_key(page, x0, top, x1 - x0)

            def flush(buffer, y, in_margin=running):
                text, marks = clean("\n".join(t for t, _ in buffer),
                                    "\n".join(m for _, m in buffer))
                if not text:
                    return
                # Page numbers and chapter banners live in the margin, and are
                # either short or set as letter-spaced caps.
                if in_margin and drop_running_heads and (
                        len(text.split()) <= 8 or spaced_caps(text)):
                    return
                out.append((y, text, marks))
                if in_margin:
                    zones.setdefault(re.sub(r"\d+", "#", text)[:60], set()).add(page_ix)

            # A heading is one or two lines. A longer run of bold or small-caps
            # lines is a sidebar's body text, so keep it as an ordinary paragraph.
            heads = []

            def settle(heads, pending, pending_y):
                if not heads:
                    return pending, pending_y
                if len(heads) >= 3:
                    if not pending:
                        pending_y = heads[0][2]
                    return pending + [(t, m) for t, m, _ in heads], pending_y
                flush(pending, pending_y)
                for text, marks, y in heads:
                    trimmed = text.strip()
                    offset = text.index(trimmed) if trimmed else 0
                    flush([(HEADING_MARK + trimmed,
                            PLAIN * len(HEADING_MARK) + marks[offset:offset + len(trimmed)])], y)
                return [], pending_y

            for line in block["lines"]:
                text, marks, bold = line_of(line)
                if not text.strip():
                    continue
                size = max((s["size"] for s in line["spans"] if s["text"].strip()), default=body_size)
                lx0, ly, lx1, _ = line["bbox"]
                y = flow_key(page, lx0, ly, x1 - x0)   # the block decides the column
                if is_heading(text, bold, size, body_size):
                    heads.append((text, marks, y))
                    continue
                pending, pending_y = settle(heads, pending, pending_y)
                heads = []
                if BULLET_START.match(text) and pending:   # each bullet stands alone
                    flush(pending, pending_y)
                    pending, pending_y = [], y
                if not pending:
                    pending_y = y
                pending.append((text, marks))
            pending, pending_y = settle(heads, pending, pending_y)
            flush(pending, pending_y)

        pages[page_ix] = join_split_paragraphs(merge_heading_runs(sorted(out)))

    if not drop_running_heads:
        return pages
    threshold = max(3, doc.page_count // 4)
    repeated = {k for k, v in zones.items() if len(v) >= threshold}
    if not repeated:
        return pages
    for page_ix, lines in pages.items():
        pages[page_ix] = [(y, t, m) for y, t, m in lines
                          if re.sub(r"\d+", "#", t)[:60] not in repeated]
    return pages


def page_lines(doc, page_ix, cache):
    """Return [(y, text)] for one page, sorted top to bottom."""
    return cache.get(page_ix, [])


def section_body(doc, entry, nxt, cache, last_page):
    """Collect (page_number, text, marks) triples that belong to one outline entry."""
    start = entry["page"]
    if nxt is None:
        end, end_y = last_page, None
    elif nxt["y"] is None:
        end, end_y = max(start, nxt["page"] - 1), None
    else:
        end, end_y = nxt["page"], nxt["y"]

    out = []
    for page_ix in range(start, end + 1):
        for y, text, marks in page_lines(doc, page_ix, cache):
            if page_ix == start and entry["y"] is not None and y < entry["y"] - 1:
                continue
            if page_ix == end and end_y is not None and y >= end_y - 1:
                continue
            out.append((page_ix + 1, text, marks))
    return out


def chunk(body):
    """Split a long section into overlapping chunks. Keeps page numbers exact."""
    if not body:
        return []
    chunks, current, words = [], [], 0
    for page, text, marks in body:
        current.append((page, text, marks))
        words += len(text.split())
        if words >= MAX_CHUNK_WORDS:
            chunks.append(current)
            current = current[-1:]  # one line of overlap
            words = len(current[0][1].split())
    if len(current) > 1 or not chunks:
        chunks.append(current)
    return chunks


# ------------------------------------------------------------------- indexing

def mark_skipped(entries, patterns):
    """Flag outline entries whose title matches any pattern, and their children.

    Matching is a case-insensitive substring, so "chapter one" is enough for
    "Chapter One: The Exalted". Returns the titles that matched.
    """
    for entry in entries:
        entry["skip"] = False
    if not patterns:
        return []

    wanted = [p.strip().lower() for p in patterns if p.strip()]
    # A lone top entry is the book itself, and every chapter hangs off it.
    # Skipping it can only mean its own front matter, never the whole book.
    roots = [e for e in entries if e["level"] == 1]
    book = roots[0] if len(roots) == 1 else None

    matched, under = [], None
    for entry in entries:
        if under is not None and entry["level"] > under:
            entry["skip"] = True          # a subsection of something skipped
            continue
        under = None
        if any(w in entry["title"].lower() for w in wanted):
            entry["skip"] = True
            under = None if entry is book else entry["level"]
            matched.append(entry["title"])
    return matched


def build(pdf_path, db_path, keep_heads=False, skip=(), progress=None):
    if os.path.exists(db_path):
        os.remove(db_path)
    if progress:
        progress("Reading the outline", 0, 0)
    doc = pymupdf.open(pdf_path)
    entries = read_outline(doc)
    last_page = doc.page_count - 1

    matched = mark_skipped(entries, skip)
    for pattern in skip:
        if not any(pattern.strip().lower() in title.lower() for title in matched):
            print(f"Nothing in the outline matches {pattern!r}.")
    for title in matched:
        print(f"Skipping {title}")

    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            path TEXT, title TEXT, number TEXT, level INTEGER,
            page_start INTEGER, page_end INTEGER, part INTEGER, text TEXT,
            styles TEXT
        );
        CREATE VIRTUAL TABLE sections_fts USING fts5(
            title, path, text, content='sections', content_rowid='id',
            tokenize='porter unicode61'
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX sections_pages ON sections(page_start, page_end);
    """)

    cache = extract_pages(doc, drop_running_heads=not keep_heads, progress=progress)
    stack, rows = [], []
    for i, entry in enumerate(entries):
        if progress and i % 10 == 0:
            progress("Splitting sections", i, len(entries))
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        stack = stack[: entry["level"] - 1]
        stack.append(entry["title"])
        if entry["skip"]:
            continue        # nxt still comes from the full outline, so the
        path = " > ".join(stack)   # section before it keeps its true end
        number_match = LEADING_NUMBER.match(entry["title"])
        number = number_match.group(0).strip().rstrip(".") if number_match else ""

        body = section_body(doc, entry, nxt, cache, last_page)
        for part, piece in enumerate(chunk(body)):
            text = "\n".join(t for _, t, _ in piece)
            if not text.strip():
                continue
            # The newline between paragraphs is unstyled, so the marks line up
            # with the joined text exactly as the pieces did on their own.
            marks = PLAIN.join(m for _, _, m in piece)
            runs = runs_of(marks) if len(marks) == len(text) else []
            rows.append((path, entry["title"], number, entry["level"],
                         piece[0][0], piece[-1][0], part, text,
                         json.dumps(runs, separators=(",", ":")) if runs else None))

    if progress:
        progress("Writing the index", len(entries), len(entries))
    db.executemany(
        "INSERT INTO sections (path,title,number,level,page_start,page_end,part,text,styles)"
        " VALUES (?,?,?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO sections_fts (rowid,title,path,text)"
               " SELECT id,title,path,text FROM sections")
    db.execute("INSERT INTO meta VALUES ('source',?)", (os.path.abspath(pdf_path),))
    db.execute("INSERT INTO meta VALUES ('pages',?)", (str(doc.page_count),))
    if matched:
        db.execute("INSERT INTO meta VALUES ('skipped',?)", ("; ".join(matched),))
    db.commit()
    print(f"Indexed {len(entries)} sections into {len(rows)} chunks from "
          f"{doc.page_count} pages -> {db_path}")


# -------------------------------------------------------------------- queries

def connect(db_path):
    if not os.path.exists(db_path):
        sys.exit(f"No index at {db_path}. Run: python rulebook.py index yourbook.pdf")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    return db


def fts_query(words):
    raw = " ".join(words)
    if FTS_OPERATORS.search(raw):
        return raw
    terms = [f'"{w}"' for w in raw.split()]
    return " AND ".join(terms)


def wrap(text, indent="    "):
    return textwrap.fill(text, width=88, initial_indent=indent,
                         subsequent_indent=indent)


BULLET = re.compile(r"^\s*[•▪◦·\-]\s+")


def render(text, indent="    "):
    """Lay a chunk out as paragraphs. Each stored line is one block of the page."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(HEADING_MARK):
            out.append(indent + line[len(HEADING_MARK):].upper())
        elif BULLET.match(line):  # hang the wrap under the bullet
            body = BULLET.sub("", line)
            out.append(textwrap.fill(body, width=88,
                                     initial_indent=indent + "• ",
                                     subsequent_indent=indent + "  "))
        else:
            out.append(wrap(line, indent))
    return "\n\n".join(out)


def cmd_search(args):
    db = connect(args.db)
    query = fts_query(args.words)
    try:
        rows = db.execute("""
            SELECT s.id, s.path, s.page_start, s.page_end, s.part, s.text,
                   snippet(sections_fts, 2, '>>', '<<', ' ... ', 18) AS snip
            FROM sections_fts f JOIN sections s ON s.id = f.rowid
            WHERE sections_fts MATCH ?
            ORDER BY bm25(sections_fts, 4.0, 2.0, 1.0)
            LIMIT ?""", (query, args.limit)).fetchall()
    except sqlite3.OperationalError as err:
        sys.exit(f"Bad query: {err}")

    if not rows:
        print("No match. Try fewer words, or a quoted phrase.")
        return
    for row in rows:
        pages = (f"p.{row['page_start']}" if row["page_start"] == row["page_end"]
                 else f"pp.{row['page_start']}-{row['page_end']}")
        part = f" [chunk {row['part'] + 1}]" if row["part"] else ""
        print(f"\n#{row['id']}  {pages}{part}  {row['path']}")
        print(render(row["text"]) if args.full else wrap(row["snip"]))
    print()


def cmd_show(args):
    db = connect(args.db)
    row = db.execute("SELECT * FROM sections WHERE id=?", (args.id,)).fetchone()
    if not row:
        sys.exit(f"No chunk with id {args.id}")
    print(f"{row['path']}   (pp.{row['page_start']}-{row['page_end']})\n")
    print(render(row["text"], indent=""))
    seen = {n for n in RULE_NUMBER.findall(row["text"])}
    seen.discard(row["number"])
    if seen:
        print("\nCross-references in this text:")
        for number in sorted(seen):
            hits = db.execute(
                "SELECT id,title,page_start FROM sections WHERE number=? AND part=0",
                (number,)).fetchall()
            for hit in hits:
                print(f"  {number} -> #{hit['id']} {hit['title']} (p.{hit['page_start']})")


def cmd_page(args):
    db = connect(args.db)
    rows = db.execute(
        "SELECT id,path,page_start,page_end FROM sections"
        " WHERE page_start<=? AND page_end>=? ORDER BY id", (args.number, args.number)
    ).fetchall()
    if not rows:
        print(f"Nothing indexed on page {args.number}.")
    for row in rows:
        print(f"#{row['id']}  pp.{row['page_start']}-{row['page_end']}  {row['path']}")


def cmd_refs(args):
    db = connect(args.db)
    rows = db.execute(
        "SELECT id,path,page_start,text FROM sections WHERE text LIKE ?",
        (f"%{args.number}%",)).fetchall()
    hits = [r for r in rows if args.number in RULE_NUMBER.findall(r["text"])]
    if not hits:
        print(f"No section cites {args.number}.")
    for row in hits:
        print(f"#{row['id']}  p.{row['page_start']}  {row['path']}")


def cmd_toc(args):
    db = connect(args.db)
    rows = db.execute(
        "SELECT id,title,level,page_start FROM sections WHERE part=0 ORDER BY id"
    ).fetchall()
    for row in rows:
        if args.depth and row["level"] > args.depth:
            continue
        print(f"{'  ' * (row['level'] - 1)}{row['title']}  (p.{row['page_start']}) #{row['id']}")


def cmd_export(args):
    """Print top hits as JSON, ready to paste into a chat window."""
    db = connect(args.db)
    rows = db.execute("""
        SELECT s.id, s.path, s.page_start, s.page_end, s.text
        FROM sections_fts f JOIN sections s ON s.id = f.rowid
        WHERE sections_fts MATCH ?
        ORDER BY bm25(sections_fts, 4.0, 2.0, 1.0) LIMIT ?""",
        (fts_query(args.words), args.limit)).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


# ----------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Search a bookmarked PDF rulebook.")
    parser.add_argument("--db", default=DEFAULT_DB, help="index file (default rulebook.db)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="build the index from a PDF")
    p.add_argument("pdf")
    p.add_argument("--keep-running-heads", action="store_true",
                   help="do not strip repeated page headers and footers")
    p.add_argument("--skip", nargs="+", default=[], metavar="TITLE",
                   help="outline entries to leave out, matched as case-insensitive "
                        "substrings. Subsections of a match are skipped too. "
                        'e.g. --skip "chapter one" "chapter two" index credits')
    p.set_defaults(func=lambda a: build(a.pdf, a.db, a.keep_running_heads, a.skip))

    p = sub.add_parser("search", help="keyword search")
    p.add_argument("words", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=8)
    p.add_argument("-f", "--full", action="store_true", help="print whole chunks")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="print one chunk and its cross-references")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("page", help="list sections on a page")
    p.add_argument("number", type=int)
    p.set_defaults(func=cmd_page)

    p = sub.add_parser("refs", help="find sections that cite a rule number")
    p.add_argument("number")
    p.set_defaults(func=cmd_refs)

    p = sub.add_parser("toc", help="print the outline")
    p.add_argument("-d", "--depth", type=int, default=0)
    p.set_defaults(func=cmd_toc)

    p = sub.add_parser("export", help="dump top hits as JSON for a chat window")
    p.add_argument("words", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=6)
    p.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        os._exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
