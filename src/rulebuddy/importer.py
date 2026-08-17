#!/usr/bin/env python3
"""importer.py - a window that turns a folder of PDFs into one collection.

Pick a folder. Every PDF in it is tested before any work starts, because a book
with no bookmarks, or a scan, cannot be indexed, and finding that out halfway
through a long import wastes the whole run. The list says which books are good
and why the others are not. Then index the good ones under a name you choose.

Opened from File in the main window.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

from . import core

try:
    from . import indexer
except (ImportError, SystemExit):
    indexer = None


class FolderImport(tk.Toplevel):
    """Test a folder of PDFs, then hand the good ones to the main window."""

    def __init__(self, parent, colors=None, fonts=None, on_index=None,
                 max_bytes=None, start=""):
        super().__init__(parent)
        self.title("Import a Folder")
        self.geometry("860x600")
        self.minsize(640, 420)
        self.colors = dict({"muted": "#6B6B6B", "accent": "#1A4E8A",
                            "quote": "#F4F5F6", "page": "#FFFFFF",
                            "ink": "#1A1A1A", "warn": "#9A3D16"}, **(colors or {}))
        self.fonts = fonts or stock_fonts()
        self.on_index = on_index           # called with (plan, name)
        self.max_bytes = max_bytes
        self.results = []                  # one dict per PDF, from indexer.check_pdf
        self.inbox = queue.Queue()
        self.scanning = False

        self.folder = tk.StringVar(value=start)
        self.name = tk.StringVar()
        self.deep = tk.BooleanVar(value=False)

        self.build()
        if start:
            self.scan()
        else:
            self.say("Pick a folder of PDFs.")

    # ---------------------------------------------------------------- layout

    def build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="Folder").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.folder).pack(side=tk.LEFT, fill=tk.X,
                                                      expand=True, padx=(8, 8))
        ttk.Button(top, text="Browse…", width=10, command=self.choose
                   ).pack(side=tk.LEFT)

        row = ttk.Frame(self, padding=(12, 0, 12, 8))
        row.pack(fill=tk.X)
        ttk.Checkbutton(row, variable=self.deep, text="Look in subfolders"
                        ).pack(side=tk.LEFT)
        self.scan_btn = ttk.Button(row, text="Check the PDFs", command=self.scan)
        self.scan_btn.pack(side=tk.LEFT, padx=(12, 0))

        middle = ttk.Frame(self, padding=(12, 0, 12, 0))
        middle.pack(fill=tk.BOTH, expand=True)
        self.header = ttk.Label(middle, text="No folder checked",
                                font=self.fonts["question"])
        self.header.pack(anchor="w", pady=(0, 6))

        holder = ttk.Frame(middle)
        holder.pack(fill=tk.BOTH, expand=True)
        self.style_tree()
        self.tree = ttk.Treeview(holder, columns=("pages", "state"),
                                 show="tree headings", style="Import.Treeview")
        self.tree.heading("#0", text="Book")
        self.tree.heading("pages", text="Pages")
        self.tree.heading("state", text="Verdict")
        self.tree.column("#0", width=380, stretch=True)
        self.tree.column("pages", width=64, stretch=False, anchor="e")
        self.tree.column("state", width=330, stretch=True)
        bar = ttk.Scrollbar(holder, command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.tag_configure("good", foreground=self.colors["ink"])
        self.tree.tag_configure("bad", foreground=self.colors["warn"])
        self.tree.tag_configure("stripe", background=self.colors["quote"])

        named = ttk.Frame(self, padding=(12, 10, 12, 0))
        named.pack(fill=tk.X)
        ttk.Label(named, text="Collection name").pack(side=tk.LEFT)
        ttk.Entry(named, textvariable=self.name).pack(side=tk.LEFT, fill=tk.X,
                                                      expand=True, padx=(8, 0))

        bottom = ttk.Frame(self, padding=(12, 10, 12, 12))
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="Close", width=10, command=self.destroy
                   ).pack(side=tk.RIGHT)
        self.index_btn = ttk.Button(bottom, text="Index the good books", width=20,
                                    command=self.start_index, state="disabled")
        self.index_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.status = ttk.Label(self, anchor="w", padding=(12, 4),
                                font=self.fonts["small"],
                                foreground=self.colors["muted"])
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def style_tree(self):
        style = ttk.Style(self)
        f = self.fonts
        style.configure("Import.Treeview",
                        font=f["body"],
                        rowheight=f["body"].metrics("linespace") + 10,
                        background=self.colors["page"],
                        fieldbackground=self.colors["page"],
                        foreground=self.colors["ink"],
                        borderwidth=0)
        style.map("Import.Treeview",
                  background=[("selected", self.colors["accent"])],
                  foreground=[("selected", self.colors["page"])])
        style.configure("Import.Treeview.Heading", font=f["bold"],
                        foreground=self.colors["accent"],
                        background=self.colors["quote"])
        style.map("Import.Treeview.Heading",
                  foreground=[("active", self.colors["accent"])],
                  background=[("active", self.colors["quote"])])

    def say(self, message):
        self.status.configure(text=message)

    # ----------------------------------------------------------- the checking

    def choose(self):
        folder = filedialog.askdirectory(parent=self, title="Folder of PDFs",
                                         mustexist=True)
        if folder:
            self.folder.set(folder)
            self.scan()

    def pdfs_in(self, folder):
        """Every PDF under the folder, by name. Subfolders only when asked."""
        found = []
        if self.deep.get():
            for here, _, names in os.walk(folder):
                found += [os.path.join(here, n) for n in names
                          if n.lower().endswith(".pdf")]
        else:
            found = [os.path.join(folder, n) for n in os.listdir(folder)
                     if n.lower().endswith(".pdf")]
        return sorted(found, key=lambda p: os.path.basename(p).lower())

    def scan(self):
        """Test every PDF in the folder, off the main thread."""
        if self.scanning:
            return
        if indexer is None:
            self.say("PyMuPDF is missing, so no PDF can be checked.")
            return
        folder = self.folder.get().strip()
        if not os.path.isdir(folder):
            self.say("That is not a folder.")
            return
        try:
            paths = self.pdfs_in(folder)
        except OSError as err:
            self.say(f"Cannot read that folder: {err}")
            return
        if not paths:
            self.results = []
            self.show()
            self.say("No PDFs in that folder.")
            return

        if not self.name.get().strip():
            self.name.set(os.path.basename(os.path.abspath(folder)))
        self.results = []
        self.show()
        self.scanning = True
        self.scan_btn.configure(state="disabled")
        self.index_btn.configure(state="disabled")
        self.say(f"Checking {len(paths)} PDFs…")
        threading.Thread(target=self.scan_work, args=(paths,),
                         daemon=True).start()
        self.after(80, self.drain)

    def scan_work(self, paths):
        """One report per book, so a slow folder fills the list as it goes."""
        for i, path in enumerate(paths, start=1):
            try:
                result = indexer.check_pdf(path, max_bytes=self.max_bytes)
            except Exception as err:       # a bad file must not stop the folder
                result = {"path": path, "name": os.path.basename(path),
                          "ok": False, "pages": 0,
                          "reason": f"{type(err).__name__}: {err}"}
            self.inbox.put(("book", result, i, len(paths)))
        self.inbox.put(("done", None, len(paths), len(paths)))

    def drain(self):
        """Move the worker's findings into the window."""
        try:
            while True:
                kind, result, done, total = self.inbox.get_nowait()
                if kind == "book":
                    self.results.append(result)
                    self.show()
                    self.say(f"Checked {done} of {total}: {result['name']}")
                else:
                    self.scanning = False
                    self.scan_btn.configure(state="normal")
                    self.finish_scan()
                    return
        except queue.Empty:
            pass
        if self.scanning:
            self.after(80, self.drain)

    def finish_scan(self):
        good = self.good()
        self.index_btn.configure(state="normal" if good else "disabled")
        if good:
            self.say(f"{len(good)} of {len(self.results)} books can be indexed. "
                     "Name the collection, then index.")
        else:
            self.say("No book here can be indexed. Every reason is in the list.")

    # ------------------------------------------------------------- display

    def good(self):
        return [r for r in self.results if r["ok"]]

    def show(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for i, result in enumerate(self.results):
            tags = ["good" if result["ok"] else "bad"]
            if i % 2:
                tags.append("stripe")
            self.tree.insert("", "end", iid=str(i), text=result["name"],
                             values=(result["pages"] or "",
                                     "Ready" if result["ok"] else result["reason"]),
                             tags=tuple(tags))
        good = len(self.good())
        self.header.configure(
            text=f"{len(self.results)} PDFs · {good} ready · "
                 f"{len(self.results) - good} cannot be indexed"
            if self.results else "No folder checked")

    # ------------------------------------------------------------- indexing

    def start_index(self):
        """Hand the good books to the main window, which owns the indexing."""
        good = self.good()
        if not good or not self.on_index:
            return
        name = self.name.get().strip()
        if not name:
            self.say("Give the collection a name first.")
            return
        plan = [(r["path"], core.title_from_path(r["path"])) for r in good]
        self.destroy()
        self.on_index(plan, name)


def stock_fonts():
    """Plain faces, for when this window opens without the main one."""
    size = tkfont.nametofont("TkTextFont").actual("size") or 10
    family = tkfont.nametofont("TkTextFont").actual("family")
    return {
        "body": tkfont.Font(family=family, size=size),
        "bold": tkfont.Font(family=family, size=size, weight="bold"),
        "question": tkfont.Font(family=family, size=size + 1, weight="bold"),
        "small": tkfont.Font(family="Consolas", size=max(9, size - 3)),
    }
