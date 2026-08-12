#!/usr/bin/env python3
"""app.py - a desktop window over the rulebook index.

    python -m rulebuddy.indexer index yourbook.pdf
    copy config.example.json config.json   # then put your key in it
    python -m rulebuddy

Uses Tk, which ships with Python, so the window is a real native window with
native menus. Retrieval and the API call come from core.py. On Debian or
Ubuntu, install Tk with:
    sudo apt install python3-tk
"""

import argparse
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

from . import core

try:
    from . import indexer
except (ImportError, SystemExit):
    indexer = None      # searching still works; importing a new book will not

CITE = re.compile(r"\[#(\d+)[^\]]*?(\d+)\]")
MAX_IMPORT_BYTES = 250 * 1_000_000
SIDEBAR_WIDTH = 265
RAIL_WIDTH = 26
COVER_HEIGHT = 120      # thumbnail height on the shelf. The indexer stores
                        # covers at a multiple of this, so they subsample cleanly.
INTRO = ("Answers cite the section and page. Click a citation to read the excerpt "
         "it came from, so you can check the book yourself.\n\n"
         "Questions work best in the book's own words. Try \"cover ranged attack\" "
         "rather than \"can I shoot someone hiding\".\n\n"
         "New question starts a fresh thread. Ask more keeps the current one, and "
         "answers over everything it has found so far.\n")
SEARCH_INTRO = ("Type words from the book and press Search.\n\n"
                "Matching sections appear on the left. Pick one to read it here.\n\n"
                "This is searching your books on this machine. Nothing is sent "
                "anywhere. Add an API key to get written answers that cite what "
                "they used.\n")


