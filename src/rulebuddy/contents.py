#!/usr/bin/env python3
"""contents.py - build an outline from a book's table of contents page.

Many rulebooks carry no PDF bookmarks, so the indexer has nothing to split on.
Their printed contents page holds the same information. The user names the page,
this module reads it, and the result is an outline the user can correct.

    python -m rulebuddy.contents book.pdf --pages 15 16
    python -m rulebuddy.contents book.pdf --pages 4 5 6 --out outline.json

The PDF must carry a text layer. A scan holds no text for the indexer to read
either, so a scanned book needs OCR over every page before any of this helps.

Requires: pip install pymupdf.
"""

import argparse
import json
import os
import re
import sys
import unicodedata

try:
    import pymupdf
except ImportError:                 # older installs expose the same module as fitz
    try:
        import fitz as pymupdf
    except ImportError:
        sys.exit("PyMuPDF is missing. Run: pip install pymupdf")

SCHEMA_VERSION = 1
ROW_TOLERANCE = 4.0     # points. Two lines this close share a row.
BARE_NUMBER = re.compile(r"\d{1,4}")
MAX_LEVEL = 4


# ------------------------------------------------------------------ reading

def page_lines(page):
    """Every line on a page as (x0, y0, height, size, text)."""
    data = page.get_text("dict")
    out = []
    for block in data["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            x0, y0, _, y1 = line["bbox"]
            size = max((s["size"] for s in line["spans"]), default=0.0)
            out.append((x0, y0, y1 - y0, size, text))
    return out


def number_columns(lines, width):
    """Where the page numbers run, as one x per column, left to right.

    A contents page prints its numbers in straight vertical lines. Those lines
    say how many columns the page has, and where each one ends. Splitting at the
    middle of the page instead cuts through a column whenever there are three,
    which puts a title in one half and its number in the other.
    """
    xs = sorted(line[0] for line in lines if BARE_NUMBER.fullmatch(line[4]))
    if not xs:
        return []
    gap = max(8.0, width * 0.04)
    runs = []
    for x in xs:
        if not runs or x - runs[-1][-1] > gap:
            runs.append([x])
        else:
            runs[-1].append(x)
    # A real column carries many numbers. A stray digit in the artwork does not.
    return [sum(run) / len(run) for run in runs if len(run) >= 3]


def columns_of(lines, width):
    """Split lines into columns, so a title stays with its own page number."""
    if not lines:
        return []
    marks = number_columns(lines, width)
    if len(marks) < 2:
        return [lines]

    slack = max(8.0, width * 0.04)
    groups = [[] for _ in marks]
    for line in lines:
        # A line belongs to the first column whose number line is at or to the
        # right of it. The slack covers a number that starts a shade late.
        for i, mark in enumerate(marks):
            if line[0] <= mark + slack:
                groups[i].append(line)
                break
    return [group for group in groups if group]


def rows_of(lines):
    """Group the lines of one column into rows that share a baseline."""
    rows = []
    for line in sorted(lines, key=lambda item: item[1]):
        if rows and abs(line[1] - rows[-1][0]) <= ROW_TOLERANCE:
            rows[-1][1].append(line)
        else:
            rows.append((line[1], [line]))
            rows[-1] = (line[1], rows[-1][1])
    return [(y, sorted(items)) for y, items in rows]


# ------------------------------------------------------------------ parsing

def entries_from_rows(rows):
    """Turn rows into entries. One row is a title and its page number.

    A title too long for the line wraps onto the next row, which then carries no
    number of its own. Such a row belongs to the entry above it.
    """
    entries = []
    pending = []          # wrapped lines waiting for the row that holds the number
    for y, items in rows:
        numbers = [i for i in items if BARE_NUMBER.fullmatch(i[4])]
        titles = [i for i in items if not BARE_NUMBER.fullmatch(i[4])]
        if not titles:
            continue
        # The leftmost text is the title. Anything after it on the same row is
        # either the number or a dot leader read as characters.
        x0, y0, height, size, text = titles[0]
        text = clean_title(text)
        if not text or not plausible_title(text):
            continue
        if not numbers:
            # A title too long for one line wraps, and the page number is
            # printed against the last line of it. So a row with no number
            # opens the next entry rather than closing the one before it.
            pending.append((text, x0, y0, height, size))
            continue

        if pending:
            pending = [p for p in pending if wraps_into(p, y0, height, size)]
        if pending:
            x0, y0, height, size = pending[0][1:]
            text = " ".join([part[0] for part in pending] + [text])
        pending = []
        entries.append({"title": text, "printed": int(numbers[-1][4]),
                        "x": round(x0, 1), "y": round(y0, 1),
                        "number_x": round(numbers[-1][0], 1),
                        "height": round(height, 1), "size": round(size, 1)})
    return in_number_column(entries)


def wraps_into(pending, y0, height, size):
    """True when a numberless line is the first half of the entry below it.

    The rest of a wrapped title sits on the very next line, in the same type.
    A page heading such as "Table of Contents" sits further up and is set
    larger, so it belongs to neither entry.
    """
    _, _, pending_y, pending_height, pending_size = pending
    if y0 - pending_y > max(28.0, 2.5 * max(height, pending_height)):
        return False
    if size > 0 and pending_size > size * 1.4:
        return False
    return True


def plausible_title(text):
    """Reject the noise a decorated page adds to the text.

    Real entries are words. Junk is short, or mostly punctuation, or a run of
    single letters. Requiring letters and one word of decent length removes it
    without touching real titles.
    """
    letters = re.findall(r"[A-Za-z]", text)
    # Three letters, because real sections are called "Sex" and "War".
    if len(letters) < 3:
        return False
    if len(letters) / len(text) < 0.6:
        return False
    return bool(re.search(r"[A-Za-z]{3,}", text))


def in_number_column(entries, tolerance=40.0):
    """Keep the entries whose page number sits in the usual number column.

    A contents page prints its numbers in a straight line down the page. A
    number found far from that line came from the artwork, not the contents.
    """
    if len(entries) < 4:
        return entries
    counts = {}
    for entry in entries:
        key = round(entry["number_x"] / 20.0) * 20.0
        counts[key] = counts.get(key, 0) + 1
    column = max(counts, key=counts.get)
    return [e for e in entries if abs(e["number_x"] - column) <= tolerance]


def clean_title(text):
    """Drop dot leaders and stray punctuation, and undo typographic ligatures.

    A book sets "fi" as a single glyph. Left alone it reaches the search index
    as one character, and a search for "fire" never matches it.
    """
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[.•·_\s]{4,}$", "", text)     # trailing leaders
    text = re.sub(r"\s{2,}", " ", text).strip(" .•-—")
    return text.strip()


def indent_anchors(values, width, keep=MAX_LEVEL):
    """The indents a book really uses, found by counting rather than chaining.

    Indents wobble by a point or two, and a decorated page adds strays. Grouping
    on the gap between neighbours then chains every value into one run, or
    splits it at random. Counting is stable: most entries are ordinary sections,
    so the indents a book really uses are the values that repeat.

    The grid scales with the page, because page sizes differ.
    """
    if not values:
        return [0.0]
    grid = max(4.0, width * 0.012)
    counts = {}
    for value in values:
        cell = round(value / grid) * grid
        counts[cell] = counts.get(cell, 0) + 1
    # Popular first, so the rare ones fall off, then read left to right.
    common = sorted(counts, key=lambda cell: -counts[cell])[:keep]
    return sorted(common)


def nearest(value, anchors):
    """Index of the anchor closest to this value."""
    return min(range(len(anchors)), key=lambda i: abs(value - anchors[i]))


def assign_levels(entries, width):
    """Give each entry a level from its indent, then its type size.

    Indent is the reliable signal. Size only breaks a tie, and only when the
    difference is large, because a section set in a heavy face can measure
    taller than the chapter above it.
    """
    if not entries:
        return entries
    anchors = indent_anchors([e["indent"] for e in entries], width)
    measure = "size"
    big = max(e[measure] for e in entries)
    small = min(e[measure] for e in entries)
    span = big - small

    for entry in entries:
        level = nearest(entry["indent"], anchors) + 1
        # A line much larger than the rest is a chapter, whatever its indent.
        # Some books centre chapter lines at a deeper x than their sections.
        if span > 0 and entry[measure] >= small + span * 0.75:
            level = 1
        entry["level"] = min(level, MAX_LEVEL)
    return normalize_levels(entries)


def normalize_levels(entries):
    """Make the levels legal: start at 1, and never jump by more than one."""
    previous = 0
    for entry in entries:
        if previous == 0:
            entry["level"] = 1
        elif entry["level"] > previous + 1:
            entry["level"] = previous + 1
        previous = entry["level"]
    return entries


# ------------------------------------------------------------- page numbers

def label_map(doc):
    """Map a printed page label to its page index, when the PDF carries labels.

    A labelled book needs no arithmetic. Books number their front matter apart
    from their body, and the labels already record that.
    """
    out = {}
    for index in range(doc.page_count):
        try:
            label = doc[index].get_label()
        except Exception:
            label = ""
        if label and label not in out:
            out[label] = index
    return out


def resolve_pages(doc, entries, offset=None):
    """Turn each printed number into a page index. Returns the method used."""
    if offset is not None:
        for entry in entries:
            entry["page"] = entry["printed"] + offset
        return f"offset {offset:+d} given by the user"

    labels = label_map(doc)
    hits = sum(1 for e in entries if str(e["printed"]) in labels)
    if hits >= max(3, len(entries) // 2):
        for entry in entries:
            entry["page"] = labels.get(str(entry["printed"]), entry["printed"])
        return f"page labels ({hits} of {len(entries)} matched)"

    # No labels. The printed number equals the index in every book tested, so
    # that is the default, and the user corrects it if the book disagrees.
    for entry in entries:
        entry["page"] = entry["printed"]
    return "no labels, assuming printed number equals page index"


# ------------------------------------------------------------- the outline

def parse(path, pages, offset=None):
    """Read a contents page and return the outline document."""
    doc = pymupdf.open(path)
    entries = []
    width = doc[0].rect.width
    for number in pages:
        index = number - 1                      # the user counts from 1
        if not 0 <= index < doc.page_count:
            sys.exit(f"Page {number} is outside this PDF, which has "
                     f"{doc.page_count} pages.")
        page = doc[index]
        lines = page_lines(page)
        if not lines:
            sys.exit(f"Page {number} holds no text. This PDF is probably a scan."
                     "\nA scan cannot be indexed either, because the indexer"
                     " reads text, not pictures.")
        for column in columns_of(lines, page.rect.width):
            found = entries_from_rows(rows_of(column))
            # Levels come from the indent inside a column. A second column
            # starts hundreds of points to the right, and that shift says
            # nothing about depth, so it is removed here.
            if found:
                left = min(e["x"] for e in found)
                for entry in found:
                    entry["indent"] = round(entry["x"] - left, 1)
            entries.extend(found)

    entries = [e for e in entries if 1 <= e["printed"] <= doc.page_count]
    entries = assign_levels(entries, width=width)
    method = resolve_pages(doc, entries, offset)

    outline = {
        "version": SCHEMA_VERSION,
        "source": {"name": os.path.basename(path), "pages": doc.page_count,
                   "contents_pages": list(pages), "page_numbers": method},
        "entries": [{"level": e["level"], "title": e["title"],
                     "page": e["page"], "printed": e["printed"],
                     "x": e["x"], "y": e["y"]} for e in entries],
    }
    doc.close()
    return outline


def report(outline):
    """Print what was found, so a person can judge it before indexing."""
    source = outline["source"]
    print(f"{source['name']}  {source['pages']} pages")
    print(f"contents page(s): {source['contents_pages']}")
    print(f"page numbers: {source['page_numbers']}")
    print(f"entries: {len(outline['entries'])}\n")
    for entry in outline["entries"]:
        indent = "  " * (entry["level"] - 1)
        print(f"  {entry['page']:>4}  {indent}{entry['title'][:60]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("pdf")
    parser.add_argument("--pages", nargs="+", type=int, required=True,
                        metavar="N", help="contents page numbers, counting from 1")
    parser.add_argument("--offset", type=int, default=None,
                        help="add this to every printed number to get a page index")
    parser.add_argument("--out", default=None, help="write the outline to this file")
    args = parser.parse_args()
    # A Windows console is cp1252 and dies on the first ligature or dash.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    outline = parse(args.pdf, args.pages, offset=args.offset)
    report(outline)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(outline, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    main()
