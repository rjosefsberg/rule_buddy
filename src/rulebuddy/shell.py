#!/usr/bin/env python3
"""shell.py - the window, drawn by the system webview instead of by Tk.

Python keeps the whole back end. The page in ui/ draws the interface, and it
calls the methods of `Api` below. Nothing in core.py, indexer.py, charms.py or
contents.py knows this file exists.

    python -m rulebuddy.shell

The window needs WebView2 on Windows, which ships with Edge. A drive that has
to run anywhere carries the fixed version runtime and points at it with
WEBVIEW2_BROWSER_EXECUTABLE_FOLDER.
"""

import json
import os
import re
import sys

import webview

from . import charms, core

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


TAGS = {"b": ("<b>", "</b>"), "i": ("<i>", "</i>"), "x": ("<b><i>", "</i></b>")}


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))


def render(text, styles):
    """Turn a stored passage into HTML blocks, keeping the bold and italic runs.

    The styles column addresses the stored text by offset, so the runs are laid
    over the text here rather than in the page. Each line takes its own share of
    the runs: a tag opened on one line and closed on the next would cross the
    paragraph that goes between them, and the markup would not nest.

    The indexer marks a heading with '## '. Everything else is a paragraph.
    """
    runs = sorted(json.loads(styles or "[]"))
    blocks, at = [], 0
    for line in (text or "").split("\n"):
        stop = at + len(line)
        mine = [[max(start, at) - at, min(end, stop) - at, code]
                for start, end, code in runs if end > at and start < stop]
        at = stop + 1                       # the newline the split took out

        shift = 3 if line.startswith("## ") else 0
        if shift:
            line = line[shift:]
            mine = [[max(0, s - shift), e - shift, c] for s, e, c in mine
                    if e > shift]
        if not line.strip():
            continue
        marked = with_runs(line, mine)
        blocks.append(f"<h3>{marked}</h3>" if shift else f"<p>{marked}</p>")
    return "".join(blocks)


def with_runs(line, runs):
    """One line of text, with its style runs turned into tags."""
    out, at = [], 0
    for start, end, code in runs:
        if start < at or code not in TAGS or end <= start:
            continue
        open_tag, close_tag = TAGS[code]
        out.append(escape(line[at:start]))
        out.append(open_tag + escape(line[start:end]) + close_tag)
        at = end
    out.append(escape(line[at:]))
    return "".join(out)


class Api:
    """Everything the page can ask for. One method, one answer, no surprises."""

    def __init__(self, db_path):
        core.DB["path"] = db_path
        self.db = core.connect()
        self.window = None
        self.sources = {}
        self.terms = []

    # ------------------------------------------------------------- the book

    def state(self):
        """What the window needs to draw itself the first time."""
        return {
            "collection": core.collection_name(self.db),
            "path": core.DB["path"],
            "books": self.books(),
            "has_key": core.has_key(),
            "model": core.CONFIG["model"],
            "charms": charms.counted(self.db),
        }

    def books(self):
        rows = self.db.execute(
            "SELECT id, title, pages, source,"
            " (SELECT COUNT(*) FROM sections s WHERE s.book_id = b.id) AS chunks"
            " FROM books b ORDER BY b.id").fetchall()
        return [{"id": r["id"], "title": r["title"], "pages": r["pages"],
                 "source": r["source"], "chunks": r["chunks"],
                 "found": bool(r["source"] and os.path.exists(r["source"]))}
                for r in rows]

    # ------------------------------------------------------------ searching

    def search(self, terms):
        """Look the words up. The page draws whatever comes back."""
        if not (terms or "").strip():
            return {"results": [], "message": "Type something to look for."}
        try:
            found = core.retrieve(self.db, terms)
        except Exception as err:
            return {"results": [], "message": f"Search failed: {err}"}

        quoted = core.query_terms(terms) or ""
        self.terms = [t.strip('"') for t in re.findall(r'"([^"]+)"', quoted)
                      if " " not in t]
        self.sources = {}
        for src in found:
            for member in src.get("members", [src["id"]]):
                self.sources[member] = src

        results = sorted(found, key=lambda s: ((s.get("book") or ""),
                                               s["page_start"]))
        best = min(found, key=lambda s: s.get("rank", 0))["id"] if found else None
        return {
            "results": [self.brief(s) for s in results],
            "best": best,
            "message": (f"{len(found)} sections for “{terms}”." if found
                        else f"Nothing matched “{terms}”."),
        }

    def brief(self, src):
        """One row of the results list."""
        pages = (str(src["page_start"]) if src["page_start"] == src["page_end"]
                 else f"{src['page_start']}–{src['page_end']}")
        return {
            "id": src["id"],
            "book": src.get("book") or "",
            "section": src["path"].split(" > ")[-1],
            "pages": pages,
            "cited": bool(src.get("cited")),
            "chunks": len(src.get("members", [])),
        }

    def excerpt(self, section_id):
        """The passage itself, as HTML, with the search words marked."""
        src = self.sources.get(int(section_id))
        if not src:
            return {"html": "", "header": ""}
        body = render(src["text"], src.get("styles"))
        pages = (f"p.{src['page_start']}" if src["page_start"] == src["page_end"]
                 else f"pp.{src['page_start']}–{src['page_end']}")
        return {
            "html": body,
            "book": src.get("book") or "",
            "pages": pages,
            "path": src["path"],
            "terms": [t for t in self.terms if len(t) > 2],
            "source": src.get("source") or "",
            "page": src["page_start"],
        }

    # -------------------------------------------------------------- the PDF

    def open_pdf(self, path, page):
        from .app import open_pdf_at        # the reader chain, unchanged
        if not path or not os.path.exists(path):
            return {"ok": False, "message": "That PDF is not where the index says."}
        how = open_pdf_at(path, int(page))
        name = os.path.basename(path)
        return {"ok": True,
                "message": (f"Opened {name} at page {page} with {how}." if how
                            else f"Opened {name}. Go to page {page}.")}

    # ------------------------------------------------------------ the library

    def charm_filters(self):
        if not charms.counted(self.db):
            return {"built": False}
        top = self.db.execute("SELECT MAX(essence) FROM charms").fetchone()[0]
        return {
            "built": True,
            "total": charms.counted(self.db),
            "books": charms.choices(self.db, "book"),
            "trees": charms.choices(self.db, "tree"),
            "types": charms.choices(self.db, "type"),
            "keywords": charms.keywords_in(self.db),
            "essence": list(range(1, (top or 5) + 1)),
        }

    def charm_search(self, filters):
        filters = filters or {}
        rows = charms.search(
            self.db,
            terms=filters.get("terms", ""),
            tree=filters.get("tree", ""),
            type_=filters.get("type", ""),
            keyword=filters.get("keyword", ""),
            book=filters.get("book", ""),
            essence=int(filters.get("essence") or 0))
        return [dict(r) for r in rows]

    def build_charms(self):
        """Read the whole collection again. The page shows a wait."""
        count = charms.build(core.connect())
        return {"count": count}


def start(db_path=None):
    """Open the window. Everything else happens in the page."""
    core.load_config()
    path = db_path or core.DB["path"]
    if not os.path.isabs(path):
        path = os.path.join(core.app_dir(), path)
    if not os.path.exists(path):
        sys.exit(f"No index at {path}")

    api = Api(path)
    api.window = webview.create_window(
        "Rule Buddy", os.path.join(UI, "index.html"),
        js_api=api, width=1360, height=880, min_size=(900, 600))
    webview.start()


if __name__ == "__main__":
    start(sys.argv[1] if len(sys.argv) > 1 else None)
