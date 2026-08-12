#!/usr/bin/env python3
"""Tests for the parts that break quietly.

Deliberately no PDFs and no Tk. Indexing a real book takes a quarter of a minute
and needs a copyrighted file that is not in the repository, and the window is
checked by hand. What is left is the bookkeeping: the schema migration, keeping
the full text index in step with the sections, and the key handling.

    python -m unittest discover -s tests
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))

from rulebuddy import bookmarks, contents, core, indexer   # noqa: E402


def v1_index(path, sections=3):
    """An index in the old one-book-per-file shape, before collections."""
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            path TEXT, title TEXT, number TEXT, level INTEGER,
            page_start INTEGER, page_end INTEGER, part INTEGER, text TEXT,
            styles TEXT);
        CREATE VIRTUAL TABLE sections_fts USING fts5(
            title, path, text, content='sections', content_rowid='id',
            tokenize='porter unicode61');
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE cover (id INTEGER PRIMARY KEY CHECK (id = 1), png BLOB);
    """)
    for i in range(sections):
        db.execute("INSERT INTO sections (path,title,number,level,page_start,"
                   "page_end,part,text,styles) VALUES (?,?,?,?,?,?,?,?,NULL)",
                   (f"Chapter One > Section {i}", f"Section {i}", "", 2,
                    10 + i, 10 + i, 0, f"initiative and combat, passage {i}"))
    db.execute("INSERT INTO sections_fts (rowid,title,path,text)"
               " SELECT id,title,path,text FROM sections")
    db.execute("INSERT INTO meta VALUES ('source',?)", (r"C:\books\Old_Rulebook.pdf",))
    db.execute("INSERT INTO meta VALUES ('pages','686')")
    db.execute("INSERT INTO cover VALUES (1, ?)", (b"not really a png",))
    db.commit()
    db.close()


def add_fake_book(path, title, sections=2, text="initiative and combat"):
    """Put a book into a collection without going anywhere near a PDF."""
    db = sqlite3.connect(path)
    db.executescript(indexer.BASE_SCHEMA)
    core.ensure_schema(db)
    book_id = db.execute("INSERT INTO books (title, source, pages, added, cover)"
                         " VALUES (?,?,?,?,NULL)",
                         (title, rf"C:\books\{title}.pdf", 100, "")).lastrowid
    for i in range(sections):
        db.execute("INSERT INTO sections (path,title,number,level,page_start,"
                   "page_end,part,text,styles,book_id) VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                   (f"{title} > Section {i}", f"Section {i}", "", 2,
                    20 + i, 20 + i, 0, f"{text}, {title} passage {i}", book_id))
    db.execute("INSERT INTO sections_fts (rowid,title,path,text)"
               " SELECT id,title,path,text FROM sections WHERE book_id=?", (book_id,))
    db.commit()
    db.close()
    return book_id


