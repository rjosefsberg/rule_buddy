#!/usr/bin/env python3
"""bookmarks.py - reading and tidying the outline of a PDF.

A book with good bookmarks indexes well, and most rulebooks ship with none or
with broken ones. The Bookmarks tab of the window does the editing; what is
here is the part with no interface in it.
"""

import re
import unicodedata

from . import core

MAX_LEVEL = 4


def flatten(title):
    """One line, no matter what the file holds.

    Bookmarks built from a printed contents page often keep the line break the
    typesetter used, so a title arrives with a line feed inside it. A row of a
    list shows one line, and everything after the break disappears.
    """
    title = unicodedata.normalize("NFKC", core.unpua(title or ""))
    return re.sub(r"\s+", " ", title).strip()


MAX_CONTENTS_PAGES = 50

# A range is written with a hyphen, an en dash, or an em dash, because a title
# pasted out of a PDF carries whatever dash the typesetter used.
RANGE = re.compile(r"^(\d+)\s*[-–—]\s*(\d+)$")


def parse_pages(text):
    """Read '4, 5, 8-11' into [4, 5, 8, 9, 10, 11].

    Commas separate the list. A range is two numbers with a dash between them.
    Raises ValueError with the message the status line should show.
    """
    numbers = []
    for part in (p.strip() for p in re.split(r"[,\s]+", text or "") if p.strip()):
        if part.isdigit():
            numbers.append(int(part))
            continue
        found = RANGE.match(part)
        if not found:
            raise ValueError(f"“{part}” is not a page or a range. "
                             "Use numbers such as: 4, 5, 8-11")
        first, last = int(found.group(1)), int(found.group(2))
        if last < first:
            raise ValueError(f"The range {part} runs backwards.")
        if last - first + 1 > MAX_CONTENTS_PAGES:
            raise ValueError(f"{part} is more than {MAX_CONTENTS_PAGES} pages. "
                             "A printed contents is shorter than that.")
        numbers += list(range(first, last + 1))

    # The parser reads the pages in order, and one page twice would double
    # every entry printed on it.
    return sorted(dict.fromkeys(numbers))
