#!/usr/bin/env python3
"""bookmarks.py - a window for editing the bookmarks of any PDF.

Open a PDF, read the bookmarks it already has, or build them from its printed
table of contents, correct them, and write them back into the file. A book with
good bookmarks indexes well, so the same window can index it afterwards.

Opened from Tools in the main window. It does not need an index to be open, and
it does not require the PDF to be one of yours.
"""

import os
import re
import tkinter as tk
import unicodedata
from tkinter import filedialog, font as tkfont, messagebox, ttk

try:
    import pymupdf
except ImportError:                 # older installs expose the same module as fitz
    try:
        import fitz as pymupdf
    except ImportError:
        pymupdf = None

from . import contents, core

MAX_LEVEL = 4


def flatten(title):
    """One line, no matter what the file holds.

    Bookmarks built from a printed contents page often keep the line break the
    typesetter used, so a title arrives with a line feed inside it. A row of a
    list shows one line, and everything after the break disappears.
    """
    title = unicodedata.normalize("NFKC", core.unpua(title or ""))
    return re.sub(r"\s+", " ", title).strip()


class BookmarkEditor(tk.Toplevel):
    """Edit one PDF's bookmarks. Everything happens in this window."""

    def __init__(self, parent, path=None, colors=None, fonts=None, on_index=None):
        super().__init__(parent)
        self.title("Bookmark Editor")
        self.geometry("900x640")
        self.minsize(640, 420)
        self.colors = dict({"muted": "#6B6B6B", "accent": "#1A4E8A",
                            "quote": "#F4F5F6", "page": "#FFFFFF",
                            "ink": "#1A1A1A", "rule": "#D2D6DA"}, **(colors or {}))
        self.fonts = fonts or self.stock_fonts()
        self.on_index = on_index           # called with a path when indexing
        self.entries = []                  # {level, title, page} with page 0 based
        self.page_count = 0
        # A reader shows the page label, not the sequence number, so the editor
        # has to speak the same language or every row looks off by one.
        self.labels = {}                   # index -> printed label
        self.by_label = {}                 # printed label -> index
        self.path = tk.StringVar(value=path or "")
        self.pages = tk.StringVar()
        self.also_index = tk.BooleanVar(value=False)

        self.build()
        if self.path.get():
            self.load_pdf(self.path.get())
        else:
            self.say("Open a PDF. Any PDF will do, whether or not it is indexed.")

    # ---------------------------------------------------------------- layout

    def build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="PDF").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.path).pack(side=tk.LEFT, fill=tk.X,
                                                    expand=True, padx=(8, 8))
        ttk.Button(top, text="Open…", width=10, command=self.choose).pack(side=tk.LEFT)

        row = ttk.Frame(self, padding=(12, 0, 12, 8))
        row.pack(fill=tk.X)
        ttk.Button(row, text="Read the PDF's bookmarks", command=self.read_toc
                   ).pack(side=tk.LEFT)
        ttk.Label(row, text="   or contents page(s)").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.pages, width=12).pack(side=tk.LEFT, padx=6)
        ttk.Button(row, text="Read", command=self.read_contents).pack(side=tk.LEFT)

        middle = ttk.Frame(self, padding=(12, 0, 12, 0))
        middle.pack(fill=tk.BOTH, expand=True)
        self.header = ttk.Label(middle, text="No outline loaded",
                                font=self.fonts["question"])
        self.header.pack(anchor="w", pady=(0, 6))

        holder = ttk.Frame(middle)
        holder.pack(fill=tk.BOTH, expand=True)
        self.style_tree()
        self.tree = ttk.Treeview(holder, columns=("page",), show="tree headings",
                                 selectmode="extended",
                                 style="Bookmark.Treeview")
        self.tag_tree()
        self.tree.heading("#0", text="Title")
        self.tree.heading("page", text="Page")
        self.tree.column("#0", width=720, stretch=True)
        self.tree.column("page", width=70, stretch=False, anchor="e")
        bar = ttk.Scrollbar(holder, command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda e: self.rename())
        self.tree.bind("<<TreeviewSelect>>", self.show_full_title)

        tools = ttk.Frame(middle, padding=(0, 8))
        tools.pack(fill=tk.X)
        for text, command in (("Rename…", self.rename),
                              ("◀ Out", lambda: self.shift_level(-1)),
                              ("In ▶", lambda: self.shift_level(1)),
                              ("Set page…", self.set_page),
                              ("Add…", self.add_entry),
                              ("Delete", self.delete_entry)):
            ttk.Button(tools, text=text, command=command).pack(side=tk.LEFT, padx=(0, 6))

        bottom = ttk.Frame(self, padding=(12, 6, 12, 12))
        bottom.pack(fill=tk.X)
        ttk.Checkbutton(bottom, variable=self.also_index,
                        text="Index this book after saving").pack(side=tk.LEFT)
        ttk.Button(bottom, text="Close", width=10, command=self.destroy
                   ).pack(side=tk.RIGHT)
        self.save_btn = ttk.Button(bottom, text="Save to PDF", width=14,
                                   command=self.save)
        self.save_btn.pack(side=tk.RIGHT, padx=(0, 8))

        self.status = ttk.Label(self, anchor="w", padding=(12, 4),
                                font=self.fonts["small"],
                                foreground=self.colors["muted"])
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def stock_fonts(self):
        """The app hands its own faces in. Alone, the editor makes plain ones."""
        size = tkfont.nametofont("TkTextFont").actual("size") or 10
        family = tkfont.nametofont("TkTextFont").actual("family")
        return {
            "body": tkfont.Font(family=family, size=size),
            "bold": tkfont.Font(family=family, size=size, weight="bold"),
            "question": tkfont.Font(family=family, size=size + 1, weight="bold"),
            "small": tkfont.Font(family="Consolas", size=max(9, size - 3)),
        }

    def style_tree(self):
        """Paint the list. The list is the window, so it carries the look."""
        style = ttk.Style(self)
        f = self.fonts
        row = f["body"].metrics("linespace") + 10
        style.configure("Bookmark.Treeview",
                        font=f["body"],
                        rowheight=row,
                        background=self.colors["page"],
                        fieldbackground=self.colors["page"],
                        foreground=self.colors["ink"],
                        borderwidth=0)
        # The system selection blue fights the accent, so use the accent.
        style.map("Bookmark.Treeview",
                  background=[("selected", self.colors["accent"])],
                  foreground=[("selected", self.colors["page"])])
        style.configure("Bookmark.Treeview.Heading",
                        font=f["bold"],
                        foreground=self.colors["accent"],
                        background=self.colors["quote"])
        # A pressed heading must not fall back to the plain system colour.
        style.map("Bookmark.Treeview.Heading",
                  foreground=[("active", self.colors["accent"])],
                  background=[("active", self.colors["quote"])])

    def tag_tree(self):
        """One tag for each level, and one for the tint on other rows.

        Four spaces of indent do not tell a chapter from a footnote. Weight and
        colour do, and they still read when the title is long.
        """
        f = self.fonts
        self.tree.tag_configure("stripe", background=self.colors["quote"])
        self.tree.tag_configure("level1", font=f["bold"],
                                foreground=self.colors["ink"])
        self.tree.tag_configure("level2", font=f["body"],
                                foreground=self.colors["ink"])
        for name in ("level3", "level4"):
            self.tree.tag_configure(name, font=f["body"],
                                    foreground=self.colors["muted"])

    def say(self, message):
        self.status.configure(text=message)

    def busy(self, working, message):
        """Show a wait, and paint it before the long job blocks the window."""
        self.say(message)
        self.save_btn.configure(state="disabled" if working else "normal")
        try:
            self.configure(cursor="watch" if working else "")
        except tk.TclError:
            pass
        self.update_idletasks()

    # ------------------------------------------------------------- loading

    def choose(self):
        path = filedialog.askopenfilename(
            parent=self, title="Open a PDF",
            filetypes=[("PDF", "*.pdf"), ("PDF", "*.PDF")])
        if path:
            self.path.set(path)
            self.load_pdf(path)

    def load_pdf(self, path):
        """Open the file and show whatever bookmarks it already carries."""
        if pymupdf is None:
            messagebox.showerror("Cannot open", "PyMuPDF is missing.", parent=self)
            return
        if not os.path.exists(path):
            self.say(f"No file at {path}")
            return
        try:
            doc = pymupdf.open(path)
        except Exception as err:
            messagebox.showerror("Cannot open that PDF", str(err), parent=self)
            return
        self.page_count = doc.page_count
        existing = doc.get_toc()
        self.read_labels(doc)
        doc.close()
        if existing:
            self.entries = [{"level": level, "title": flatten(title),
                             "page": page - 1}
                            for level, title, page in existing]
            self.show()
            self.say(f"{os.path.basename(path)}: {self.page_count} pages, "
                     f"{len(existing)} bookmarks already.")
        else:
            self.entries = []
            self.show()
            self.say(f"{os.path.basename(path)}: {self.page_count} pages, "
                     "no bookmarks. Give the contents page numbers and press Read.")

    def read_labels(self, doc):
        """Remember what each page is called, so the editor agrees with a reader."""
        self.labels, self.by_label = {}, {}
        for index in range(doc.page_count):
            try:
                label = doc[index].get_label()
            except Exception:
                label = ""
            if label:
                self.labels[index] = label
                self.by_label.setdefault(label, index)

    def page_name(self, index):
        """What a reader calls this page."""
        return self.labels.get(index, str(index + 1))

    def show_full_title(self, _event=None):
        """A long title does not fit the column, so put it in the status line."""
        picked = self.chosen_many()
        if len(picked) > 1:
            self.say(f"{len(picked)} entries selected.")
        elif picked:
            entry = self.entries[picked[0]]
            self.say(f"page {self.page_name(entry['page'])}  ·  {entry['title']}")

    def read_toc(self):
        if self.path.get():
            self.load_pdf(self.path.get())

    def read_contents(self):
        """Build an outline from the printed contents page."""
        path = self.path.get()
        if not path:
            self.say("Open a PDF first.")
            return
        try:
            numbers = [int(part) for part in self.pages.get().replace(",", " ").split()]
        except ValueError:
            self.say("Contents pages must be numbers, such as: 4 5 6")
            return
        if not numbers:
            self.say("Give the page the contents is printed on, such as: 4")
            return
        try:
            outline = contents.parse(path, numbers)
        except SystemExit as err:
            messagebox.showerror("Cannot read that page", str(err), parent=self)
            return
        except Exception as err:
            messagebox.showerror("Cannot read that page",
                                 f"{type(err).__name__}: {err}", parent=self)
            return

        self.entries = [{"level": e["level"], "title": e["title"], "page": e["page"]}
                        for e in outline["entries"]]
        self.page_count = outline["source"]["pages"]
        self.show()
        self.say(f"{len(self.entries)} entries. Page numbers: "
                 f"{outline['source']['page_numbers']}")

    # ------------------------------------------------------------- display

    def show(self, select=None):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for i, entry in enumerate(self.entries):
            level = max(1, min(entry["level"], MAX_LEVEL))
            indent = "    " * (level - 1)
            tags = [f"level{level}"]
            if i % 2:
                tags.append("stripe")
            self.tree.insert("", "end", iid=str(i),
                             text=f"{indent}{entry['title']}",
                             values=(self.page_name(entry["page"]),),
                             tags=tuple(tags))
        self.header.configure(
            text=f"Outline: {len(self.entries)} entries" if self.entries
            else "No outline loaded")
        if select is not None and self.entries:
            last = len(self.entries) - 1
            wanted = [select] if isinstance(select, int) else list(select)
            iids = [str(min(i, last)) for i in wanted]
            if iids:
                self.tree.selection_set(*iids)
                self.tree.see(iids[0])

    def chosen(self):
        """The first selected row. The commands that edit one entry use this."""
        picked = self.chosen_many()
        return picked[0] if picked else None

    def chosen_many(self):
        """Every selected row, top of the list first."""
        return sorted(int(iid) for iid in self.tree.selection())

    # -------------------------------------------------------------- editing

    def rename(self):
        i = self.chosen()
        if i is None:
            return
        name = ask_line(self, "Rename", "Title:", self.entries[i]["title"])
        if name:
            self.entries[i]["title"] = flatten(name)
            self.show(select=i)

    def shift_level(self, step):
        """Move the selected entries in or out.

        Children are not dragged with a parent. Rows move from the top down,
        because the ceiling of a row comes from the row above it, and that row
        must find its new level first.
        """
        picked = self.chosen_many()
        if not picked:
            return
        for i in picked:
            ceiling = 1 if i == 0 else self.entries[i - 1]["level"] + 1
            level = self.entries[i]["level"] + step
            self.entries[i]["level"] = max(1, min(level, ceiling, MAX_LEVEL))
        self.show(select=picked)

    def set_page(self):
        i = self.chosen()
        if i is None:
            return
        value = ask_line(self, "Set page", "Page, as your reader shows it:",
                         self.page_name(self.entries[i]["page"]))
        if not value:
            return
        if value in self.by_label:
            self.entries[i]["page"] = self.by_label[value]
        else:
            try:
                page = int(value)
            except ValueError:
                self.say(f"No page called {value} in this PDF.")
                return
            if not 1 <= page <= max(1, self.page_count):
                self.say(f"That PDF has {self.page_count} pages.")
                return
            self.entries[i]["page"] = page - 1
        self.show(select=i)

    def add_entry(self):
        """Insert an entry under the selection, or at the end."""
        i = self.chosen()
        name = ask_line(self, "Add a bookmark", "Title:", "")
        if not name:
            return
        at = len(self.entries) if i is None else i + 1
        near = self.entries[i] if i is not None else None
        self.entries.insert(at, {"level": near["level"] if near else 1,
                                 "title": name,
                                 "page": near["page"] if near else 0})
        self.show(select=at)

    def delete_entry(self):
        i = self.chosen()
        if i is None:
            return
        del self.entries[i]
        self.show(select=i)

    # --------------------------------------------------------------- saving

    def save(self):
        """Write the outline into the PDF, then index it when asked."""
        path = self.path.get()
        if not path or not self.entries:
            self.say("Nothing to save.")
            return
        if not messagebox.askyesno(
                "Write into this PDF?",
                f"Write {len(self.entries)} bookmarks into:\n{path}\n\n"
                "The file is changed in place.", parent=self):
            return

        toc = [[e["level"], flatten(e["title"]), e["page"] + 1]
               for e in self.entries]
        # A large book takes seconds to write. The window cannot repaint while
        # PyMuPDF works, so tell the user before the wait starts, and do not let
        # a second Save begin. The file is incomplete until the wait ends.
        self.busy(True, "Saving. Do not open the PDF until this finishes…")
        try:
            doc = pymupdf.open(path)
            doc.set_toc(toc)
            # Incremental keeps the rest of the file untouched, which matters
            # when the file is hundreds of megabytes of artwork.
            doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
            doc.close()
        except Exception as err:
            self.busy(False, "Save failed. The PDF is unchanged.")
            messagebox.showerror("Could not save", f"{type(err).__name__}: {err}",
                                 parent=self)
            return
        name = os.path.basename(path)
        self.busy(False, f"Wrote {len(self.entries)} bookmarks into {name}.")
        messagebox.showinfo(
            "Saved",
            f"{len(self.entries)} bookmarks are now in:\n{name}\n\n"
            "The file is closed. You can open it in your reader.", parent=self)

        if self.also_index.get() and self.on_index:
            self.on_index(path)
            self.destroy()


