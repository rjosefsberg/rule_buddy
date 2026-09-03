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
from rulebuddy.exalted import charms   # noqa: E402

try:                                    # the window needs pywebview installed
    from rulebuddy import shell         # noqa: E402
except ImportError:
    shell = None


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


class ContentsPageNumbers(unittest.TestCase):
    def test_a_comma_list(self):
        self.assertEqual(bookmarks.parse_pages("4, 5, 6"), [4, 5, 6])

    def test_a_range(self):
        self.assertEqual(bookmarks.parse_pages("8-11"), [8, 9, 10, 11])

    def test_a_list_and_a_range_together(self):
        self.assertEqual(bookmarks.parse_pages("4, 5, 8-11"),
                         [4, 5, 8, 9, 10, 11])

    def test_spaces_still_separate(self):
        """The field took spaces before, and a saved habit must not break."""
        self.assertEqual(bookmarks.parse_pages("4 5 6"), [4, 5, 6])

    def test_a_repeat_is_read_once(self):
        self.assertEqual(bookmarks.parse_pages("5, 4-6"), [4, 5, 6])

    def test_an_en_dash_is_a_range(self):
        self.assertEqual(bookmarks.parse_pages("8–9"), [8, 9])

    def test_nothing_gives_nothing(self):
        self.assertEqual(bookmarks.parse_pages(""), [])
        self.assertEqual(bookmarks.parse_pages("  ,  "), [])

    def test_a_backwards_range_is_refused(self):
        with self.assertRaises(ValueError):
            bookmarks.parse_pages("11-8")

    def test_a_huge_range_is_refused(self):
        with self.assertRaises(ValueError):
            bookmarks.parse_pages("1-500")

    def test_words_are_refused(self):
        with self.assertRaises(ValueError):
            bookmarks.parse_pages("four")


CORE_BOOK = """## Archery
Blood Without Balance
Cost: 3m; Mins: Archery 4, Essence 1
Type: Reflexive
Keywords: Decisive-only
Duration: Instant
Prerequisite Charms: Sight Without Eyes
Drawing upon the perfect moment to shoot, the Solar sees nothing but her target.
Force Without Fire
Cost: 3m; Mins: Archery 4, Essence 2
Type: Supplemental
Keywords: Withering-only, Mute
Duration: Instant
Prerequisite Charms: None
The Solar nocks an arrow with purpose.
"""

RUN_ON_BOOK = """## Athletics
Might of the Maiden Cost: 3m; Mins: Athletics 1, Essence 1 Type: Supplemental Keywords: Decisive-only Duration: Instant Prerequisite Charms: None
Ten Sheaves' blessing has magnified her strength.
"""


@unittest.skipIf(shell is None, "pywebview is not installed")
class Rendering(unittest.TestCase):
    """shell.render lays the style runs over the stored text, as HTML."""

    def test_a_paragraph_and_a_heading(self):
        html = shell.render("## Archery\nShe shoots.", None)
        self.assertEqual(html, "<h3>Archery</h3><p>She shoots.</p>")

    def test_a_run_becomes_a_tag(self):
        html = shell.render("Cost: 3m", '[[0,5,"b"]]')
        self.assertEqual(html, "<p><b>Cost:</b> 3m</p>")

    def test_a_run_never_crosses_a_line(self):
        """A tag opened on one line and closed on the next would not nest."""
        html = shell.render("one\ntwo", '[[0,7,"b"]]')
        self.assertEqual(html, "<p><b>one</b></p><p><b>two</b></p>")

    def test_the_heading_marker_moves_its_runs(self):
        html = shell.render("## Name", '[[3,7,"b"]]')
        self.assertEqual(html, "<h3><b>Name</b></h3>")

    def test_markup_in_the_book_is_escaped(self):
        self.assertEqual(shell.render("a <b> & c", None), "<p>a &lt;b&gt; &amp; c</p>")

    def test_blank_lines_are_dropped(self):
        self.assertEqual(shell.render("one\n\n\ntwo", None), "<p>one</p><p>two</p>")

    def test_nothing_renders_as_nothing(self):
        self.assertEqual(shell.render("", None), "")
        self.assertEqual(shell.render(None, None), "")


@unittest.skipIf(shell is None, "pywebview is not installed")
class FileNames(unittest.TestCase):
    def test_a_collection_name_becomes_a_file_name(self):
        self.assertEqual(shell.safe_filename('My: Books/2024'), "My Books 2024")

    def test_a_name_of_nothing_still_names_something(self):
        self.assertEqual(shell.safe_filename("   "), "Collection")
        self.assertEqual(shell.safe_filename("..."), "Collection")

    def test_a_long_name_is_cut(self):
        self.assertEqual(len(shell.safe_filename("x" * 200)), 80)


