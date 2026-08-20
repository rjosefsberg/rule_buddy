# Rule Buddy

Ask a tabletop rulebook a question in plain English and get an answer that cites
the section and page it came from.

The point is checkability. Every claim in an answer carries a marker like
`[#412 p.87]`; clicking it opens the exact excerpt the model was given, so you can
read the passage yourself and decide whether the answer holds. Answers are drawn
only from excerpts retrieved out of a local index of your own PDF — the model is
told not to fall back on what it may know about the game.

<!-- A screenshot of the window belongs here. -->

## How it works

A bookmarked PDF is split into sections along its outline, chunked, and written to
a SQLite database with an FTS5 full-text index. A question becomes a keyword query
over that index; the top sections, plus any sections they cross-reference by rule
number, are packed into a prompt and sent to the Claude API. Retrieval is local and
free. Only the excerpts and your question leave your machine, and only when you ask
for a written answer.

## The window

The window is drawn by the system webview, so the interface is HTML and CSS and
the whole back end stays Python. On Windows that is WebView2, which ships with
Microsoft Edge. Five tabs: Search, Ask, Charm Library, Bookmarks, and Import.

## Two modes

**Search** needs no key. It is a local full-text search over your books: results
on the left, the passage itself filling the right. No network call is reachable
from this path — nothing leaves the machine.

**Ask** turns on when a key is present, either in `config.json` or in
`ANTHROPIC_API_KEY`. The tab is a conversation: transcript above the question,
the sections that fed the answer below it, and citations you can click to read
the passage.

## Requirements

- Python 3.14+
- `pywebview`, and WebView2 on Windows. WebView2 ships with Edge, so a machine
  with Edge already has it. A drive that must run anywhere carries the fixed
  version runtime in `WebView2/`; see the build section.
- `pymupdf`, to index a PDF. Searching an index built elsewhere does not need it.
- A bookmarked PDF of the rulebook. The outline is what section splitting relies
  on; a PDF without one will index poorly.