def ask_line(parent, title, prompt, value=""):
    """A one line prompt. simpledialog does not center on the parent window."""
    box = tk.Toplevel(parent)
    box.title(title)
    box.transient(parent)
    box.resizable(False, False)
    result = {"value": None}

    body = ttk.Frame(box, padding=(14, 12))
    body.pack(fill=tk.BOTH, expand=True)
    ttk.Label(body, text=prompt).pack(anchor="w")
    field = ttk.Entry(body, width=54)
    field.pack(fill=tk.X, pady=(6, 10))
    field.insert(0, value)
    field.select_range(0, "end")

    def accept():
        result["value"] = field.get().strip()
        box.destroy()

    row = ttk.Frame(body)
    row.pack(fill=tk.X)
    ttk.Button(row, text="Cancel", width=10, command=box.destroy).pack(side=tk.RIGHT)
    ttk.Button(row, text="OK", width=10, command=accept).pack(side=tk.RIGHT, padx=(0, 8))
    box.bind("<Return>", lambda e: accept())
    box.bind("<Escape>", lambda e: box.destroy())

    box.update_idletasks()
    box.geometry("+%d+%d" % (
        parent.winfo_rootx() + max(0, (parent.winfo_width() - box.winfo_width()) // 2),
        parent.winfo_rooty() + 120))
    field.focus_set()
    box.grab_set()
    parent.wait_window(box)
    return result["value"]