class JoiningChunks(unittest.TestCase):
    """A long section is stored in overlapping chunks and read back as one."""

    def test_the_overlap_is_measured_in_lines(self):
        self.assertEqual(core.overlap(["a", "b", "c"], ["b", "c", "d"]), 2)
        self.assertEqual(core.overlap(["a", "b"], ["c"]), 0)
        self.assertEqual(core.overlap([], ["a"]), 0)

    def test_the_shared_lines_are_printed_once(self):
        pieces = [{"id": 1, "page_start": 1, "page_end": 2,
                   "text": "one\ntwo\nthree", "styles": None},
                  {"id": 2, "page_start": 2, "page_end": 3,
                   "text": "two\nthree\nfour", "styles": None}]
        members = [dict(pieces[0], rank=0, cited=False)]
        out = core.joined(members, pieces)
        self.assertEqual(out["text"], "one\ntwo\nthree\nfour")
        self.assertEqual(out["page_start"], 1)
        self.assertEqual(out["page_end"], 3)
        self.assertEqual(out["members"], [1, 2])

    def test_the_styles_still_mark_the_same_words(self):
        pieces = [{"id": 1, "page_start": 1, "page_end": 1,
                   "text": "alpha\nbeta", "styles": '[[0,5,"b"]]'},
                  {"id": 2, "page_start": 1, "page_end": 2,
                   "text": "beta\ngamma", "styles": '[[5,10,"i"]]'}]
        members = [dict(pieces[0], rank=0, cited=False)]
        out = core.joined(members, pieces)
        runs = json.loads(out["styles"])
        self.assertEqual(out["text"][runs[0][0]:runs[0][1]], "alpha")
        self.assertEqual(out["text"][runs[1][0]:runs[1][1]], "gamma")


class CharmParsing(unittest.TestCase):
    """Exalted sets the same block two ways, and both have to read."""

    def test_a_book_that_gives_each_field_a_line(self):
        found = charms.parse(CORE_BOOK, group="Archery")
        self.assertEqual([c["name"] for c in found],
                         ["Blood Without Balance", "Force Without Fire"])
        first = found[0]
        self.assertEqual(first["cost"], "3m")
        self.assertEqual(first["ability"], "Archery")
        self.assertEqual(first["rating"], 4)
        self.assertEqual(first["essence"], 1)
        self.assertEqual(first["type"], "Reflexive")
        self.assertEqual(first["keywords"], "Decisive-only")
        self.assertEqual(first["prereqs"], "Sight Without Eyes")
        self.assertIn("perfect moment", first["text"])

    def test_a_book_that_runs_the_block_onto_one_line(self):
        found = charms.parse(RUN_ON_BOOK, group="Athletics")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "Might of the Maiden")
        self.assertEqual(found[0]["type"], "Supplemental")
        self.assertEqual(found[0]["essence"], 1)

    def test_none_means_no_keywords(self):
        found = charms.parse(CORE_BOOK)
        self.assertEqual(found[1]["prereqs"], "")
        self.assertEqual(found[1]["keywords"], "Withering-only, Mute")

    def test_the_body_stops_at_the_next_charm(self):
        found = charms.parse(CORE_BOOK)
        self.assertNotIn("Force Without Fire", found[0]["text"])

    def test_a_ligature_reads_as_letters(self):
        block = ("Nine Ways\nCost: 1m; Mins: Dodge 2, Essence 1\n"
                 "Type: Reﬂexive\nKeywords: None\nDuration: Instant\n"
                 "Prerequisite Charms: None\nShe moves.\n")
        self.assertEqual(charms.parse(block)[0]["type"], "Reflexive")

    def test_a_comma_inside_brackets_does_not_split_a_keyword(self):
        self.assertEqual(charms.split_list("Keystone (Perception, Wits), Mute"),
                         ["Keystone (Perception, Wits)", "Mute"])

    def test_a_sidebar_between_fields_is_refused(self):
        """A field that ate a line break would swallow the page under it."""
        broken = ("Something\nCost: 3m; Mins: Archery 4, Essence 1\n"
                  "Type: Reflexive\n" + "A long sidebar. " * 12 +
                  "\nKeywords: None\nDuration: Instant\n"
                  "Prerequisite Charms: None\n")
        self.assertEqual(charms.parse(broken), [])

    def test_the_tree_comes_off_the_section_path(self):
        self.assertEqual(charms.group_of("Chapter Six: Charms > Archery"),
                         "Archery")
        self.assertEqual(charms.group_of("Chapter Six > Martial Arts Charms"),
                         "Martial Arts")

    def test_mins_without_an_ability(self):
        ability, rating, essence = charms.read_mins("Essence 3")
        self.assertEqual((ability, rating, essence), ("", 0, 3))


class PrivateUseLetters(unittest.TestCase):
    """Exigents sets Charm names in a font that hides its small caps in the PUA."""

    def test_small_caps_come_back_as_letters(self):
        raw = ("W\U000f0069\U000f0063\U000f006b\U000f0065\U000f0064 "
               "H\U000f0065\U000f0061\U000f0072\U000f0074\U000f0062"
               "\U000f0072\U000f0065\U000f0061\U000f006b "
               "E\U000f0070\U000f0069\U000f0070\U000f0068\U000f0061"
               "\U000f006e\U000f0079")
        self.assertEqual(core.unpua(raw), "Wicked Heartbreak Epiphany")

    def test_the_length_does_not_change(self):
        """The style codes address the text by offset, so it must not move."""
        raw = "T\U000f0061\U000f0073\U000f0074\U000f0065"
        self.assertEqual(len(core.unpua(raw)), len(raw))

    def test_ordinary_text_is_untouched(self):
        self.assertEqual(core.unpua("Taste of the Heart"), "Taste of the Heart")
        self.assertEqual(core.unpua(""), "")


if __name__ == "__main__":
    unittest.main()