class Migration(unittest.TestCase):
    """A version 1 index has to keep working without being rebuilt."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "old.db")

    def test_v1_becomes_one_book(self):
        v1_index(self.path)
        db = sqlite3.connect(self.path)
        core.ensure_schema(db)
        books = db.execute("SELECT id, title, source, pages, cover FROM books").fetchall()
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0][1], "Old Rulebook")
        self.assertEqual(books[0][3], 686)
        self.assertEqual(books[0][4], b"not really a png")   # cover carried over
        owned = db.execute("SELECT COUNT(*) FROM sections WHERE book_id=1").fetchone()[0]
        self.assertEqual(owned, 3)
        db.close()

    def test_migration_is_not_repeated(self):
        v1_index(self.path)
        db = sqlite3.connect(self.path)
        core.ensure_schema(db)
        core.ensure_schema(db)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0], 1)
        db.close()

    def test_new_file_gets_no_phantom_book(self):
        """A file created from scratch is empty, not old."""
        db = sqlite3.connect(self.path)
        db.executescript(indexer.BASE_SCHEMA)
        core.ensure_schema(db)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0], 0)
        db.close()


class Bookkeeping(unittest.TestCase):
    """Sections, the full text index and the books table move together."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "collection.db")
        self.core_id = add_fake_book(self.path, "Core Book", sections=3)
        self.supp_id = add_fake_book(self.path, "Supplement", sections=2)

    def counts(self):
        db = sqlite3.connect(self.path)
        out = (db.execute("SELECT COUNT(*) FROM sections").fetchone()[0],
               db.execute("SELECT COUNT(*) FROM sections_fts").fetchone()[0],
               db.execute("SELECT COUNT(*) FROM books").fetchone()[0])
        db.close()
        return out

    def test_books_accumulate_without_duplicating_the_index(self):
        self.assertEqual(self.counts(), (5, 5, 2))

    def test_ids_do_not_collide(self):
        """A citation has to name one passage, whatever book it came from."""
        db = sqlite3.connect(self.path)
        ids = [row[0] for row in db.execute("SELECT id FROM sections")]
        db.close()
        self.assertEqual(len(ids), len(set(ids)))

    def test_removing_a_book_takes_its_rows_only(self):
        db = sqlite3.connect(self.path)
        indexer.remove_book(db, self.supp_id)
        db.close()
        self.assertEqual(self.counts(), (3, 3, 1))

    def test_removing_a_book_leaves_search_consistent(self):
        db = sqlite3.connect(self.path)
        indexer.remove_book(db, self.supp_id)
        db.close()
        core.DB["path"] = self.path
        db = core.connect()
        hits = core.retrieve(db, "initiative and combat")
        self.assertTrue(hits)
        self.assertTrue(all(h["book"] == "Core Book" for h in hits))
        db.close()


class Retrieval(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "collection.db")
        add_fake_book(self.path, "Core Book")
        add_fake_book(self.path, "Supplement")
        core.DB["path"] = self.path
        self.db = core.connect()

    def tearDown(self):
        self.db.close()

    def test_hits_say_which_book_they_came_from(self):
        hits = core.retrieve(self.db, "initiative and combat")
        self.assertTrue(hits)
        self.assertEqual({h["book"] for h in hits}, {"Core Book", "Supplement"})

    def test_prompt_names_the_book(self):
        """Page 20 means nothing on its own once two books are in here."""
        hits = core.retrieve(self.db, "initiative and combat")
        prompt = core.build_prompt("how does initiative work?", hits, "")
        self.assertIn("Core Book —", prompt)
        self.assertIn("Supplement —", prompt)

    def test_query_terms_drops_filler(self):
        query = core.query_terms("how does the initiative work in combat")
        self.assertIn('"initiative"', query)
        self.assertNotIn('"the"', query)

    def test_collection_name_falls_back_to_the_first_book(self):
        self.assertEqual(core.collection_name(self.db), "Core Book")
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('name','Exalted Shelf')")
        self.assertEqual(core.collection_name(self.db), "Exalted Shelf")


