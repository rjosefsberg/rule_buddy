#!/usr/bin/env python3
"""rulebook_app.py - a desktop window over the rulebook index.

    python rulebook.py index yourbook.pdf
    copy config.example.json config.json   # then put your key in it
    python rulebook_app.py

Uses Tk, which ships with Python, so the window is a real native window with
native menus. Retrieval and the API call come from rulebook_core.py, which must
sit in the same folder. On Debian or Ubuntu, install Tk with:
    sudo apt install python3-tk
"""

import argparse
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

try:
    import rulebook_core as core
except ImportError:
    sys.exit("rulebook_core.py must sit in the same folder as this file.")

try:
    import rulebook as indexer
except (ImportError, SystemExit):
    indexer = None      # searching still works; importing a new book will not

CITE = re.compile(r"\[#(\d+)[^\]]*?(\d+)\]")
MAX_IMPORT_BYTES = 100 * 1_000_000
INTRO = ("Answers cite the section and page. Click a citation to read the excerpt "
         "it came from, so you can check the book yourself.\n\n"
         "Questions work best in the book's own words. Try \"cover ranged attack\" "
         "rather than \"can I shoot someone hiding\".\n\n"
         "New question starts a fresh thread. Ask more keeps the current one, and "
         "answers over everything it has found so far.\n")