- An [Anthropic API key](https://console.anthropic.com/), optional, for written
  answers.

## Setup

```sh
git clone https://github.com/rjosefsberg/rule_buddy.git
cd rule_buddy
uv sync                      # or: pip install -e .

python -m rulebuddy.indexer index yourbook.pdf --db books/yourbook.db
cp config.example.json config.json    # then put your key in it
python -m rulebuddy
```

`python -m rulebuddy` needs the package on the path, which `uv sync` or
`pip install -e .` does. Without either, start it with `python run.py`, which
puts `src/` on the path itself. That is the entry PyInstaller builds from, so it
is the same code either way.

Indexing a full-size rulebook takes a few minutes. Drop the resulting `.db` in
`books/` and it appears in the sidebar.

## Configuration

`config.json` sits in the application folder — the project root from source, the
folder holding the exe when packaged — and is read at startup:

```json
{
  "api_key": "sk-ant-...",
  "model": "claude-sonnet-5",
  "db": "books/yourbook.db",
  "books_dir": "books"
}
```

`db` is the book opened on startup. `books_dir` is scanned for every other `.db`
to fill the sidebar; relative paths resolve against the application folder.

`ANTHROPIC_API_KEY` in the environment overrides the key in the file, and command
line flags (`--db`, `--model`, `--config`) override the rest. `config.json` is
gitignored; `config.example.json` is the template to copy.

## Using the window

**Search** looks the words up in the index. Questions work best in the book's own
words — "cover ranged attack" beats "can I shoot someone who is hiding", because
retrieval is keyword-driven and the book does not use your phrasing. Results give
the book, the section, and the page. A section stored as several chunks comes back
as one result covering the whole page range. Double-click a result, or press
**Open the PDF at this page**, to open the book in a reader at that page.

**Ask** sends the excerpts to the model. **New question** starts a fresh thread.
**Ask more** keeps the thread and answers over everything found so far. Clicking a
`[#412 p.87]` marker opens that excerpt.

**Charm Library** is a table of one mechanic, read back out of the indexed
sections. Exalted writes every Charm to the same skeleton — Cost, Mins, Type,
Keywords, Duration, Prerequisite Charms — and that is regular enough to parse.
Filter by book, tree, type, keyword, and an Essence ceiling, over a word search.
Press **Build the library** once per collection.

**Bookmarks** edits the outline of any PDF and writes it back into the file. A PDF
with no outline indexes poorly, so this is how you fix one. Read the bookmarks it
has, or build them from its printed contents pages (`4, 5, 8-11`). Shift-click and
Control-click select several rows, and **In** and **Out** move all of them.

**Import** turns a folder of PDFs into one collection. Every PDF is tested first,
and the list says which are ready and why the others are not: a scan, no
bookmarks, a password, or too large. Then name the collection and index the good
ones.

The shelf on the left lists the books in the open collection, and every other
collection under `books_dir`. Clicking a collection switches to it. Right-clicking
a book renames it, adds another book, or removes it.

**Ctrl and plus** or **minus** changes the text size, and **Ctrl and 0** puts it
back.

## Command line

The indexer is usable on its own, without the window or an API key:

```sh
python -m rulebuddy.indexer add supplement.pdf          # add a book to a collection
python -m rulebuddy.indexer books                       # what is in this collection
python -m rulebuddy.indexer drop 2                      # remove book 2
python -m rulebuddy.indexer search "sustained action"   # keyword search
python -m rulebuddy.indexer show 42        # one chunk and its cross-references
python -m rulebuddy.indexer page 271       # sections on a page
python -m rulebuddy.indexer refs 14.3      # sections citing a rule number
python -m rulebuddy.indexer toc            # the outline
python -m rulebuddy.indexer --db books/x.db cover book.pdf   # backfill a cover
```

Every subcommand takes `--db` to pick which index it works on.

## Tests

```sh
python -m unittest discover -s tests
```

Standard library only, and under a second: no PDFs and no window. Indexing a real
book takes a quarter of a minute and needs a copyrighted file that is not in the
repository, and the window is checked by hand. What the tests cover is the
bookkeeping that breaks quietly — the version 1 migration, sections and the full
text index staying in step as books come and go, excerpts carrying the name of
the book they came from, and the key never reaching config.json when it was not
meant to.

## Packaging a standalone build

To hand the app to someone who does not have Python:

```sh
pip install pyinstaller
python build.py
```

The page in `ui/` travels with the exe as data, and `pywebview` and its .NET
loader are collected with it. A machine with Edge already has WebView2; to cover
a machine without it, download the WebView2 **fixed version** runtime cab from
Microsoft, expand it into `WebView2/` at the project root, and the build copies
it onto the drive.

That produces `RuleBuddy-Drive/`, ready to copy onto a jump drive: a READ ME and
a `Rule Buddy` folder holding the exe, its `_internal` directory, `config.json`
and `books/`. All four have to travel together — the exe alone will not start.

```sh
python build.py --strip-key                  leave the API key out
python build.py --books exalted.db           ship only these collections
python build.py --skip-exe                   reassemble without rebuilding
```

The script points the shipped `config.json` at a book that actually made it onto
the drive, so the exe cannot start by complaining about a missing file, and it
says out loud when the key is travelling in plain text.

The build goes through `run.py`, not `rulebuddy/__main__.py`: a frozen entry
script runs as a top-level module with no package context, so the relative
imports inside the package fail there. `run.py` puts the package on the path and
calls `main()` by absolute import. `core.app_dir()` is what lets both forms find
`config.json` and `books/` — the exe's folder when frozen, the project root when
not — and startup failures surface in a dialog rather than on a console that is
not there.

**If the build embeds a real key, treat the folder as a secret.** `config.json`
travels in plain text and anyone holding it can spend against your account.
Rotate the key when the loan ends.

## Layout

```
rule_buddy/
  config.example.json      settings template
  build.py                 makes the jump-drive folder
  tests/                   python -m unittest discover -s tests
  run.py                   entry point PyInstaller builds from
  READ ME FIRST.txt        ships with the drive folder
  books/                   the .db indexes, one per system
  src/rulebuddy/
    __main__.py            python -m rulebuddy
    shell.py               the window: the Api the page calls
    ui/                    index.html, styles.css, app.js
    core.py                retrieval and the API call
    indexer.py             PDF extraction, chunking, index building, the CLI
    contents.py            reads an outline from a printed contents page
    charms.py              reads the Charm library out of the sections
    bookmarks.py           outline parsing for the Bookmarks tab
    assets/rulebuddy.ico
```

Indexes, source PDFs, build output, and the real `config.json` are gitignored.

## Bookmarks

The indexer splits a book along its PDF bookmarks. Most rulebooks ship with
none, and the printed table of contents holds the same information.

The **Bookmarks** tab opens any PDF, indexed or not. It shows the
bookmarks the file already carries, or builds them from the contents page when
you give the page numbers. Titles, levels, pages and entries can all be edited,
and the result is written back into the PDF with an incremental save, so a file
of several hundred megabytes gains kilobytes rather than being rebuilt. Saving is not
instant on a large book, and the window says so: do not open the PDF in a reader
until it reports that the file is closed.

The parser reads the columns from the page numbers, which print in straight
vertical lines, so pages set in one, two or three columns all work. Levels come
from the indent, counted rather than chained, because indents wobble and a
decorated page adds strays. A title too long for its line wraps, and these books
print the page number against the last line, so a row with no number opens the
next entry rather than closing the one before it.

Page numbers become page indexes through the PDF page labels when the file
carries them. A reader shows those labels, which is why the editor shows them
too. Without labels, the printed number is taken as the index.

The same work is available without the window:

```sh
python -m rulebuddy.contents book.pdf --pages 4 5 6 --out outline.json
```

## Collections

A `.db` is a **collection**; each PDF indexed into it is a **book**. One system's
core rulebook and its supplements belong in one collection, because a question
about a rule needs to see the supplement that amends it, and cross-references
resolve across the whole file. Separate systems belong in separate collections —
one FTS index spanning two rules systems will happily answer a question about
Exalted with a passage from Trinity.

```sh
python -m rulebuddy.indexer index core.pdf --db books/exalted.db
python -m rulebuddy.indexer --db books/exalted.db add supplement.pdf
```

Sections are numbered from one sequence across the whole collection, so a
citation like `#412` always names exactly one passage. Because page numbers do
not survive that way — every book has a page 87 — the book's name travels with
each excerpt into the prompt, the excerpt pane, and the answer.

Adding a second book gives the collection a default name of `<first book>
Collection`, which **Rename…** overrides. Re-indexing a PDF already in a
collection replaces that book rather than duplicating it, keeping whatever name
it was given.

An index built before collections existed is migrated the first time it is
opened: its single book, cover and source move into the new tables. Nothing to
run.

## Covers

Indexing renders page one of the PDF and stores it as PNG bytes in a `cover`
table inside the index, so a book stays a single portable file — copy the `.db`
anywhere and its cover travels with it. Indexes built before covers existed
still open fine and simply show no image; give one a cover without a full
re-index using:

```sh
python -m rulebuddy.indexer --db books/yourbook.db cover yourbook.pdf
```

Covers are stored taller than they are shown, from when the window could only
shrink an image by whole-number steps.

## A note on books

The index is built from a PDF you supply. No rulebook text is included in this
repository, and the databases it produces are derivative of a copyrighted book —
keep them to yourself.