class Keys(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.json")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"model": "claude-sonnet-5", "db": "books/x.db"}, handle)
        self.was = core.CONFIG["key"]

    def tearDown(self):
        core.CONFIG["key"] = self.was

    def read(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_obvious_rubbish_is_caught_before_a_request(self):
        self.assertTrue(core.looks_like_key("sk-ant-" + "a" * 40))
        self.assertFalse(core.looks_like_key("hunter2"))
        self.assertFalse(core.looks_like_key("sk-ant-short"))
        self.assertFalse(core.looks_like_key(""))

    def test_saving_a_key_leaves_other_settings_alone(self):
        ok, _ = core.save_setting("api_key", "sk-ant-test", path=self.path)
        self.assertTrue(ok)
        data = self.read()
        self.assertEqual(data["api_key"], "sk-ant-test")
        self.assertEqual(data["model"], "claude-sonnet-5")
        self.assertEqual(data["db"], "books/x.db")

    def test_removing_a_key_leaves_other_settings_alone(self):
        core.save_setting("api_key", "sk-ant-test", path=self.path)
        core.save_setting("api_key", None, path=self.path)
        data = self.read()
        self.assertNotIn("api_key", data)
        self.assertEqual(data["model"], "claude-sonnet-5")

    def test_session_only_key_never_reaches_the_file(self):
        core.set_key("sk-ant-session", persist=False)
        self.assertTrue(core.has_key())
        self.assertNotIn("api_key", self.read())

    def test_unwritable_config_is_reported_not_raised(self):
        """The packaged app may sit on read-only media."""
        ok, message = core.save_setting("api_key", "sk-ant-test",
                                        path=os.path.join(self.dir, "nope", "config.json"))
        self.assertFalse(ok)
        self.assertIn("Could not write", message)


def line(x0, y0, text, size=8.0, height=10.0):
    """One line as the parser sees it: (x0, y0, height, size, text)."""
    return (x0, y0, height, size, text)


class ContentsPage(unittest.TestCase):
    """The three faults real books produced, kept from coming back."""

    def test_columns_come_from_the_number_lines(self):
        """Aeon prints three columns. Splitting at the middle cuts one in half."""
        lines = []
        for i in range(4):
            y = 40.0 + i * 12
            lines += [line(58.5, y, f"Left {i}"), line(171.5, y, str(10 + i)),
                      line(196.5, y, f"Middle {i}"), line(309.5, y, str(40 + i)),
                      line(339.0, y, f"Right {i}"), line(447.5, y, str(80 + i))]
        self.assertEqual(contents.number_columns(lines, 504), [171.5, 309.5, 447.5])
        groups = contents.columns_of(lines, 504)
        self.assertEqual(len(groups), 3)
        for group in groups:
            titles = [item for item in group
                      if not contents.BARE_NUMBER.fullmatch(item[4])]
            numbers = [item for item in group
                       if contents.BARE_NUMBER.fullmatch(item[4])]
            self.assertEqual(len(titles), 4)
            self.assertEqual(len(numbers), 4, "a column lost its page numbers")

    def test_a_wrapped_title_joins_the_entry_below_it(self):
        """These books print the number against the last line of a wrapped title."""
        rows = contents.rows_of([
            line(58.5, 40, "Glossary"), line(171.5, 40, "20"),
            line(58.5, 52, "CHAPTER ONE: HISTORY"),
            line(58.5, 64, "& BACKGROUND"), line(171.5, 64, "24"),
        ])
        entries = contents.entries_from_rows(rows)
        self.assertEqual([e["title"] for e in entries],
                         ["Glossary", "CHAPTER ONE: HISTORY & BACKGROUND"])

    def test_a_page_heading_does_not_join_the_first_entry(self):
        """"Table of Contents" sits higher and larger, so it belongs to nobody."""
        rows = contents.rows_of([
            line(200.0, 20, "Table of Contents", size=20.0, height=26.0),
            line(58.5, 90, "Introduction"), line(171.5, 90, "10"),
        ])
        entries = contents.entries_from_rows(rows)
        self.assertEqual([e["title"] for e in entries], ["Introduction"])

    def test_ligatures_are_normalised(self):
        """A search for "fire" must match a title the book set with one glyph."""
        self.assertEqual(contents.clean_title("The ﬁrst Fire"), "The first Fire")

    def test_short_titles_survive(self):
        """Exalted has sections called Sex and War."""
        self.assertTrue(contents.plausible_title("Sex"))
        self.assertTrue(contents.plausible_title("War"))
        self.assertFalse(contents.plausible_title("~~"))
        self.assertFalse(contents.plausible_title(""))


class BookmarkTitles(unittest.TestCase):
    def test_a_line_feed_inside_a_title_is_removed(self):
        """39 of Aeon's 567 bookmarks hold one, and a list row shows one line."""
        self.assertEqual(bookmarks.flatten("Recovery & \nThe Urban Schism"),
                         "Recovery & The Urban Schism")

    def test_flatten_handles_nothing(self):
        self.assertEqual(bookmarks.flatten(None), "")
        self.assertEqual(bookmarks.flatten("   "), "")


if __name__ == "__main__":
    unittest.main()
