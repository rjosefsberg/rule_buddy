#!/usr/bin/env python3
"""library.py - browse and filter the Charm library of the open collection.

The library is built from the sections already indexed, so this window opens on
whatever collection the main window has. Filters narrow together: a book, a
tree, a type, a keyword, and an Essence ceiling, over a word search.

Opened from Tools in the main window.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from . import charms

ANY = "Any"


class CharmLibrary(tk.Toplevel):
    """One window over the charms table. It reads; the indexer writes."""

    def __init__(self, parent, db, colors=None, fonts=None, on_open=None,
                 mechanic="Charm"):
        super().__init__(parent)
        self.db = db
        self.on_open = on_open             # called with (source path, page)
        self.mechanic = mechanic
        self.title(f"{mechanic} Library")
        self.geometry("1080x720")
        self.minsize(760, 520)
        self.colors = dict({"muted": "#6B6B6B", "accent": "#1A4E8A",
                            "quote": "#F4F5F6", "page": "#FFFFFF",
                            "ink": "#1A1A1A", "paper": "#FBF8F1",
                            "paperink": "#241F17"}, **(colors or {}))
        self.fonts = fonts or stock_fonts()
        self.rows = []
        self.inbox = queue.Queue()
        self.building = False

        self.terms = tk.StringVar()
        self.tree_pick = tk.StringVar(value=ANY)
        self.type_pick = tk.StringVar(value=ANY)
        self.word_pick = tk.StringVar(value=ANY)
        self.book_pick = tk.StringVar(value=ANY)
        self.essence_pick = tk.StringVar(value=ANY)

        self.build()
        if charms.counted(self.db):
            self.load_filters()
            self.run_search()
        else:
            self.say(f"No {mechanic} library yet. Press Build the library.")

    # ---------------------------------------------------------------- layout

    def build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill=tk.X)
        ttk.Label(top, text="Search").pack(side=tk.LEFT)
        field = ttk.Entry(top, textvariable=self.terms)
        field.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        field.bind("<Return>", lambda e: self.run_search())
        ttk.Button(top, text="Find", width=8, command=self.run_search
                   ).pack(side=tk.LEFT)
        ttk.Button(top, text="Clear", width=8, command=self.clear_filters
                   ).pack(side=tk.LEFT, padx=(6, 0))

        picks = ttk.Frame(self, padding=(12, 0, 12, 8))
        picks.pack(fill=tk.X)
        self.menus = {}
        for label, variable, width in (("Book", self.book_pick, 22),
                                       ("Tree", self.tree_pick, 20),
                                       ("Type", self.type_pick, 16),
                                       ("Keyword", self.word_pick, 18),
                                       ("Essence up to", self.essence_pick, 6)):
            ttk.Label(picks, text=label).pack(side=tk.LEFT, padx=(0, 4))
            box = ttk.Combobox(picks, textvariable=variable, width=width,
                               state="readonly", values=(ANY,))
            box.pack(side=tk.LEFT, padx=(0, 12))
            box.bind("<<ComboboxSelected>>", lambda e: self.run_search())
            self.menus[label] = box

        middle = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        middle.pack(fill=tk.BOTH, expand=True, padx=12)

        left = ttk.Frame(middle)
        middle.add(left, weight=3)
        self.header = ttk.Label(left, text="", font=self.fonts["question"])
        self.header.pack(anchor="w", pady=(0, 6))
        holder = ttk.Frame(left)
        holder.pack(fill=tk.BOTH, expand=True)
        self.style_tree()
        self.list = ttk.Treeview(
            holder, columns=("tree", "cost", "type", "essence", "page"),
            show="tree headings", style="Library.Treeview")
        for column, text, width, anchor in (
                ("#0", self.mechanic, 260, "w"),
                ("tree", "Tree", 150, "w"),
                ("cost", "Cost", 110, "w"),
                ("type", "Type", 110, "w"),
                ("essence", "Ess", 44, "e"),
                ("page", "Page", 54, "e")):
            self.list.heading(column, text=text)
            self.list.column(column, width=width, anchor=anchor,
                             stretch=column in ("#0", "tree"))
        bar = ttk.Scrollbar(holder, command=self.list.yview)
        self.list.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.list.tag_configure("stripe", background=self.colors["quote"])
        self.list.bind("<<TreeviewSelect>>", lambda e: self.show_one())
        self.list.bind("<Double-1>", lambda e: self.open_page())

        right = ttk.Frame(middle)
        middle.add(right, weight=2)
        self.detail = tk.Text(right, wrap="word", relief="flat", padx=18, pady=16,
                              bg=self.colors["paper"], fg=self.colors["paperink"],
                              highlightthickness=0, state="disabled",
                              font=self.fonts["body"], spacing1=2, spacing3=6,
                              cursor="arrow", width=34)
        self.detail.pack(fill=tk.BOTH, expand=True)
        self.detail.tag_configure("name", font=self.fonts["question"],
                                  foreground=self.colors["accent"], spacing3=10)
        self.detail.tag_configure("label", font=self.fonts["bold"])
        self.detail.tag_configure("muted", foreground=self.colors["muted"],
                                  font=self.fonts["small"], spacing1=10)

        bottom = ttk.Frame(self, padding=(12, 8, 12, 12))
        bottom.pack(fill=tk.X)
        self.build_btn = ttk.Button(bottom, text="Build the library", width=18,
                                    command=self.start_build)
        self.build_btn.pack(side=tk.LEFT)
        ttk.Button(bottom, text="Close", width=10, command=self.destroy
                   ).pack(side=tk.RIGHT)
        self.open_btn = ttk.Button(bottom, text="Open the PDF at this page",
                                   command=self.open_page)
        self.open_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.status = ttk.Label(self, anchor="w", padding=(12, 4),
                                font=self.fonts["small"],
                                foreground=self.colors["muted"])
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def style_tree(self):
        style = ttk.Style(self)
        f = self.fonts
        style.configure("Library.Treeview", font=f["body"],
                        rowheight=f["body"].metrics("linespace") + 8,
                        background=self.colors["page"],
                        fieldbackground=self.colors["page"],
                        foreground=self.colors["ink"], borderwidth=0)
        style.map("Library.Treeview",
                  background=[("selected", self.colors["accent"])],
                  foreground=[("selected", self.colors["page"])])
        style.configure("Library.Treeview.Heading", font=f["bold"],
                        foreground=self.colors["accent"],
                        background=self.colors["quote"])
        style.map("Library.Treeview.Heading",
                  foreground=[("active", self.colors["accent"])],
                  background=[("active", self.colors["quote"])])

    def say(self, message):
        self.status.configure(text=message)

    # -------------------------------------------------------------- filters

    def load_filters(self):
        """Fill the menus from what the library actually holds."""
        self.menus["Book"].configure(
            values=[ANY] + charms.choices(self.db, "book"))
        self.menus["Tree"].configure(
            values=[ANY] + charms.choices(self.db, "tree"))
        self.menus["Type"].configure(
            values=[ANY] + charms.choices(self.db, "type"))
        self.menus["Keyword"].configure(
            values=[ANY] + charms.keywords_in(self.db))
        top = self.db.execute("SELECT MAX(essence) FROM charms").fetchone()[0] or 5
        self.menus["Essence up to"].configure(
            values=[ANY] + [str(n) for n in range(1, top + 1)])

    def clear_filters(self):
        self.terms.set("")
        for variable in (self.tree_pick, self.type_pick, self.word_pick,
                         self.book_pick, self.essence_pick):
            variable.set(ANY)
        self.run_search()

    def chosen(self, variable):
        value = variable.get()
        return "" if value == ANY else value

    # -------------------------------------------------------------- searching

    def run_search(self):
        if not charms.counted(self.db):
            return
        try:
            self.rows = charms.search(
                self.db,
                terms=self.terms.get(),
                tree=self.chosen(self.tree_pick),
                type_=self.chosen(self.type_pick),
                keyword=self.chosen(self.word_pick),
                book=self.chosen(self.book_pick),
                essence=int(self.chosen(self.essence_pick) or 0))
        except Exception as err:
            self.say(f"Search failed: {err}")
            return
        self.show()

    def show(self):
        for row in self.list.get_children():
            self.list.delete(row)
        for i, row in enumerate(self.rows):
            self.list.insert("", "end", iid=str(row["id"]), text=row["name"],
                             values=(row["tree"], row["cost"], row["type"],
                                     row["essence"] or "", row["page"]),
                             tags=("stripe",) if i % 2 else ())
        total = charms.counted(self.db)
        self.header.configure(
            text=f"{len(self.rows)} of {total} {self.mechanic.lower()}s")
        if self.rows:
            first = str(self.rows[0]["id"])
            self.list.selection_set(first)
            self.list.see(first)
            self.say("")
        else:
            self.write_detail(None)
            self.say("Nothing matched. Clear a filter and try again.")

    def picked(self):
        chosen = self.list.selection()
        if not chosen:
            return None
        wanted = int(chosen[0])
        return next((r for r in self.rows if r["id"] == wanted), None)

    def show_one(self):
        self.write_detail(self.picked())

    def write_detail(self, row):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if row is not None:
            self.detail.insert("end", row["name"] + "\n", "name")
            for label, value in (("Cost", row["cost"]), ("Mins", row["mins"]),
                                 ("Type", row["type"]),
                                 ("Keywords", row["keywords"] or "None"),
                                 ("Duration", row["duration"]),
                                 ("Prerequisite Charms",
                                  row["prereqs"] or "None")):
                self.detail.insert("end", f"{label}: ", "label")
                self.detail.insert("end", f"{value}\n")
            self.detail.insert("end", "\n" + (row["text"] or "") + "\n")
            book = f"{row['book']}  ·  " if row["book"] else ""
            self.detail.insert("end", f"\n{book}page {row['page']}\n", "muted")
        self.detail.configure(state="disabled")

    def open_page(self):
        row = self.picked()
        if row is None or not self.on_open:
            return
        source = self.db.execute("SELECT source FROM books WHERE id=?",
                                 (row["book_id"],)).fetchone()
        path = source[0] if source else ""
        if not path or not os.path.exists(path):
            self.say("The PDF for that book is not where the index says.")
            return
        self.on_open(path, row["page"])

    # -------------------------------------------------------------- building

    def start_build(self):
        """Read the whole collection again and rebuild the table."""
        if self.building:
            return
        if charms.counted(self.db) and not messagebox.askyesno(
                "Build it again?",
                f"The library already holds {charms.counted(self.db)} "
                f"{self.mechanic.lower()}s.\n\nRead the books again?",
                parent=self):
            return
        self.building = True
        self.build_btn.configure(state="disabled")
        self.say("Reading the books…")
        threading.Thread(target=self.build_work, daemon=True).start()
        self.after(80, self.drain)

    def build_work(self):
        """A second connection, because the reader thread must not share one."""
        try:
            import sqlite3
            path = self.db.execute("PRAGMA database_list").fetchall()[0][2]
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            count = charms.build(db, progress=lambda stage, done, total:
                                 self.inbox.put(("status", f"{stage} {done}/{total}")))
            db.close()
        except Exception as err:
            self.inbox.put(("failed", f"{type(err).__name__}: {err}"))
        else:
            self.inbox.put(("done", count))

    def drain(self):
        try:
            while True:
                kind, detail = self.inbox.get_nowait()
                if kind == "status":
                    self.say(detail)
                    continue
                self.building = False
                self.build_btn.configure(state="normal")
                if kind == "failed":
                    self.say(f"Could not build the library. {detail}")
                    return
                self.load_filters()
                self.run_search()
                self.say(f"The library holds {detail} "
                         f"{self.mechanic.lower()}s.")
                return
        except queue.Empty:
            pass
        if self.building:
            self.after(80, self.drain)


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