def luminance(widget, color):
    r, g, b = widget.winfo_rgb(color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 65535


class App(tk.Tk):
    def __init__(self, db_path, model):
        super().__init__()
        core.DB["path"] = db_path
        core.CONFIG["model"] = model
        self.db = core.connect()
        self.outline = self.load_outline()
        self.history = []
        self.pending = ""
        self.pending_import = None
        self.sources = {}
        self.terms = []
        self.inbox = queue.Queue()
        self.busy = False

        self.title(self.book_name())
        self.geometry("1040x720")
        self.minsize(620, 420)
        self.set_theme()
        self.build_menu()
        self.build_layout()
        self.show_intro()
        self.after(80, self.drain)
        self.entry.focus_set()

    # ------------------------------------------------------------- appearance

    def set_theme(self):
        style = ttk.Style(self)
        if sys.platform == "win32" and "vista" in style.theme_names():
            style.theme_use("vista")
        base = style.lookup("TFrame", "background") or "#ffffff"
        self.dark = luminance(self, base) < 0.5
        self.colors = {
            "page": "#1E1E1E" if self.dark else "#FFFFFF",
            "ink": "#E4E4E4" if self.dark else "#1A1A1A",
            "muted": "#8C8C8C" if self.dark else "#6B6B6B",
            "accent": "#7AA7DC" if self.dark else "#1A4E8A",
            "mark": "#5A4A16" if self.dark else "#FBEA9E",
            "warn": "#D08A6A" if self.dark else "#9A3D16",
            "quote": "#2A2A2A" if self.dark else "#F4F5F6",
            "rule": "#3A3A3A" if self.dark else "#D2D6DA",
        }
        self.base_size = tkfont.nametofont("TkTextFont").actual("size") or 13
        self.scale = 0

    def fonts(self):
        size = self.base_size + self.scale
        family = tkfont.nametofont("TkTextFont").actual("family")
        mono = "Menlo" if sys.platform == "darwin" else (
            "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono")
        return {
            "body": tkfont.Font(family=family, size=size),
            "bold": tkfont.Font(family=family, size=size, weight="bold"),
            "question": tkfont.Font(family=family, size=size + 1, weight="bold"),
            "small": tkfont.Font(family=mono, size=max(9, size - 3)),
            "italic": tkfont.Font(family=family, size=size, slant="italic"),
            "bolditalic": tkfont.Font(family=family, size=size, weight="bold",
                                      slant="italic"),
        }

    def apply_fonts(self):
        f = self.fonts()
        self.transcript.configure(font=f["body"])
        self.excerpt.configure(font=f["body"])
        self.entry.configure(font=f["body"])
        for name, spec in (("you", "question"), ("answer", "body"),
                           ("cite", "small"), ("muted", "small"), ("warn", "body"),
                           ("label", "small"), ("rule", "small"), ("bullet", "body"),
                           ("heading", "bold"), ("strong", "bold"), ("emph", "italic")):
            self.transcript.tag_configure(name, font=f[spec])
        self.excerpt.tag_configure("head", font=f["small"])
        self.excerpt.tag_configure("subhead", font=f["bold"])
        self.excerpt.tag_configure("strong", font=f["bold"])
        self.excerpt.tag_configure("emph", font=f["italic"])
        self.excerpt.tag_configure("strongemph", font=f["bolditalic"])

    # ---------------------------------------------------------------- widgets

    def build_menu(self):
        menu = tk.Menu(self)
        accel = "Command" if sys.platform == "darwin" else "Control"
        key = "Cmd" if sys.platform == "darwin" else "Ctrl"

        m_file = tk.Menu(menu, tearoff=0)
        m_file.add_command(label="Import rulebook…", accelerator=f"{key}+I",
                           command=self.import_rulebook)
        m_file.add_command(label="Open index…", accelerator=f"{key}+O", command=self.open_index)
        m_file.add_separator()
        m_file.add_command(label="Clear conversation", accelerator=f"{key}+K", command=self.clear)
        menu.add_cascade(label="File", menu=m_file)

        m_edit = tk.Menu(menu, tearoff=0)
        m_edit.add_command(label="Copy", accelerator=f"{key}+C",
                           command=lambda: self.focus_get().event_generate("<<Copy>>"))
        m_edit.add_command(label="Copy last answer", command=self.copy_answer)
        menu.add_cascade(label="Edit", menu=m_edit)

        m_view = tk.Menu(menu, tearoff=0)
        self.show_sources = tk.BooleanVar(value=True)
        m_view.add_checkbutton(label="Sections pane", variable=self.show_sources,
                               command=self.toggle_sources)
        m_view.add_separator()
        m_view.add_command(label="Bigger text", accelerator=f"{key}++",
                           command=lambda: self.zoom(1))
        m_view.add_command(label="Smaller text", accelerator=f"{key}+-",
                           command=lambda: self.zoom(-1))
        menu.add_cascade(label="View", menu=m_view)
        self.config(menu=menu)

        self.bind_all(f"<{accel}-i>", lambda e: self.import_rulebook())
        self.bind_all(f"<{accel}-o>", lambda e: self.open_index())
        self.bind_all(f"<{accel}-k>", lambda e: self.clear())
        self.bind_all(f"<{accel}-plus>", lambda e: self.zoom(1))
        self.bind_all(f"<{accel}-equal>", lambda e: self.zoom(1))
        self.bind_all(f"<{accel}-minus>", lambda e: self.zoom(-1))

    def build_layout(self):
        self.status = ttk.Label(self, anchor="w", padding=(10, 3))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self.panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.panes.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(self.panes)
        self.panes.add(left, weight=3)

        wrap = ttk.Frame(left)
        wrap.pack(fill=tk.BOTH, expand=True, padx=(2, 0), pady=(2, 0))
        self.transcript = tk.Text(wrap, wrap="word", padx=20, pady=16, relief="flat",
                                  spacing1=2, spacing3=6, cursor="arrow",
                                  bg=self.colors["page"], fg=self.colors["ink"],
                                  insertbackground=self.colors["ink"],
                                  highlightthickness=0, state="disabled")
        bar = ttk.Scrollbar(wrap, command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.transcript.pack(fill=tk.BOTH, expand=True)
        self.transcript.tag_configure("label", foreground=self.colors["muted"],
                                      spacing1=18, spacing3=2)
        self.transcript.tag_configure("you", foreground=self.colors["ink"],
                                      spacing3=10, lmargin1=0, lmargin2=0)
        self.transcript.tag_configure("answer", spacing2=3, spacing3=10,
                                      lmargin1=0, lmargin2=0)
        self.transcript.tag_configure("heading", foreground=self.colors["accent"],
                                      spacing1=8, spacing3=4)
        self.transcript.tag_configure("cite", foreground=self.colors["accent"])
        self.transcript.tag_configure("muted", foreground=self.colors["muted"])
        self.transcript.tag_configure("warn", foreground=self.colors["warn"])
        self.transcript.tag_configure("bullet", lmargin1=20, lmargin2=36,
                                      spacing2=3, spacing3=6)
        self.transcript.tag_configure("strong", foreground=self.colors["ink"])
        self.transcript.tag_configure("emph")
        self.transcript.tag_configure("rule", foreground=self.colors["rule"],
                                      spacing1=12, spacing3=12, justify="center")

        composer = ttk.Frame(left, padding=(10, 6, 10, 10))
        composer.pack(fill=tk.X)

        # The box carries the border so it reads as one field, not a sunken widget.
        field = tk.Frame(composer, bg=self.colors["rule"], padx=1, pady=1)
        field.pack(fill=tk.X)
        self.entry = tk.Text(field, height=3, wrap="word", relief="flat", bd=0,
                             padx=10, pady=8, bg=self.colors["page"],
                             fg=self.colors["ink"], insertbackground=self.colors["ink"],
                             highlightthickness=0)
        self.entry.pack(fill=tk.BOTH, expand=True)
        self.entry.bind("<Return>", self.on_return)
        self.entry.bind("<Shift-Return>", lambda e: None)
        self.entry.bind("<FocusIn>", lambda e: field.configure(bg=self.colors["accent"]))
        self.entry.bind("<FocusOut>", lambda e: field.configure(bg=self.colors["rule"]))

        row = ttk.Frame(composer)
        row.pack(fill=tk.X, pady=(8, 0))
        # Both buttons keep the stock style and one width, so the theme gives
        # them identical padding and their labels share a baseline.
        self.more = ttk.Button(row, text="Ask more", width=14,
                               command=lambda: self.submit(follow=True))
        self.more.pack(side=tk.RIGHT)
        self.send = ttk.Button(row, text="New question", width=14,
                               command=lambda: self.submit(follow=False))
        self.send.pack(side=tk.RIGHT, padx=(0, 8))
        self.hint = ttk.Label(row, text="Enter asks  ·  Shift+Enter adds a line",
                              foreground=self.colors["muted"], anchor="w")
        self.hint.pack(side=tk.LEFT, fill=tk.Y)

        right = ttk.Frame(self.panes)
        self.panes.add(right, weight=2)
        self.right = right
        head = ttk.Label(right, text="Sections found", padding=(8, 6))
        head.pack(fill=tk.X)
        self.tree = ttk.Treeview(right, columns=("page",), show="tree headings", height=8)
        self.tree.heading("#0", text="Section")
        self.tree.heading("page", text="Page")
        self.tree.column("#0", width=240, stretch=True)
        self.tree.column("page", width=64, stretch=False, anchor="e")
        self.tree.pack(fill=tk.BOTH, expand=False, padx=6)
        self.tree.bind("<<TreeviewSelect>>", self.on_pick)
        self.excerpt = tk.Text(right, wrap="word", relief="flat", padx=12, pady=10,
                               bg=self.colors["quote"], fg=self.colors["ink"],
                               highlightthickness=0, state="disabled", height=10)
        self.excerpt.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.excerpt.tag_configure("head", foreground=self.colors["muted"], spacing3=8)
        self.excerpt.tag_configure("subhead", foreground=self.colors["accent"], spacing3=4)
        self.excerpt.tag_configure("mark", background=self.colors["mark"])

        self.apply_fonts()
        self.set_status()
        self.after(60, lambda: self.panes.sashpos(0, int(self.winfo_width() * 0.6)))

    # ----------------------------------------------------------------- helpers

    def book_name(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
        name = os.path.basename(row["value"]) if row else core.DB["path"]
        return f"{name} — Rulebook"

    def load_outline(self):
        rows = self.db.execute("SELECT title, level, page_start FROM sections"
                               " WHERE part=0 AND level<=2 ORDER BY id").fetchall()
        return "\n".join(f"{'  ' * (r['level'] - 1)}{r['title']} (p.{r['page_start']})"
                         for r in rows[:250])

    def set_status(self, message=None):
        if message is None:
            pages = self.db.execute("SELECT value FROM meta WHERE key='pages'").fetchone()
            count = self.db.execute("SELECT COUNT(*) c FROM sections WHERE part=0").fetchone()
            model = core.CONFIG["model"] if core.CONFIG["key"] else "search only, no API key"
            message = f"{pages['value'] if pages else '?'} pages · {count['c']} sections · {model}"
        self.status.configure(text=message)

    def write(self, text, *tags):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text, tags)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def show_intro(self):
        self.write("Ask about a rule\n", "heading")
        self.write(INTRO, "muted")

    def clear(self):
        self.history.clear()
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.sources.clear()
        self.show_intro()

    def zoom(self, step):
        self.scale = max(-3, min(8, self.scale + step))
        self.apply_fonts()

    def toggle_sources(self):
        if self.show_sources.get():
            self.panes.add(self.right, weight=2)
        else:
            self.panes.forget(self.right)

    def copy_answer(self):
        last = [t["content"] for t in self.history if t["role"] == "assistant"]
        if last:
            self.clipboard_clear()
            self.clipboard_append(last[-1])
            self.set_status("Answer copied.")

    def import_rulebook(self):
        """Pick a PDF and build an index from it."""
        if self.busy:
            return
        if indexer is None:
            messagebox.showerror("Cannot import",
                                 "rulebook.py must sit in the same folder as this file.")
            return
        path = filedialog.askopenfilename(
            title="Import a rulebook",
            filetypes=[("PDF rulebook", "*.pdf"), ("PDF rulebook", "*.PDF")])
        if not path:
            return

        # The dialog filters by extension, but a typed name can slip past it.
        if os.path.splitext(path)[1].lower() != ".pdf":
            messagebox.showerror("Not a PDF",
                                 "Only PDF rulebooks can be imported.")
            return
        try:
            size = os.path.getsize(path)
        except OSError as err:
            messagebox.showerror("Cannot read that file", str(err))
            return
        if size > MAX_IMPORT_BYTES:
            messagebox.showerror(
                "That file is too large",
                f"{os.path.basename(path)} is {size / 1_000_000:.0f} MB.\n"
                f"The limit is {MAX_IMPORT_BYTES // 1_000_000} MB.")
            return

        target = os.path.splitext(path)[0] + ".db"
        if os.path.exists(target) and not messagebox.askyesno(
                "Replace that index?",
                f"{os.path.basename(target)} already exists.\n\nBuild it again?"):
            return

        self.pending_import = path
        self.busy = True
        self.send.state(["disabled"])
        self.more.state(["disabled"])
        # Windows will not let the file be replaced while we hold it open.
        if os.path.abspath(target) == os.path.abspath(core.DB["path"]):
            self.db.close()
            self.db = None
        self.set_status(f"Indexing {os.path.basename(path)}…")
        threading.Thread(target=self.index_work, args=(path, target),
                         daemon=True).start()

    def index_work(self, pdf, target):
        """Build an index off the main thread, reporting through the inbox."""
        def report(stage, done, total):
            share = f" {done}/{total}" if total else ""
            self.inbox.put(("status", f"{stage}{share} — {os.path.basename(pdf)}"))

        try:
            indexer.build(pdf, target, progress=report)
        except SystemExit as err:            # the indexer bails out this way
            self.inbox.put(("index_failed", str(err) or "The indexer stopped."))
        except Exception as err:
            self.inbox.put(("index_failed", f"{type(err).__name__}: {err}"))
        else:
            self.inbox.put(("indexed", target))

    def finish_import(self, target):
        try:
            self.use_index(target)
        except SystemExit as err:
            self.import_failed(str(err))
            return
        self.busy = False
        self.send.state(["!disabled"])
        self.more.state(["!disabled"])
        self.set_status()
        count = self.db.execute("SELECT COUNT(*) c FROM sections").fetchone()["c"]
        messagebox.showinfo("Rulebook indexed",
                            f"{os.path.basename(target)} is ready: {count} chunks.\n\n"
                            "The window is now searching this book.")

    def import_failed(self, detail):
        if self.db is None:                  # put the old index back
            try:
                self.db = core.connect()
            except SystemExit:
                self.db = None
        self.busy = False
        self.send.state(["!disabled"])
        self.more.state(["!disabled"])
        self.set_status() if self.db else self.status.configure(text="No index open.")
        messagebox.showerror("Could not index that rulebook", detail)

    def use_index(self, path):
        """Point the window at an index file and refresh everything it feeds."""
        core.DB["path"] = path
        self.db = core.connect()
        self.outline = self.load_outline()
        self.clear()
        self.title(self.book_name())
        self.set_status()

    def open_index(self):
        path = filedialog.askopenfilename(title="Open a rulebook index",
                                          filetypes=[("Rulebook index", "*.db"), ("All", "*")])
        if not path:
            return
        try:
            self.use_index(path)
        except SystemExit as err:
            messagebox.showerror("Cannot open index", str(err))

    # -------------------------------------------------------------- the cycle

    def on_return(self, event):
        if event.state & 0x0001:  # Shift held, let the newline through
            return None
        self.submit(follow=bool(self.history))  # Enter continues an open thread
        return "break"

    def submit(self, follow=False):
        """Ask a question. A new one clears the window; a follow-up keeps it."""
        question = self.entry.get("1.0", "end").strip()
        if not question or self.busy:
            return
        self.entry.delete("1.0", "end")
        if not follow:
            self.clear()
        self.busy = True
        self.send.state(["disabled"])
        self.more.state(["disabled"])
        if not self.history:
            self.transcript.configure(state="normal")
            self.transcript.delete("1.0", "end")
            self.transcript.configure(state="disabled")
        self.pending = question
        self.write("Question\n", "label")
        self.write(f"{question}\n", "you")
        self.set_status("Searching the book…")
        threading.Thread(target=self.work, args=(question, follow), daemon=True).start()

    def work(self, question, follow):
        lookup = question
        if follow and len(question.split()) < 6:
            previous = [t["content"] for t in self.history if t["role"] == "user"]
            if previous:
                lookup = previous[-1] + " " + question
        try:
            found = core.retrieve(self.db, lookup)
        except Exception as err:
            self.inbox.put(("error", f"Search failed: {err}"))
            return
        # A follow-up answers over everything the conversation has gathered so far.
        if follow:
            pool = {s["id"]: s for s in self.sources.values()}
            pool.update({s["id"]: s for s in found})
            found = list(pool.values())
        self.inbox.put(("sources", (found, core.query_terms(lookup) or "")))
        if not found:
            self.inbox.put(("answer", {"error": "empty",
                                       "text": "Nothing in the index matches that. "
                                               "Try the words the book itself uses."}))
            return
        self.inbox.put(("status", "Reading the excerpts…"))
        self.inbox.put(("answer", core.ask_model(question, found, self.outline,
                                                 list(self.history))))

    def drain(self):
        while True:
            try:
                kind, payload = self.inbox.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.set_status(payload)
            elif kind == "sources":
                self.fill_sources(*payload)
            elif kind == "answer":
                self.render(payload)
            elif kind == "error":
                self.write(payload + "\n", "warn")
                self.done()
            elif kind == "indexed":
                self.finish_import(payload)
            elif kind == "index_failed":
                self.import_failed(payload)
        self.after(80, self.drain)

    def done(self):
        self.busy = False
        self.send.state(["!disabled"])
        self.more.state(["!disabled"])
        self.set_status()
        self.entry.focus_set()

    # ------------------------------------------------------------- rendering

    def fill_sources(self, found, terms):
        self.terms = [t.strip('"') for t in re.findall(r'"([^"]+)"', terms) if " " not in t]
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.sources = {s["id"]: s for s in found}
        for src in found:
            pages = (str(src["page_start"]) if src["page_start"] == src["page_end"]
                     else f"{src['page_start']}–{src['page_end']}")
            label = src["path"].split(" > ")[-1]
            if src.get("cited"):
                label += "  (cross-reference)"
            self.tree.insert("", "end", iid=str(src["id"]),
                             text=f"#{src['id']}  {label}", values=(pages,))
        if found:
            self.tree.selection_set(str(found[0]["id"]))
            self.show_excerpt(found[0]["id"])

    def on_pick(self, _event):
        picked = self.tree.selection()
        if picked:
            self.show_excerpt(int(picked[0]))

    def show_excerpt(self, source_id):
        src = self.sources.get(source_id)
        if not src:
            return
        pages = (f"p.{src['page_start']}" if src["page_start"] == src["page_end"]
                 else f"pp.{src['page_start']}–{src['page_end']}")
        self.excerpt.configure(state="normal")
        self.excerpt.delete("1.0", "end")
        self.excerpt.insert("end", f"#{src['id']}  {pages}  {src['path']}\n", "head")

        # Paragraphs go in one at a time, so remember where each one landed:
        # the styles column addresses the stored text, not the widget.
        placed, at = [], 0
        for line in src["text"].split("\n"):
            start, body, tag = at, line, None
            if line.startswith("## "):       # a heading the indexer marked
                start, body, tag = at + 3, line[3:], "subhead"
                self.excerpt.insert("end", "\n")
            here = self.excerpt.index("end-1c")
            self.excerpt.insert("end", body + "\n" + ("" if tag else "\n"), tag or ())
            placed.append((start, len(body), here))
            at += len(line) + 1              # the newline the join put back

        self.apply_styles(src.get("styles"), placed)
        for term in self.terms:
            if len(term) < 3:
                continue
            start = "1.0"
            while True:
                found = self.excerpt.search(term, start, stopindex="end", nocase=True)
                if not found:
                    break
                end = f"{found}+{len(term)}c"
                self.excerpt.tag_add("mark", found, end)
                start = end
        self.excerpt.configure(state="disabled")
        self.excerpt.see("1.0")

    def apply_styles(self, styles, placed):
        """Paint the bold and italic runs the indexer recorded onto the excerpt."""
        if not styles:
            return
        try:
            runs = json.loads(styles)
        except (TypeError, ValueError):
            return
        for start, end, code in runs:
            tag = {"b": "strong", "i": "emph", "x": "strongemph"}.get(code)
            if not tag:
                continue
            for para_start, length, where in placed:
                head = max(start, para_start)
                tail = min(end, para_start + length)
                if head >= tail:
                    continue
                self.excerpt.tag_add(tag, f"{where}+{head - para_start}c",
                                     f"{where}+{tail - para_start}c")

    def shape(self, line):
        """Decide how one line of the model's answer should be laid out."""
        stripped = line.strip()
        if stripped.startswith("#"):                      # a markdown heading
            return stripped.lstrip("#").strip(), "heading"
        bullet = re.match(r"[-*•]\s+(.*)", stripped)
        if bullet:
            return "•  " + bullet.group(1), "bullet"
        number = re.match(r"(\d+[.)])\s+(.*)", stripped)
        if number:
            return f"{number.group(1)}  {number.group(2)}", "bullet"
        return stripped, "answer"

    def render(self, result):
        text = result.get("text", "")
        if result.get("error"):
            self.write("\n" + text + "\n", "warn")
            self.done()
            return
        self.write("Answer\n", "label")
        for block in [b for b in re.split(r"\n{2,}", text) if b.strip()]:
            for line in block.split("\n"):
                if not line.strip():
                    continue
                self.insert_line(*self.shape(line))
            self.write("\n")
        self.write("─" * 40 + "\n", "rule")
        self.history.append({"role": "user", "content": self.pending})
        self.history.append({"role": "assistant", "content": text})
        self.done()

    def emphasis(self, text, tag):
        """Write a stretch of text, honouring **bold** and *italic* markers."""
        for piece in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_)", text):
            if not piece:
                continue
            if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
                self.write(piece[2:-2], tag, "strong")
            elif piece[0] in "*_" and piece[-1] == piece[0] and len(piece) > 2:
                self.write(piece[1:-1], tag, "emph")
            else:
                self.write(piece, tag)

    def insert_line(self, line, tag):
        """Write one line, turning [#id p.N] markers into clickable citations."""
        cursor = 0
        for match in CITE.finditer(line):
            self.emphasis(line[cursor:match.start()].rstrip() + " ", tag)
            source_id = int(match.group(1))
            name = f"cite-{source_id}-{self.transcript.index('end')}"
            self.transcript.configure(state="normal")
            start = self.transcript.index("end-1c")
            self.transcript.insert("end", f"#{source_id} p.{match.group(2)}", ("cite", name))
            self.transcript.configure(state="disabled")
            self.transcript.tag_configure(name, foreground=self.colors["accent"])
            self.transcript.tag_bind(name, "<Button-1>",
                                     lambda e, i=source_id: self.jump(i))
            self.transcript.tag_bind(name, "<Enter>",
                                     lambda e, n=name: (self.transcript.configure(cursor="hand2"),
                                                        self.transcript.tag_configure(n, underline=True)))
            self.transcript.tag_bind(name, "<Leave>",
                                     lambda e, n=name: (self.transcript.configure(cursor="arrow"),
                                                        self.transcript.tag_configure(n, underline=False)))
            del start
            cursor = match.end()
        self.emphasis(line[cursor:], tag)
        self.write("\n", tag)

    def jump(self, source_id):
        if not self.show_sources.get():
            self.show_sources.set(True)
            self.toggle_sources()
        if str(source_id) in self.tree.get_children():
            self.tree.selection_set(str(source_id))
            self.tree.see(str(source_id))
        self.show_excerpt(source_id)


def main():
    parser = argparse.ArgumentParser(description="Desktop window for a rulebook index.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--config", default=None, help="JSON settings (default config.json)")
    args = parser.parse_args()
    core.load_config(args.config)
    app = App(args.db or core.DB["path"], args.model or core.CONFIG["model"])
    app.mainloop()


if __name__ == "__main__":
    main()
