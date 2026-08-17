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
import logging
import os
import re
import sys
import threading

import webview

from . import bookmarks, charms, contents, core

try:
    from . import indexer
    import pymupdf
except ImportError:                 # searching works; indexing will not
    indexer = pymupdf = None

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
MAX_BYTES = 250 * 1_000_000


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


def safe_filename(name):
    """A collection name becomes a file name, so take out what a path forbids."""
    cleaned = re.sub(r'[<>:"/\\|?*]', " ", name).strip(" .")
    return " ".join(cleaned.split())[:80] or "Collection"


class Api:
    """Everything the page can ask for. One method, one answer, no surprises."""

    def __init__(self, db_path):
        core.DB["path"] = db_path
        self.db = core.connect()
        self.window = None
        self.sources = {}
        self.terms = []
        self.history = []

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

    # ---------------------------------------------------------------- asking

    def outline(self):
        rows = self.db.execute("SELECT title, level, page_start FROM sections"
                               " WHERE part=0 AND level<=2 ORDER BY id").fetchall()
        return "\n".join(f"{'  ' * (r['level'] - 1)}{r['title']} (p.{r['page_start']})"
                         for r in rows[:250])

    def ask(self, question, follow=False):
        """Start an answer. The page hears about it through tell()."""
        question = (question or "").strip()
        if not question:
            return {"started": False}
        if not core.has_key():
            return {"started": False,
                    "message": "No API key. Add one to get written answers."}
        threading.Thread(target=self.ask_work, args=(question, bool(follow)),
                         daemon=True).start()
        return {"started": True}

    def ask_work(self, question, follow):
        """Retrieval, then the model. Off the main thread, as it always was."""
        lookup = question
        if follow and len(question.split()) < 6:
            said = [t["content"] for t in self.history if t["role"] == "user"]
            if said:
                lookup = said[-1] + " " + question
        self.tell("searching", {"question": question})
        try:
            found = core.retrieve(self.db, lookup)
        except Exception as err:
            self.tell("failed", {"message": f"Search failed: {err}"})
            return

        # A follow-up answers over everything the conversation has gathered.
        if follow and self.sources:
            pool = {s["id"]: s for s in self.sources.values()}
            pool.update({s["id"]: s for s in found})
            found = list(pool.values())

        self.sources = {}
        for src in found:
            for member in src.get("members", [src["id"]]):
                self.sources[member] = src
        rows = sorted(found, key=lambda s: ((s.get("book") or ""), s["page_start"]))
        self.tell("sources", {"results": [self.brief(s) for s in rows]})

        self.tell("thinking", {})
        answer = core.ask_model(question, found, self.outline(), self.history)
        if answer.get("error"):
            self.tell("failed", {"message": answer["text"]})
            return
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer["text"]})
        self.tell("answer", {"question": question, "text": answer["text"]})

    def tell(self, kind, detail):
        """Push an event into the page. Safe from a worker thread."""
        if not self.window:
            return
        payload = json.dumps({"kind": kind, **detail})
        self.window.evaluate_js(f"window.onEvent({payload})")

    def clear(self):
        self.history = []
        self.sources = {}
        return {"ok": True}

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

    # ------------------------------------------------------- native dialogs

    def pick_pdf(self):
        """The system file dialog. This is why the window is a webview and not
        a browser tab: a page can never learn a path on disk."""
        picked = self.window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("PDF rulebook (*.pdf)",))
        return picked[0] if picked else ""

    def pick_folder(self):
        picked = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return picked[0] if picked else ""

    # ------------------------------------------------------------- bookmarks

    def read_bookmarks(self, path):
        """What the PDF already carries, plus what its pages are called."""
        if not path or not os.path.exists(path):
            return {"ok": False, "message": "No file there."}
        try:
            doc = pymupdf.open(path)
        except Exception as err:
            return {"ok": False, "message": f"{type(err).__name__}: {err}"}
        labels = {}
        for index in range(doc.page_count):
            try:
                label = doc[index].get_label()
            except Exception:
                label = ""
            if label:
                labels[str(index)] = label
        entries = [{"level": level, "title": bookmarks.flatten(title),
                    "page": page - 1}
                   for level, title, page in doc.get_toc()]
        pages = doc.page_count
        doc.close()
        return {"ok": True, "entries": entries, "pages": pages, "labels": labels,
                "name": os.path.basename(path)}

    def read_contents(self, path, pages):
        """Build an outline from the printed contents page."""
        try:
            numbers = bookmarks.parse_pages(pages)
        except ValueError as err:
            return {"ok": False, "message": str(err)}
        if not numbers:
            return {"ok": False, "message": "Give a page, such as: 4, 5, 8-11"}
        try:
            outline = contents.parse(path, numbers)
        except SystemExit as err:
            return {"ok": False, "message": str(err)}
        except Exception as err:
            return {"ok": False, "message": f"{type(err).__name__}: {err}"}
        return {"ok": True,
                "entries": [{"level": e["level"], "title": e["title"],
                             "page": e["page"]} for e in outline["entries"]],
                "pages": outline["source"]["pages"],
                "message": f"{len(outline['entries'])} entries. Page numbers: "
                           f"{outline['source']['page_numbers']}"}

    def save_bookmarks(self, path, entries):
        """Write the outline into the PDF. The file is changed in place."""
        if not path or not entries:
            return {"ok": False, "message": "Nothing to save."}
        toc = [[e["level"], bookmarks.flatten(e["title"]), e["page"] + 1]
               for e in entries]
        try:
            doc = pymupdf.open(path)
            doc.set_toc(toc)
            doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
            doc.close()
        except Exception as err:
            return {"ok": False, "message": f"{type(err).__name__}: {err}"}
        return {"ok": True,
                "message": f"Wrote {len(entries)} bookmarks into "
                           f"{os.path.basename(path)}. The file is closed."}

    # -------------------------------------------------------- folder import

    def check_folder(self, folder, deep=False):
        """Test every PDF in a folder before any indexing starts."""
        if not folder or not os.path.isdir(folder):
            return {"ok": False, "message": "That is not a folder."}
        paths = []
        if deep:
            for here, _, names in os.walk(folder):
                paths += [os.path.join(here, n) for n in names
                          if n.lower().endswith(".pdf")]
        else:
            paths = [os.path.join(folder, n) for n in os.listdir(folder)
                     if n.lower().endswith(".pdf")]
        paths.sort(key=lambda p: os.path.basename(p).lower())

        results = []
        for i, path in enumerate(paths, start=1):
            self.tell("checked", {"done": i, "total": len(paths),
                                  "name": os.path.basename(path)})
            try:
                results.append(indexer.check_pdf(path, max_bytes=MAX_BYTES))
            except Exception as err:
                results.append({"path": path, "name": os.path.basename(path),
                                "ok": False, "pages": 0,
                                "reason": f"{type(err).__name__}: {err}"})
        return {"ok": True, "results": results,
                "name": os.path.basename(os.path.abspath(folder))}

    def index_folder(self, plan, name):
        """Build one collection from several PDFs, off the main thread."""
        if not plan or not name.strip():
            return {"started": False, "message": "Name the collection first."}
        shelf = os.path.join(core.app_dir(), core.CONFIG["books_dir"])
        try:
            os.makedirs(shelf, exist_ok=True)
        except OSError as err:
            return {"started": False, "message": str(err)}
        target = os.path.join(shelf, safe_filename(name) + ".db")
        threading.Thread(target=self.index_work,
                         args=([(p, core.title_from_path(p)) for p in plan],
                               name, target), daemon=True).start()
        return {"started": True, "target": os.path.basename(target)}

    def index_work(self, plan, name, target):
        # Build beside the old file and swap at the end. A collection takes
        # minutes, and a failure must not leave the shelf short a book.
        scratch = target + ".building"
        try:
            indexer.rebuild_collection(
                scratch, plan, name=name,
                progress=lambda stage, done, total: self.tell(
                    "indexing", {"stage": stage, "done": done, "total": total}))
        except Exception as err:
            try:
                os.remove(scratch)
            except OSError:
                pass
            self.tell("index_failed", {"message": f"{type(err).__name__}: {err}"})
            return
        try:
            os.replace(scratch, target)
        except OSError as err:
            self.tell("index_failed", {"message": str(err)})
            return
        self.open_index(target)
        self.tell("indexed", {"name": os.path.basename(target),
                              "collection": core.collection_name(self.db),
                              "books": self.books()})

    def open_index(self, path):
        """Point the window at another collection."""
        if not os.path.exists(path):
            return {"ok": False, "message": f"No index at {path}"}
        core.DB["path"] = path
        old, self.db = self.db, core.connect()
        if old is not None:
            old.close()             # or Windows keeps a lock on the old file
        self.sources, self.history = {}, []
        return {"ok": True, **self.state()}

    def shelf(self):
        """Every collection on the shelf, by name."""
        folder = os.path.join(core.app_dir(), core.CONFIG["books_dir"])
        try:
            names = sorted(n for n in os.listdir(folder)
                           if n.lower().endswith(".db"))
        except OSError:
            return []
        here = os.path.normcase(os.path.abspath(core.DB["path"]))
        out = []
        for name in names:
            full = os.path.abspath(os.path.join(folder, name))
            out.append({"path": full, "name": os.path.splitext(name)[0],
                        "open": os.path.normcase(full) == here})
        return out

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


def quieten():
    """Stop pywebview printing COM faults it has already handled.

    It probes WebView2 for interfaces from a newer SDK than the runtime
    carries. Each miss is caught and logged as a stack trace, which says
    nothing to a user and nothing to us.
    """
    for name in ("pywebview", "pywebview.platforms.winforms"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def start(db_path=None):
    """Open the window. Everything else happens in the page."""
    quieten()
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