class KeyDialog(tk.Toplevel):
    """Ask for an API key, with the choice of keeping it past this session."""

    def __init__(self, parent, current="", muted="#8C8C8C"):
        super().__init__(parent)
        self.transient(parent)
        self.title("Anthropic API key")
        self.resizable(False, False)
        self.result = None

        body = ttk.Frame(self, padding=(16, 14))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, wraplength=430, justify="left",
                  text="A key turns on written answers. Without one the window "
                       "still searches your books and shows the text.").pack(anchor="w")
        ttk.Label(body, text="Key", padding=(0, 10, 0, 2)).pack(anchor="w")

        self.entry = ttk.Entry(body, width=52, show="•")
        self.entry.pack(fill=tk.X)
        self.entry.insert(0, current)

        self.save = tk.BooleanVar(value=bool(current))
        ttk.Checkbutton(body, variable=self.save, text="Save it in config.json for next time"
                        ).pack(anchor="w", pady=(10, 0))
        ttk.Label(body, wraplength=430, justify="left", foreground=muted,
                  text="Saved keys are stored as plain text next to the app. Leave "
                       "this off on a shared machine.").pack(anchor="w", pady=(2, 0))

        row = ttk.Frame(body)
        row.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(row, text="Cancel", width=12, command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(row, text="Check and use", width=14,
                   command=self.accept).pack(side=tk.RIGHT, padx=(0, 8))

        self.bind("<Return>", lambda e: self.accept())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry("+%d+%d" % (
            parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2),
            parent.winfo_rooty() + 90))
        self.entry.focus_set()
        self.grab_set()
        self.wait_window(self)

    def accept(self):
        key = self.entry.get().strip()
        if not key:
            messagebox.showwarning("No key", "Paste a key, or cancel.", parent=self)
            return
        self.result = (key, self.save.get())
        self.destroy()


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
        self.pending_swap = None
        # Indexes opened from outside the books folder, kept for this session so
        # they do not vanish from the list when another book is opened.
        self.extra_books = []
        self.sources = {}
        self.terms = []
        self.inbox = queue.Queue()
        self.busy = False
        self.transcript = None
        self.showing_cover = False
        self.big_cover = None
        # Written answers follow the key: present means on, and the Mode menu
        # can turn them off again without throwing the key away.
        self.want_ai = tk.BooleanVar(value=core.has_key())

        self.title(self.book_name())
        self.geometry("1800x880")
        self.minsize(620, 420)
        self.set_theme()
        self.build_menu()
        self.build_layout()
        self.refresh_books()
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
            # The reading surface is its own material: warmer than the chrome,
            # so the book reads as paper and the app around it does not.
            "chrome": base,
            "paper": "#22211F" if self.dark else "#FBF8F1",
            "paperink": "#E6E1D6" if self.dark else "#241F17",
        }
        self.base_size = tkfont.nametofont("TkTextFont").actual("size") or 13
        self.scale = 0

    def pick_family(self, wanted, fallback):
        """First of `wanted` the system actually has, else the stock face."""
        if not hasattr(self, "_families"):
            self._families = {name.lower() for name in tkfont.families(self)}
        for name in wanted:
            if name.lower() in self._families:
                return name
        return fallback

    def fonts(self):
        """Two voices: the book is set in serif, the app around it is not.

        Prose out of the rulebook — excerpts and answers — reads better with
        the face a book would use, and the contrast keeps chrome from being
        mistaken for content. Metadata stays mono so it reads as machinery.
        """
        size = self.base_size + self.scale
        family = tkfont.nametofont("TkTextFont").actual("family")
        mono = self.pick_family(
            ["Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"], family)
        serif = self.pick_family(
            ["Cambria", "Georgia", "Iowan Old Style", "Palatino Linotype",
             "Palatino", "DejaVu Serif", "Times New Roman"], family)
        read = size + 3                      # serif faces run small at the same size
        return {
            "body": tkfont.Font(family=family, size=size),
            "bold": tkfont.Font(family=family, size=size, weight="bold"),
            "question": tkfont.Font(family=family, size=size + 1, weight="bold"),
            "small": tkfont.Font(family=mono, size=max(9, size - 3)),
            "italic": tkfont.Font(family=family, size=size, slant="italic"),
            "bolditalic": tkfont.Font(family=family, size=size, weight="bold",
                                      slant="italic"),
            # the reading voice
            "read": tkfont.Font(family=serif, size=read),
            "readbold": tkfont.Font(family=serif, size=read, weight="bold"),
            "readitalic": tkfont.Font(family=serif, size=read, slant="italic"),
            "readbolditalic": tkfont.Font(family=serif, size=read, weight="bold",
                                          slant="italic"),
            "readhead": tkfont.Font(family=serif, size=read + 3, weight="bold"),
            "readtitle": tkfont.Font(family=serif, size=read + 8, weight="bold"),
        }

    def apply_fonts(self):
        f = self.fonts()
        self.excerpt.configure(font=f["read"])
        self.entry.configure(font=f["body"])
        if self.transcript is not None:      # search only has no transcript
            self.transcript.configure(font=f["read"])
            # The answer is prose from the book, so it reads in the book's face.
            # Everything the app says about it stays in the interface face.
            for name, spec in (("you", "question"), ("answer", "read"),
                               ("cite", "small"), ("muted", "small"), ("warn", "body"),
                               ("label", "small"), ("rule", "small"), ("bullet", "read"),
                               ("heading", "readhead"), ("strong", "readbold"),
                               ("emph", "readitalic")):
                self.transcript.tag_configure(name, font=f[spec])
        self.excerpt.tag_configure("head", font=f["small"])
        self.excerpt.tag_configure("subhead", font=f["readhead"])
        self.excerpt.tag_configure("strong", font=f["readbold"])
        self.excerpt.tag_configure("emph", font=f["readitalic"])
        self.excerpt.tag_configure("strongemph", font=f["readbolditalic"])
        self.excerpt.tag_configure("title", font=f["readtitle"])
        self.excerpt.tag_configure("muted", font=f["body"])
        self.side_empty.configure(font=f["small"])

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
        m_file.add_command(label="Set API key…", command=self.ask_for_key)
        m_file.add_command(label="Remove API key…", command=self.forget_key)
        m_file.add_separator()
        m_file.add_command(label="Clear conversation", accelerator=f"{key}+K", command=self.clear)
        menu.add_cascade(label="File", menu=m_file)

        m_edit = tk.Menu(menu, tearoff=0)
        m_edit.add_command(label="Copy", accelerator=f"{key}+C",
                           command=lambda: self.focus_get().event_generate("<<Copy>>"))
        m_edit.add_command(label="Copy last answer", command=self.copy_answer)
        menu.add_cascade(label="Edit", menu=m_edit)

        m_tools = tk.Menu(menu, tearoff=0)
        m_tools.add_command(label="Bookmark Editor…", command=self.open_bookmarks)
        menu.add_cascade(label="Tools", menu=m_tools)

        m_mode = tk.Menu(menu, tearoff=0)
        m_mode.add_checkbutton(label="Written answers (AI)", variable=self.want_ai,
                               command=self.toggle_ai)
        menu.add_cascade(label="Mode", menu=m_mode)

        m_view = tk.Menu(menu, tearoff=0)
        self.show_books = tk.BooleanVar(value=True)
        m_view.add_checkbutton(label="Book panel", accelerator=f"{key}+B",
                               variable=self.show_books, command=self.toggle_sidebar)
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
        self.bind_all(f"<{accel}-b>", lambda e: self.set_sidebar(not self.show_books.get()))
        self.bind_all(f"<{accel}-plus>", lambda e: self.zoom(1))
        self.bind_all(f"<{accel}-equal>", lambda e: self.zoom(1))
        self.bind_all(f"<{accel}-minus>", lambda e: self.zoom(-1))

    def build_sidebar(self):
        """The left panel, plus the thin rail that stands in for it when closed.

        Both are built once and swapped by packing, so the panel keeps its
        contents and scroll position across a collapse.
        """
        f = self.fonts()

        self.side = ttk.Frame(self, width=SIDEBAR_WIDTH)
        self.side.pack(side=tk.LEFT, fill=tk.Y)
        # self.side.pack_propagate(False)      # hold the width against the contents

        head = ttk.Frame(self.side, padding=(10, 6, 4, 6))
        head.pack(fill=tk.X)
        ttk.Label(head, text="Books", font=f["bold"]).pack(side=tk.LEFT)
        self.side_close = ttk.Label(head, text="‹", foreground=self.colors["muted"],
                                    cursor="hand2", padding=(6, 0))
        self.side_close.pack(side=tk.RIGHT)
        self.side_close.bind("<Button-1>", lambda e: self.set_sidebar(False))
        ttk.Separator(self.side, orient=tk.HORIZONTAL).pack(fill=tk.X)

        self.side_body = ttk.Frame(self.side, padding=(8, 8))
        self.side_body.pack(fill=tk.BOTH, expand=True)

        # A card per collection: cover on top, name under it. Treeview puts the
        # two side by side on one line and gives no say in the matter, so the
        # list is built from plain frames inside a scrolling canvas.
        self.shelf = tk.Canvas(self.side_body, bg=self.colors["chrome"],
                               highlightthickness=0, width=SIDEBAR_WIDTH - 30)
        bar = ttk.Scrollbar(self.side_body, command=self.shelf.yview)
        self.shelf.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.shelf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.book_area = tk.Frame(self.shelf, bg=self.colors["chrome"])
        self.shelf_window = self.shelf.create_window((0, 0), window=self.book_area,
                                                     anchor="nw")
        self.book_area.bind(
            "<Configure>",
            lambda e: self.shelf.configure(scrollregion=self.shelf.bbox("all")))
        self.shelf.bind(
            "<Configure>",
            lambda e: self.shelf.itemconfigure(self.shelf_window, width=e.width))
        self.shelf.bind("<MouseWheel>",
                        lambda e: self.shelf.yview_scroll(-e.delta // 120, "units"))

        self.menu_target = None
        self.book_menu = tk.Menu(self, tearoff=0)
        self.book_menu.add_command(label="Rename…", command=self.rename_target)
        self.book_menu.add_command(label="Add a book to this collection…",
                                   command=self.add_to_collection)
        self.book_menu.add_command(label="Reimport from PDF…", command=self.reimport_book)
        self.book_menu.add_command(label="Edit bookmarks…",
                                   command=self.edit_book_bookmarks)
        self.book_menu.add_separator()
        self.book_menu.add_command(label="Remove this book…", command=self.remove_from_collection)
        self.book_menu.add_command(label="Delete collection…", command=self.delete_book)

        self.side_empty = ttk.Label(self.side_body, wraplength=SIDEBAR_WIDTH - 40,
                                    justify="left", foreground=self.colors["muted"],
                                    font=f["small"],
                                    text="No books yet.\n\nUse File → Import rulebook… "
                                         "to index a PDF, or put a .db index in the "
                                         "books folder.")

        self.rail = ttk.Frame(self, width=RAIL_WIDTH)
        self.rail.pack_propagate(False)
        self.side_open_btn = ttk.Label(self.rail, text="›", foreground=self.colors["muted"],
                                       cursor="hand2", padding=(0, 8))
        self.side_open_btn.pack(fill=tk.X)
        self.side_open_btn.bind("<Button-1>", lambda e: self.set_sidebar(True))

    def set_sidebar(self, opening):
        """Swap panel for rail, or back. Keeps both left of the divider."""
        self.show_books.set(opening)
        showing, hiding = (self.side, self.rail) if opening else (self.rail, self.side)
        hiding.pack_forget()
        showing.pack(side=tk.LEFT, fill=tk.Y, before=self.divider)

    def toggle_sidebar(self):
        self.set_sidebar(self.show_books.get())

    def ai_mode(self):
        """Written answers are on only when there is a key and you want them."""
        return core.has_key() and self.want_ai.get()

    def switch_mode(self):
        """Rebuild the window for the current mode, keeping what still applies.

        Tearing the panes down and building them again beats juggling two
        arrangements of the same widgets, which is where this sort of thing rots.
        """
        typed = self.entry.get("1.0", "end").strip()
        self.panes.destroy()
        self.build_panes()
        self.apply_fonts()
        self.history.clear()      # a conversation means nothing in search only
        self.sources.clear()
        self.show_intro()
        if typed:
            self.entry.insert("1.0", typed)
        self.set_status()
        self.entry.focus_set()

    def toggle_ai(self):
        """The Mode menu. Turning it on without a key asks for one."""
        if self.busy:
            # The panes are about to be rebuilt; a worker still holding the old
            # buttons would re-enable widgets that no longer exist.
            self.want_ai.set(not self.want_ai.get())
            self.set_status("Still working. Try the mode again when it finishes.")
            return
        if self.want_ai.get() and not core.has_key():
            self.ask_for_key()
            self.want_ai.set(core.has_key())
            return
        self.switch_mode()

    def ask_for_key(self):
        """Take a key, check it against the API, and switch modes if it works."""
        dialog = KeyDialog(self, current=core.CONFIG["key"],
                           muted=self.colors["muted"])
        if not dialog.result:
            return
        key, persist = dialog.result
        if not core.looks_like_key(key):
            if not messagebox.askyesno(
                    "That does not look like a key",
                    "Anthropic keys start with sk-ant- and are long.\n\n"
                    "Try it anyway?"):
                return

        self.set_status("Checking the key…")
        self.update_idletasks()
        ok, detail = core.verify_key(key)
        if not ok:
            self.set_status()
            messagebox.showerror("That key did not work", detail)
            return

        saved, where = core.set_key(key, persist=persist)
        if persist and not saved:
            messagebox.showwarning(
                "Key not saved",
                f"{where}\n\nIt is in use for this session, but you will have to "
                "enter it again next time.")
        self.want_ai.set(True)
        self.switch_mode()
        self.set_status("Key accepted. Written answers are on.")

    def forget_key(self):
        """Drop the key and fall back to search only."""
        if not core.has_key():
            return
        forget = messagebox.askyesno(
            "Remove the key?",
            "Stop using the key and go back to searching only?\n\n"
            "Yes also deletes it from config.json. No keeps the file as it is "
            "and only forgets it for this session.")
        core.clear_key(forget=forget)
        self.want_ai.set(False)
        self.switch_mode()
        self.set_status("Search only. No key in use.")

    def build_layout(self):
        self.status = ttk.Label(self, anchor="w", padding=(10, 3))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        # The sidebar and its collapsed rail sit outside the paned window, so
        # dragging the sash never resizes them and the width stays honest.
        self.divider = ttk.Separator(self, orient=tk.VERTICAL)
        self.build_sidebar()
        self.divider.pack(side=tk.LEFT, fill=tk.Y)
        self.build_panes()

    def build_panes(self):
        """The two arrangements. Which one depends on the mode.

        AI mode reads as a conversation: transcript on the left, sections and
        their text stacked on the right. Search only has no conversation, so the
        results take the middle and the text takes the whole right side.
        """
        self.panes = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.panes.pack(fill=tk.BOTH, expand=True)
        ai = self.ai_mode()

        left = ttk.Frame(self.panes)
        self.panes.add(left, weight=3)
        # The composer is packed first so it claims its height before anything
        # else. Pack hands out space in packing order, so a greedy widget packed
        # ahead of it would squeeze the question box off the bottom of a short
        # window instead of giving up its own room.
        self.build_composer(left, ai)
        self.transcript = self.build_transcript(left) if ai else None

        right = ttk.Frame(self.panes)
        self.panes.add(right, weight=2)
        self.right = right

        # Search only puts the results next to the question that produced them;
        # AI mode keeps them beside the answer that cites them.
        self.build_results(left if not ai else right, fill=not ai)
        self.build_excerpt(right)

    def build_transcript(self, parent):
        wrap = ttk.Frame(parent)
        wrap.pack(fill=tk.BOTH, expand=True, padx=(2, 0), pady=(2, 0))
        text = tk.Text(wrap, wrap="word", padx=26, pady=20, relief="flat",
                       spacing1=3, spacing2=4, spacing3=9, cursor="arrow",
                       bg=self.colors["paper"], fg=self.colors["paperink"],
                       insertbackground=self.colors["paperink"],
                       highlightthickness=0, state="disabled", height=8)
        bar = ttk.Scrollbar(wrap, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True)
        text.tag_configure("label", foreground=self.colors["muted"],
                           spacing1=18, spacing3=2)
        text.tag_configure("you", foreground=self.colors["paperink"],
                           spacing3=12, lmargin1=0, lmargin2=0)
        text.tag_configure("answer", spacing2=3, spacing3=10, lmargin1=0, lmargin2=0)
        text.tag_configure("heading", foreground=self.colors["accent"],
                           spacing1=8, spacing3=4)
        text.tag_configure("cite", foreground=self.colors["accent"])
        text.tag_configure("muted", foreground=self.colors["muted"])
        text.tag_configure("warn", foreground=self.colors["warn"])
        text.tag_configure("bullet", lmargin1=20, lmargin2=36, spacing2=3, spacing3=6)
        text.tag_configure("strong", foreground=self.colors["paperink"])
        text.tag_configure("emph")
        text.tag_configure("rule", foreground=self.colors["rule"],
                           spacing1=12, spacing3=12, justify="center")
        return text

    def build_composer(self, parent, ai):
        composer = ttk.Frame(parent, padding=(10, 6, 10, 10))
        composer.pack(fill=tk.X, side=tk.TOP if not ai else tk.BOTTOM)

        # The box carries the border so it reads as one field, not a sunken widget.
        field = tk.Frame(composer, bg=self.colors["rule"], padx=1, pady=1)
        field.pack(fill=tk.X)
        self.entry = tk.Text(field, height=3 if ai else 1, wrap="word", relief="flat",
                             bd=0, padx=10, pady=8, bg=self.colors["page"],
                             fg=self.colors["ink"], insertbackground=self.colors["ink"],
                             highlightthickness=0)
        self.entry.pack(fill=tk.BOTH, expand=True)
        self.entry.bind("<Return>", self.on_return)
        self.entry.bind("<Shift-Return>", lambda e: None)
        self.entry.bind("<FocusIn>", lambda e: field.configure(bg=self.colors["accent"]))
        self.entry.bind("<FocusOut>", lambda e: field.configure(bg=self.colors["rule"]))

        row = ttk.Frame(composer)
        row.pack(fill=tk.X, pady=(8, 0))
        if ai:
            # Both buttons keep the stock style and one width, so the theme gives
            # them identical padding and their labels share a baseline.
            self.more = ttk.Button(row, text="Ask more", width=14,
                                   command=lambda: self.submit(follow=True))
            self.more.pack(side=tk.RIGHT)
            self.send = ttk.Button(row, text="New question", width=14,
                                   command=lambda: self.submit(follow=False))
            self.send.pack(side=tk.RIGHT, padx=(0, 8))
            hint = "Enter asks  ·  Shift+Enter adds a line"
        else:
            self.more = ttk.Button(row, text="Add a key…", width=14,
                                   command=self.ask_for_key)
            self.more.pack(side=tk.RIGHT)
            self.send = ttk.Button(row, text="Search", width=14,
                                   command=lambda: self.submit(follow=False))
            self.send.pack(side=tk.RIGHT, padx=(0, 8))
            hint = "Searching your books  ·  a key adds written answers"
        self.hint = ttk.Label(row, text=hint, foreground=self.colors["muted"], anchor="w")
        self.hint.pack(side=tk.LEFT, fill=tk.Y)

    def build_results(self, parent, fill):
        head = ttk.Label(parent, text="Sections found", padding=(8, 6))
        head.pack(fill=tk.X)
        self.tree = ttk.Treeview(parent, columns=("page",), show="tree headings",
                                 height=6)
        self.tree.heading("#0", text="Section")
        self.tree.heading("page", text="Page")
        self.tree.column("#0", width=240, stretch=True)
        self.tree.column("page", width=64, stretch=False, anchor="e")
        self.tree.pack(fill=tk.BOTH, expand=fill, padx=6)
        self.tree.bind("<<TreeviewSelect>>", self.on_pick)

    def build_excerpt(self, parent):
        self.excerpt = tk.Text(parent, wrap="word", relief="flat", padx=32, pady=26,
                               bg=self.colors["paper"], fg=self.colors["paperink"],
                               highlightthickness=0, state="disabled", height=5,
                               spacing1=3, spacing2=4, spacing3=9, cursor="arrow")
        self.excerpt.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        # Metadata reads as machinery: mono, quiet, and held well clear
        # of the prose underneath it.
        self.excerpt.tag_configure("head", foreground=self.colors["muted"], spacing3=18)
        self.excerpt.tag_configure("subhead", foreground=self.colors["accent"],
                                   spacing1=14, spacing3=6)
        self.excerpt.tag_configure("mark", background=self.colors["mark"])
        self.excerpt.tag_configure("muted", foreground=self.colors["muted"])
        self.excerpt.tag_configure("centre", justify="center")
        self.excerpt.bind("<Configure>", self.reflow_excerpt)

    def reflow_excerpt(self, _event=None):
        """Hold the text to a readable measure on a wide window.

        A line running the full width of a maximised pane is tiring to read, so
        the column stays near 74 characters and the leftover width becomes
        margin on both sides.
        """
        try:
            char = self.fonts()["read"].measure("n") or 8
        except tk.TclError:
            return
        width = self.excerpt.winfo_width()
        if width < 80:
            return
        margin = max(24, (width - 74 * char) // 2)
        if abs(margin - int(self.excerpt.cget("padx"))) > 3:
            self.excerpt.configure(padx=margin)

        # A resting cover is sized to the pane, so it has to be redrawn when the
        # pane changes. The threshold keeps this off the resize path until the
        # difference would actually show.
        if getattr(self, "showing_cover", False) and self.big_cover is not None:
            if abs(self.cover_room() - self.big_cover.height()) > 60:
                self.show_cover()

        self.apply_fonts()
        self.set_status()
        self.after(60, lambda: self.panes.sashpos(0, int(self.winfo_width() * 0.6)))

    # ----------------------------------------------------------------- helpers

    def book_name(self):
        return f"{core.collection_name(self.db)} — Rulebook"

    def load_outline(self):
        rows = self.db.execute("SELECT title, level, page_start FROM sections"
                               " WHERE part=0 AND level<=2 ORDER BY id").fetchall()
        return "\n".join(f"{'  ' * (r['level'] - 1)}{r['title']} (p.{r['page_start']})"
                         for r in rows[:250])

    def set_status(self, message=None):
        if message is None:
            books = self.db.execute("SELECT COUNT(*) c, SUM(pages) p FROM books").fetchone()
            count = self.db.execute("SELECT COUNT(*) c FROM sections WHERE part=0").fetchone()
            model = core.CONFIG["model"] if self.ai_mode() else "search only"
            shelf = f"{books['c']} books · " if books and books["c"] > 1 else ""
            message = (f"{shelf}{books['p'] or '?'} pages · {count['c']} sections"
                       f" · {model}")
        self.status.configure(text=message)

    def write(self, text, *tags):
        if self.transcript is None:
            return
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text, tags)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def show_intro(self):
        if self.transcript is not None:
            self.write("Ask about a rule\n", "heading")
            self.write(INTRO, "muted")
        self.show_cover()

    def show_cover(self):
        """The reading pane at rest: the open collection, named and pictured.

        Title first, then the cover sized to what is left, and nothing after it.
        Anything below the picture would push the page into scrolling, which
        makes a resting state look like unfinished content.
        """
        self.excerpt.configure(state="normal")
        self.excerpt.delete("1.0", "end")
        current = self.read_collection(core.DB["path"])

        self.excerpt.insert("end", "\n")
        self.excerpt.insert("end", f"{current['label']}\n", ("centre", "title"))
        count = len(current["books"])
        if count > 1:
            self.excerpt.insert("end", f"{count} books in this collection\n",
                                ("centre", "muted"))

        # Held on the instance or Tk drops the image the moment this returns.
        self.big_cover = self.cover_image(current["cover"], self.cover_room())
        if self.big_cover is not None:
            self.excerpt.insert("end", "\n")
            start = self.excerpt.index("end-1c")
            self.excerpt.image_create("end", image=self.big_cover, pady=10)
            # justify works by line, so the image's own line carries the tag
            self.excerpt.tag_add("centre", start, "end")
        self.excerpt.configure(state="disabled")
        self.showing_cover = True

    def cover_room(self):
        """Height left for the cover once the title has taken its share."""
        height = self.excerpt.winfo_height()
        if height < 120:                     # too early to know; pick something sane
            return COVER_HEIGHT * 4
        return max(COVER_HEIGHT, height - 150)

    def clear(self):
        self.history.clear()
        if self.transcript is not None:
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

    def open_bookmarks(self, path=""):
        """Open the Bookmark Editor. Empty unless a caller names a PDF.

        From Tools it opens with nothing loaded, because the editor works on any
        PDF and has no business assuming the open book is the one you meant.
        """
        from .bookmarks import BookmarkEditor
        BookmarkEditor(self, path=path, colors=self.colors,
                       on_index=self.index_pdf)

    def edit_book_bookmarks(self):
        """Open the editor on the PDF behind the book that was right clicked."""
        collection, book_id = self.target(self.menu_target)
        if collection is None:
            return
        books = collection["books"]
        if book_id:
            books = [b for b in books if b["id"] == book_id] or books
        source = next((b["source"] for b in books
                       if b["source"] and os.path.exists(b["source"])), "")
        if not source:
            recorded = books[0]["source"] if books else ""
            messagebox.showinfo(
                "PDF not found",
                f"The PDF behind {collection['label']} is not where the index "
                f"says.\n\n{recorded}\n\nOpen it yourself in the editor.")
        self.open_bookmarks(source)

    def import_rulebook(self):
        """Pick a PDF and build an index from it."""
        if self.busy:
            return
        if indexer is None:
            messagebox.showerror("Cannot import",
                                 "PyMuPDF is missing, so PDFs cannot be indexed.")
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
        self.index_pdf(path)

    def index_pdf(self, path):
        """Index a PDF into the books folder.

        Import picks the file and checks it, then calls this. The Bookmark
        Editor calls it directly, since it already holds a path.
        """
        if self.busy:
            self.set_status("Still working. Try again when it finishes.")
            return
        if indexer is None:
            messagebox.showerror("Cannot import",
                                 "PyMuPDF is missing, so PDFs cannot be indexed.")
            return

        # The index belongs on the shelf, not beside the PDF, or it drops out of
        # the sidebar the moment another book is opened.
        shelf = self.books_dir()
        try:
            os.makedirs(shelf, exist_ok=True)
        except OSError as err:
            messagebox.showerror("Cannot import", f"{shelf}\n\n{err}")
            return
        stem = os.path.splitext(os.path.basename(path))[0]
        target = os.path.join(shelf, stem + ".db")
        if os.path.exists(target) and not messagebox.askyesno(
                "Replace that index?",
                f"{os.path.basename(target)} already exists.\n\nBuild it again?"):
            return

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
        swap = getattr(self, "pending_swap", None)
        if swap and os.path.abspath(swap[0]) == os.path.abspath(target):
            scratch, final = swap
            self.pending_swap = None
            if self.db is not None and (os.path.abspath(final)
                                        == os.path.abspath(core.DB["path"])):
                self.db.close()             # release the file we are about to replace
                self.db = None
            try:
                os.replace(scratch, final)
            except OSError as err:
                self.import_failed(f"Could not replace {os.path.basename(final)}: {err}")
                return
            target = final
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
        swap = getattr(self, "pending_swap", None)
        if swap:                             # a half-built index helps nobody
            self.pending_swap = None
            try:
                os.remove(swap[0])
            except OSError:
                pass
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

    # ------------------------------------------------------------------ books

    def books_dir(self):
        """Where the book indexes live, resolved next to config.json."""
        setting = core.CONFIG.get("books_dir") or "books"
        if os.path.isabs(setting):
            return setting
        return os.path.join(core.app_dir(), setting)

    @staticmethod
    def cover_image(png, height=COVER_HEIGHT):
        """Stored cover bytes as a Tk image, shrunk to fit.

        PhotoImage.subsample only divides by whole numbers, which is why the
        indexer stores covers at a multiple of the height wanted here.
        """
        if not png:
            return None
        try:
            full = tk.PhotoImage(data=png)
        except tk.TclError:
            return None
        step = max(1, round(full.height() / height))
        return full.subsample(step, step) if step > 1 else full

    @staticmethod
    def read_collection(path):
        """Name, cover and contents of an index file, in one pass.

        Opened read only, so a collection can be listed without disturbing it.
        Anything unreadable comes back as a bare filename rather than an error;
        the sidebar has to show something.
        """
        stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        out = {"label": stem, "cover": None, "books": []}
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:
            return out
        try:
            try:
                rows = db.execute("SELECT id, title, source, cover FROM books"
                                  " ORDER BY id").fetchall()
            except sqlite3.Error:
                rows = []                  # older index, no books table yet
            if rows:
                out["books"] = [{"id": r[0], "title": r[1], "source": r[2] or "",
                                 "cover": r[3]} for r in rows]
                out["cover"] = rows[0][3]
                name = db.execute("SELECT value FROM meta WHERE key='name'").fetchone()
                out["label"] = (name[0] if name and name[0] else rows[0][1]) or stem
                return out

            # Version 1: one book, its name in meta and its cover in its own table.
            source = db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
            if source and source[0]:
                out["label"] = os.path.splitext(
                    os.path.basename(source[0]))[0].replace("_", " ")
            try:
                cover = db.execute("SELECT png FROM cover WHERE id=1").fetchone()
                out["cover"] = cover[0] if cover else None
            except sqlite3.Error:
                pass
        finally:
            db.close()
        return out

    def scan_books(self):
        """Every index we know of: the books folder, plus whatever is open now.

        Sorted by name, so the list does not reshuffle when a book is opened
        from somewhere else on disk.
        """
        found = {}
        directory = self.books_dir()
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                if name.lower().endswith(".db"):
                    full = os.path.abspath(os.path.join(directory, name))
                    found[os.path.normcase(full)] = full
        for extra in getattr(self, "extra_books", []):
            if os.path.exists(extra):
                found.setdefault(os.path.normcase(extra), extra)
        current = os.path.abspath(core.DB["path"])
        found.setdefault(os.path.normcase(current), current)
        books = []
        for path in found.values():
            entry = self.read_collection(path)
            entry["path"] = path
            books.append(entry)
        books.sort(key=lambda b: b["label"].lower())
        return books

    def target(self, iid):
        """Split a row id into its collection and, for a child row, its book.

        Collections are numbered; a book inside one is "collection:book".
        """
        if not iid:
            return None, None
        head, _, book_id = str(iid).partition(":")
        try:
            collection = self.books[int(head)]
        except (ValueError, IndexError):
            return None, None
        return collection, int(book_id) if book_id else None

    def remember_book(self, path):
        """Keep hold of an index that lives outside the books folder."""
        full = os.path.abspath(path)
        shelf = os.path.normcase(self.books_dir())
        if os.path.normcase(os.path.dirname(full)) == shelf:
            return                          # already on the shelf, it will be found
        if not any(os.path.normcase(p) == os.path.normcase(full)
                   for p in self.extra_books):
            self.extra_books.append(full)

    def refresh_books(self):
        """Rebuild the shelf and mark whichever collection is open."""
        self.books = self.scan_books()
        for child in self.book_area.winfo_children():
            child.destroy()
        # Tk drops an image the moment nothing references it, so the cards would
        # come up blank without this list holding on to them.
        self.covers = []
        current = os.path.normcase(os.path.abspath(core.DB["path"]))

        for i, book in enumerate(self.books):
            open_now = os.path.normcase(book["path"]) == current
            self.build_card(i, book, open_now)

        if self.books:
            self.side_empty.pack_forget()
            self.shelf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        else:
            self.shelf.pack_forget()
            self.side_empty.pack(anchor="nw")

    def build_card(self, i, book, open_now):
        """One collection: its cover, then its name under it."""
        back = self.colors["quote"] if open_now else self.colors["chrome"]
        edge = self.colors["accent"] if open_now else self.colors["rule"]
        f = self.fonts()

        card = tk.Frame(self.book_area, bg=edge, padx=1, pady=1)
        card.pack(fill=tk.X, pady=(0, 8))
        inner = tk.Frame(card, bg=back, padx=6, pady=8)
        inner.pack(fill=tk.BOTH, expand=True)

        cover = self.cover_image(book["cover"], COVER_HEIGHT)
        self.covers.append(cover)
        if cover is not None:
            tk.Label(inner, image=cover, bg=back, bd=0).pack()
        name = tk.Label(inner, text=book["label"], bg=back, fg=self.colors["ink"],
                        font=f["bold"], wraplength=SIDEBAR_WIDTH - 60,
                        justify="center")
        name.pack(fill=tk.X, pady=(6, 0))

        count = len(book["books"])
        if count > 1:
            tk.Label(inner, text=f"{count} books", bg=back,
                     fg=self.colors["muted"], font=f["small"]).pack()

        self.arm_card(inner, str(i))
        # A collection of one is just that book, so it needs no list under it.
        if count > 1:
            for entry in book["books"]:
                row = tk.Label(inner, text=entry["title"], bg=back,
                               fg=self.colors["muted"], font=f["small"],
                               wraplength=SIDEBAR_WIDTH - 70, justify="left",
                               anchor="w")
                row.pack(fill=tk.X, padx=(8, 0), pady=(4, 0))
                self.arm_card(row, f"{i}:{entry['id']}")

    def arm_card(self, widget, target):
        """Make a card and everything drawn on it answer to the same row id.

        Tk does not pass a click up from a child to its parent, so each label
        carries the bindings itself.
        """
        widget.bind("<Button-1>", lambda e, t=target: self.pick_card(t))
        widget.bind("<Button-3>", lambda e, t=target: self.post_book_menu(e, t))
        if sys.platform == "darwin":
            widget.bind("<Button-2>", lambda e, t=target: self.post_book_menu(e, t))
            widget.bind("<Control-Button-1>", lambda e, t=target: self.post_book_menu(e, t))
        for child in widget.winfo_children():
            self.arm_card(child, target)

    def pick_card(self, target):
        """Open the collection a card belongs to."""
        book, _ = self.target(target)
        if book is None:
            return
        if os.path.normcase(book["path"]) == os.path.normcase(os.path.abspath(core.DB["path"])):
            return                          # already open, nothing to do
        if self.busy:
            self.set_status("Still answering. Try again when it finishes.")
            return
        try:
            self.use_index(book["path"])
        except SystemExit as err:
            messagebox.showerror("Cannot open index", str(err))
            self.refresh_books()

    def post_book_menu(self, event, row):
        """Right click acts on the card under the pointer without opening it.

        Opening is what a left click means, and a right click must not do it.
        """
        self.menu_target = row
        # "Remove this book" only means something on a book inside a collection.
        _, book_id = self.target(row)
        self.book_menu.entryconfigure("Remove this book…",
                                      state="normal" if book_id else "disabled")
        try:
            self.book_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.book_menu.grab_release()

    def rename_target(self):
        """Rename whatever was right clicked: a collection, or a book in one."""
        collection, book_id = self.target(self.menu_target)
        if collection is None:
            return
        if self.busy:
            self.set_status("Still answering. Try again when it finishes.")
            return

        if book_id:
            entry = next((b for b in collection["books"] if b["id"] == book_id), None)
            if entry is None:
                return
            what, current = "book", entry["title"]
        else:
            what, current = "collection", collection["label"]

        name = simpledialog.askstring(f"Rename {what}", f"Name for this {what}:",
                                      initialvalue=current, parent=self)
        if name is None:
            return
        name = name.strip()
        if not name or name == current:
            return

        open_now = os.path.abspath(collection["path"]) == os.path.abspath(core.DB["path"])
        db = self.db if open_now and self.db is not None else None
        own = db is None
        try:
            if own:
                db = sqlite3.connect(collection["path"])
            if book_id:
                db.execute("UPDATE books SET title=? WHERE id=?", (name, book_id))
            else:
                db.execute("INSERT OR REPLACE INTO meta VALUES ('name',?)", (name,))
            db.commit()
        except sqlite3.Error as err:
            messagebox.showerror("Could not rename", str(err))
            return
        finally:
            if own and db is not None:
                db.close()

        if open_now:
            self.title(self.book_name())
            self.set_status(f"Renamed to {name}.")
        self.refresh_books()

    def add_to_collection(self):
        """Index another PDF into an existing collection."""
        collection, _ = self.target(self.menu_target)
        if collection is None:
            return
        if self.busy:
            self.set_status("Still answering. Try again when it finishes.")
            return
        if indexer is None:
            messagebox.showerror("Cannot add",
                                 "PyMuPDF is missing, so PDFs cannot be indexed.")
            return
        pdf = filedialog.askopenfilename(
            title=f"Add a book to {collection['label']}",
            filetypes=[("PDF rulebook", "*.pdf"), ("PDF rulebook", "*.PDF")])
        if not pdf:
            return
        if os.path.splitext(pdf)[1].lower() != ".pdf":
            messagebox.showerror("Not a PDF", "Only PDF rulebooks can be indexed.")
            return
        try:
            size = os.path.getsize(pdf)
        except OSError as err:
            messagebox.showerror("Cannot read that file", str(err))
            return
        if size > MAX_IMPORT_BYTES:
            messagebox.showerror(
                "That file is too large",
                f"{os.path.basename(pdf)} is {size / 1_000_000:.0f} MB.\n"
                f"The limit is {MAX_IMPORT_BYTES // 1_000_000} MB.")
            return

        self.busy = True
        self.send.state(["disabled"])
        self.more.state(["disabled"])
        # Appending writes into the file we may be reading from, so let go first.
        if os.path.abspath(collection["path"]) == os.path.abspath(core.DB["path"]):
            self.db.close()
            self.db = None
        self.set_status(f"Adding {os.path.basename(pdf)}…")
        threading.Thread(target=self.append_work,
                         args=(pdf, collection["path"]), daemon=True).start()

    def append_work(self, pdf, db_path, title=None):
        """Add or replace a book in a collection, off the main thread."""
        def report(stage, done, total):
            share = f" {done}/{total}" if total else ""
            self.inbox.put(("status", f"{stage}{share} — {os.path.basename(pdf)}"))

        try:
            indexer.add_book(pdf, db_path, progress=report, title=title)
        except SystemExit as err:
            self.inbox.put(("index_failed", str(err) or "The indexer stopped."))
        except Exception as err:
            self.inbox.put(("index_failed", f"{type(err).__name__}: {err}"))
        else:
            self.inbox.put(("indexed", db_path))

    def remove_from_collection(self):
        """Take one book back out of a collection."""
        collection, book_id = self.target(self.menu_target)
        if collection is None or not book_id:
            return
        if self.busy:
            self.set_status("Still answering. Try again when it finishes.")
            return
        entry = next((b for b in collection["books"] if b["id"] == book_id), None)
        if entry is None:
            return
        if len(collection["books"]) <= 1:
            messagebox.showinfo(
                "Cannot remove",
                f"{entry['title']} is the only book in {collection['label']}.\n\n"
                "Delete the whole collection instead.")
            return
        if not messagebox.askyesno(
                "Remove this book?",
                f"Take {entry['title']} out of {collection['label']}?\n\n"
                "Its sections stop being searchable. The rest of the collection "
                "is untouched, and the PDF is not deleted.",
                icon="warning", default="no"):
            return

        open_now = os.path.abspath(collection["path"]) == os.path.abspath(core.DB["path"])
        db = self.db if open_now and self.db is not None else None
        try:
            own = db is None
            if own:
                db = sqlite3.connect(collection["path"])
            indexer.remove_book(db, book_id)
            if own:
                db.close()
        except sqlite3.Error as err:
            messagebox.showerror("Could not remove that book", str(err))
            return
        if open_now:
            self.clear()                    # citations into that book are stale now
            self.outline = self.load_outline()
            self.set_status(f"Removed {entry['title']}.")
        self.refresh_books()

    def locate_pdf(self, title, recorded):
        """Find the PDF for a book, asking when it is not where it should be."""
        if recorded and os.path.exists(recorded):
            return recorded
        where = f"\n\nIt was built from:\n{recorded}" if recorded else ""
        if not messagebox.askyesno(
                "Where is the PDF?",
                f"The PDF behind {title} is not where the index says.{where}"
                "\n\nPick it now?"):
            return None
        pdf = filedialog.askopenfilename(
            title=f"PDF for {title}",
            filetypes=[("PDF rulebook", "*.pdf"), ("PDF rulebook", "*.PDF")])
        if not pdf:
            return None
        if os.path.splitext(pdf)[1].lower() != ".pdf":
            messagebox.showerror("Not a PDF", "Only PDF rulebooks can be indexed.")
            return None
        return pdf

    def reimport_book(self):
        """Rebuild from source: one book of a collection, or the whole thing."""
        collection, book_id = self.target(self.menu_target)
        if collection is None:
            return
        if self.busy:
            self.set_status("Still answering. Try again when it finishes.")
            return
        if indexer is None:
            messagebox.showerror("Cannot reimport",
                                 "PyMuPDF is missing, so PDFs cannot be indexed.")
            return
        if book_id:
            self.reimport_one(collection, book_id)
        else:
            self.reimport_all(collection)

    def reimport_one(self, collection, book_id):
        """Rebuild a single book inside a collection, in place.

        add_book writes the new rows before dropping the old ones, in a single
        transaction, so the collection survives a failure unharmed.
        """
        entry = next((b for b in collection["books"] if b["id"] == book_id), None)
        if entry is None:
            return
        pdf = self.locate_pdf(entry["title"], entry["source"])
        if not pdf:
            return
        if not messagebox.askyesno(
                "Reimport this book?",
                f"Rebuild {entry['title']} from:\n{os.path.basename(pdf)}\n\n"
                "The rest of the collection is untouched, and the conversation "
                "is cleared."):
            return

        self.busy = True
        self.send.state(["disabled"])
        self.more.state(["disabled"])
        if os.path.abspath(collection["path"]) == os.path.abspath(core.DB["path"]):
            self.db.close()
            self.db = None
        self.set_status(f"Indexing {os.path.basename(pdf)}…")
        threading.Thread(target=self.append_work,
                         args=(pdf, collection["path"], entry["title"]),
                         daemon=True).start()

    def reimport_all(self, collection):
        """Rebuild every book in a collection, keeping names and order."""
        books = collection["books"]
        if not books:
            messagebox.showinfo("Nothing to reimport",
                                f"{collection['label']} lists no books.")
            return

        plan = []
        for entry in books:
            pdf = self.locate_pdf(entry["title"], entry["source"])
            if not pdf:
                messagebox.showinfo(
                    "Reimport stopped",
                    f"Without a PDF for {entry['title']} the collection cannot be "
                    "rebuilt whole, so nothing was changed.")
                return
            plan.append((pdf, entry["title"]))

        listing = "\n".join(f"  · {title}" for _, title in plan)
        if not messagebox.askyesno(
                "Reimport this collection?",
                f"Rebuild all {len(plan)} book(s) in {collection['label']}?\n\n"
                f"{listing}\n\nThis takes a while. The conversation is cleared."):
            return

        self.busy = True
        self.send.state(["disabled"])
        self.more.state(["disabled"])
        if os.path.abspath(collection["path"]) == os.path.abspath(core.DB["path"]):
            self.db.close()
            self.db = None
        scratch = collection["path"] + ".rebuilding"
        self.pending_swap = (scratch, collection["path"])
        self.set_status(f"Rebuilding {collection['label']}…")
        threading.Thread(target=self.rebuild_work,
                         args=(plan, scratch, collection["label"]),
                         daemon=True).start()

    def rebuild_work(self, plan, scratch, name):
        """Rebuild a whole collection off the main thread."""
        def report(stage, done, total):
            share = f" {done}/{total}" if total else ""
            self.inbox.put(("status", f"{stage}{share}"))

        try:
            indexer.rebuild_collection(scratch, plan, name=name, progress=report)
        except SystemExit as err:
            self.inbox.put(("index_failed", str(err) or "The indexer stopped."))
        except Exception as err:
            self.inbox.put(("index_failed", f"{type(err).__name__}: {err}"))
        else:
            self.inbox.put(("indexed", scratch))

    def delete_book(self):
        """Remove a book's index file, after asking."""
        if self.menu_target is None:
            return
        book, _ = self.target(self.menu_target)
        if book is None:
            return
        open_now = (os.path.normcase(book["path"])
                    == os.path.normcase(os.path.abspath(core.DB["path"])))
        others = [b for b in self.books if b["path"] != book["path"]]

        if open_now and not others:
            messagebox.showinfo(
                "Cannot delete",
                f"{book['label']} is the only book. Add another before deleting "
                "this one, or the window would have nothing to search.")
            return
        if self.busy:
            self.set_status("Still answering. Try again when it finishes.")
            return
        if not messagebox.askyesno(
                "Delete this book?",
                f"Delete the index for {book['label']}?\n\n{book['path']}\n\n"
                "This erases the file for good. The PDF it was built from is not "
                "touched, so the book can be indexed again.",
                icon="warning", default="no"):
            return

        # Windows will not unlink a file SQLite still holds open, so move off the
        # book first and let use_index close the old connection.
        if open_now:
            try:
                self.use_index(others[0]["path"])
            except SystemExit as err:
                messagebox.showerror("Cannot delete", str(err))
                return
        try:
            os.remove(book["path"])
        except OSError as err:
            messagebox.showerror("Cannot delete", f"{book['path']}\n\n{err}")
        else:
            self.set_status(f"Deleted {book['label']}.")
        self.refresh_books()

    def use_index(self, path):
        """Point the window at an index file and refresh everything it feeds."""
        core.DB["path"] = path
        previous = getattr(self, "db", None)
        self.db = core.connect()
        if previous is not None:
            previous.close()   # or Windows keeps a lock on the file we just left
        self.remember_book(path)
        self.outline = self.load_outline()
        self.clear()
        self.title(self.book_name())
        self.set_status()
        self.refresh_books()

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

    def search_only(self, terms):
        """Look the words up and show what matched. No API call, no thread.

        Retrieval is a local FTS query, quick enough that going off the main
        thread would only add a flicker.
        """
        self.set_status("Searching…")
        try:
            found = core.retrieve(self.db, terms)
        except Exception as err:
            self.set_status(f"Search failed: {err}")
            return
        self.fill_sources(found, core.query_terms(terms) or "")
        if found:
            self.set_status(f"{len(found)} sections for “{terms}”.")
        else:
            self.excerpt.configure(state="normal")
            self.excerpt.delete("1.0", "end")
            self.excerpt.insert("end", f"Nothing matched “{terms}”.\n\n"
                                "Try the book's own words, or fewer of them.\n", "muted")
            self.excerpt.configure(state="disabled")
            self.set_status("No sections matched.")

    def submit(self, follow=False):
        """Ask a question. A new one clears the window; a follow-up keeps it."""
        question = self.entry.get("1.0", "end").strip()
        if not question or self.busy:
            return
        self.entry.delete("1.0", "end")
        if not self.ai_mode():
            self.search_only(question)
            return
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
        self.showing_cover = False
        pages = (f"p.{src['page_start']}" if src["page_start"] == src["page_end"]
                 else f"pp.{src['page_start']}–{src['page_end']}")
        self.excerpt.configure(state="normal")
        self.excerpt.delete("1.0", "end")
        # Name the book: a page number alone is ambiguous across a collection.
        book = f"{src['book']}  ·  " if src.get("book") else ""
        self.excerpt.insert("end", f"#{src['id']}  {book}{pages}  {src['path']}\n", "head")

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
