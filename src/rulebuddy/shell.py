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

import base64
import json
import logging
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

import webview

from . import bookmarks, charms, contents, core

try:
    from . import indexer
    import pymupdf
except ImportError:                 # searching works; indexing will not
    indexer = pymupdf = None

def ui_dir():
    """Where the page lives.

    Frozen, the files are unpacked beside the bundle, not next to this module:
    a module inside the archive has no folder on disk to look in.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "rulebuddy", "ui")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


UI = ui_dir()
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


def pdf_viewers():
    """Readers that take a page on the command line, best first.

    Each item is (name, list of places to look, arguments before the file).
    The page number is a PDF sequence number, which is what these readers want.
    """
    if sys.platform == "win32":
        program = os.environ.get("ProgramFiles", r"C:\Program Files")
        x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        return [
            ("SumatraPDF",
             [os.path.join(local, r"SumatraPDF\SumatraPDF.exe"),
              os.path.join(program, r"SumatraPDF\SumatraPDF.exe")],
             lambda page: ["-reuse-instance", "-page", str(page)]),
            ("Acrobat",
             [os.path.join(x86, r"Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
              os.path.join(program, r"Adobe\Acrobat DC\Acrobat\Acrobat.exe")],
             lambda page: ["/A", f"page={page}"]),
        ]
    if sys.platform == "darwin":
        return []                       # Preview takes no page from the shell
    return [
        ("Evince", ["evince"], lambda page: [f"--page-index={page}"]),
        ("Okular", ["okular"], lambda page: ["-p", str(page)]),
        ("Zathura", ["zathura"], lambda page: ["-P", str(page)]),
    ]


def open_pdf_at(path, page):
    """Open a PDF at a page. Return the reader used, or None if the page is lost.

    A PDF reader is not required to take a page number, and the system default
    on Windows never does. So try the readers that do, then a browser, which
    honours the #page fragment. The last try opens the file at page one.
    """
    for name, places, arguments in pdf_viewers():
        for place in places:
            exe = place if os.path.isfile(place) else shutil.which(place)
            if not exe:
                continue
            try:
                subprocess.Popen([exe] + arguments(page) + [path])
                return name
            except OSError:
                break                   # this reader is there but will not run

    url = urllib.parse.urljoin("file:", urllib.request.pathname2url(
        os.path.abspath(path))) + f"#page={page}"
    try:
        if webbrowser.open(url):
            return "your browser"
    except webbrowser.Error:
        pass

    try:
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open",
                              path])
    except OSError:
        pass
    return None


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
        self.events = queue.Queue()

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

    def cover(self, book_id):
        """Page one of a book as a data URI, or nothing.

        The bytes are asked for one book at a time. Sending every cover with
        every book list would carry a megabyte of PNG through the bridge each
        time anything on the shelf changed.
        """
        row = self.db.execute("SELECT cover FROM books WHERE id=?",
                              (book_id,)).fetchone()
        if row is None or not row["cover"]:
            return ""
        return "data:image/png;base64," + base64.b64encode(row["cover"]).decode()

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
            # An answer reads wider than a search list does. The chunks of one
            # section join into a single source, so 60 rows is far fewer than
            # 60 passages, and the prompt budget still decides what fits.
            found = core.retrieve(self.db, lookup, limit=60)
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
        """Hand an event to the page. Called from worker threads.

        It only queues. Python must never call into the window from a worker:
        evaluate_js and run_js both go through a synchronous cross-thread
        Invoke in the WebView2 backend, and pythonnet holds the GIL across it.
        While the window sits in a modal loop — which is what Windows runs for
        the whole time a title bar is dragged — the UI thread cannot answer,
        the worker cannot give the GIL back, and the entire process locks up.

        The page pulls instead, through poll().
        """
        self.events.put({"kind": kind, **detail})

    def poll(self):
        """Everything that happened since the page last asked."""
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def clear(self):
        self.history = []
        self.sources = {}
        return {"ok": True}

    # -------------------------------------------------------------- the PDF

    def open_pdf(self, path, page):
        if not path or not os.path.exists(path):
            return {"ok": False, "message": "That PDF is not where the index says."}
        how = open_pdf_at(path, int(page))
        name = os.path.basename(path)
        return {"ok": True,
                "message": (f"Opened {name} at page {page} with {how}." if how
                            else f"Opened {name}. Go to page {page}.")}

    # ----------------------------------------------------------------- the key

    def check_key(self, key, persist=False, force=False):
        """Take a key, try it against the API, and keep it if it works.

        The check costs one small request and turns a typo into a message here
        rather than a failure at the first question.
        """
        key = (key or "").strip()
        if not key:
            return {"ok": False, "message": "No key given."}
        # The shape check is a courtesy, not a rule: a key of another shape is
        # still worth trying if the user says so.
        if not force and not core.looks_like_key(key):
            return {"ok": False, "shape": True,
                    "message": "Anthropic keys start with sk-ant- and are long."}
        ok, detail = core.verify_key(key)
        if not ok:
            return {"ok": False, "message": detail}
        saved, where = core.set_key(key, persist=bool(persist))
        note = "" if saved or not persist else f" It was not saved: {where}"
        return {"ok": True, "has_key": True,
                "message": f"Key accepted. Written answers are on.{note}"}

    def drop_key(self, forget=True):
        """Stop using the key, and take it out of config.json when asked."""
        core.clear_key(forget=bool(forget))
        return {"ok": True, "has_key": core.has_key(),
                "message": "Key removed." if forget
                           else "Key dropped for this session."}

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

    # ---------------------------------------------------------- book actions

    def rename_collection(self, name):
        name = (name or "").strip()
        if not name:
            return {"ok": False, "message": "A collection needs a name."}
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('name',?)", (name,))
        self.db.commit()
        return {"ok": True, "collection": core.collection_name(self.db)}

    def rename_book(self, book_id, name):
        name = (name or "").strip()
        if not name:
            return {"ok": False, "message": "A book needs a name."}
        self.db.execute("UPDATE books SET title=? WHERE id=?", (name, book_id))
        self.db.commit()
        return {"ok": True, "books": self.books()}

    def find_pdf(self, book_id):
        """Point a book at its PDF again, after the file moved or was renamed.

        The index holds the path the book was built from. Nothing else needs
        that file, so a book with a stale path still searches: only opening the
        page in a reader stops working.
        """
        row = self.db.execute("SELECT title, source, pages FROM books WHERE id=?",
                              (book_id,)).fetchone()
        if row is None:
            return {"ok": False}
        path = self.pick_pdf()
        if not path:
            return {"ok": False}

        # A different book at the right path helps nobody, so count the pages.
        # It is a warning, not a refusal: a reprint can differ by a page.
        note = ""
        if pymupdf is not None:
            try:
                doc = pymupdf.open(path)
                pages = doc.page_count
                doc.close()
                if row["pages"] and pages != row["pages"]:
                    note = (f" That PDF has {pages} pages and the index was built"
                            f" from {row['pages']}. Check it is the same book.")
            except Exception as err:
                return {"ok": False, "message": f"Cannot read that PDF: {err}"}

        self.db.execute("UPDATE books SET source=? WHERE id=?", (path, book_id))
        self.db.commit()
        return {"ok": True, "books": self.books(),
                "message": f"{row['title']} now points at "
                           f"{os.path.basename(path)}.{note}"}

    def remove_book(self, book_id):
        """Take one book out. Its sections stop being searchable."""
        if len(self.books()) <= 1:
            return {"ok": False,
                    "message": "That is the only book. Delete the collection instead."}
        indexer.remove_book(self.db, int(book_id))
        return {"ok": True, "books": self.books(),
                "message": "The book is out. Its PDF was not deleted."}

    def add_book(self, path=""):
        """Index another PDF into the collection that is open."""
        path = path or self.pick_pdf()
        if not path:
            return {"started": False}
        check = indexer.check_pdf(path, max_bytes=MAX_BYTES)
        if not check["ok"]:
            return {"started": False, "message": check["reason"]}
        threading.Thread(target=self.add_work, args=(path,), daemon=True).start()
        return {"started": True, "name": os.path.basename(path)}

    def add_work(self, path):
        target = core.DB["path"]
        self.db.close()                 # Windows will not let us write it open
        self.db = None
        try:
            indexer.add_book(path, target, progress=lambda stage, done, total:
                             self.tell("indexing", {"stage": stage, "done": done,
                                                    "total": total}))
        except Exception as err:
            self.db = core.connect()
            self.tell("index_failed", {"message": f"{type(err).__name__}: {err}"})
            return
        self.db = core.connect()
        self.tell("indexed", {"name": os.path.basename(path),
                              "collection": core.collection_name(self.db),
                              "books": self.books()})

    def reimport_book(self, book_id):
        """Rebuild one book from its PDF, in place.

        add_book writes the new rows before it drops the old ones, in one
        transaction, so a failure leaves the collection as it was.
        """
        row = self.db.execute("SELECT title, source FROM books WHERE id=?",
                              (book_id,)).fetchone()
        if row is None:
            return {"started": False}
        path = row["source"]
        if not path or not os.path.exists(path):
            return {"started": False,
                    "message": "That PDF is not where the index says. "
                               "Find it first."}
        threading.Thread(target=self.add_work, args=(path,), daemon=True).start()
        return {"started": True, "name": os.path.basename(path)}

    def delete_collection(self, path):
        """Delete a whole collection file. The PDFs behind it are not touched."""
        if os.path.normcase(os.path.abspath(path)) == \
                os.path.normcase(os.path.abspath(core.DB["path"])):
            return {"ok": False,
                    "message": "That collection is open. Open another one first."}
        try:
            os.remove(path)
        except OSError as err:
            return {"ok": False, "message": str(err)}
        return {"ok": True, "message": f"Deleted {os.path.basename(path)}."}

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
            out.append({"path": full, "name": self.name_of(full),
                        "open": os.path.normcase(full) == here})
        return out

    def name_of(self, path):
        """What a collection calls itself, else its file name.

        The name is a row in the file, so a renamed collection has to be read
        rather than guessed from the path it happens to sit at.
        """
        if os.path.normcase(path) == os.path.normcase(
                os.path.abspath(core.DB["path"])):
            return core.collection_name(self.db)
        try:
            other = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            row = other.execute(
                "SELECT value FROM meta WHERE key='name'").fetchone()
            other.close()
            if row and row[0]:
                return row[0]
        except sqlite3.Error:
            pass
        return os.path.splitext(os.path.basename(path))[0]

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


def centred(width, height):
    """Where to put the window, as arguments for create_window.

    The work area, not the whole screen, because the task bar takes a strip of
    it. The window is told its size in the same units the work area is measured
    in, so display scaling needs no arithmetic here: both are already scaled.
    Anything unexpected returns nothing and lets pywebview place the window.
    """
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        area = wintypes.RECT()
        if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
            return {}                   # SPI_GETWORKAREA
        free_w = area.right - area.left
        free_h = area.bottom - area.top
        if free_w < width or free_h < height:
            return {}                   # it will not fit, so do not place it
        return {"x": area.left + (free_w - width) // 2,
                "y": area.top + (free_h - height) // 2}
    except Exception:
        return {}


def start(db_path=None):
    """Open the window. Everything else happens in the page."""
    # The window must own the main thread. WebView2 wants its message loop
    # there, in a single threaded apartment. Started anywhere else it paints
    # once and then stops answering, which reads as a frozen window rather than
    # as a mistake. PyCharm does this when a run configuration has "Run with
    # Python Console" ticked: the script runs inside the console's thread.
    if threading.current_thread() is not threading.main_thread():
        sys.exit("Rule Buddy has to start on the main thread.\n"
                 "In PyCharm, turn off 'Run with Python Console' in the run "
                 "configuration, or start it from a terminal: python run.py")
    quieten()
    core.load_config()
    path = db_path or core.DB["path"]
    if not os.path.isabs(path):
        path = os.path.join(core.app_dir(), path)
    if not os.path.exists(path):
        sys.exit(f"No index at {path}")

    width, height = 1360, 880
    api = Api(path)
    api.window = webview.create_window(
        "Rule Buddy", os.path.join(UI, "index.html"),
        # The floor is what the tab bar needs with the shelf open: 792 pixels
        # of buttons and 265 of shelf, measured. Below that a button would be
        # pushed out of reach, and the page never scrolls sideways.
        js_api=api, width=width, height=height, min_size=(1060, 640),
        **centred(width, height))
    webview.start()


if __name__ == "__main__":
    start(sys.argv[1] if len(sys.argv) > 1 else None)
