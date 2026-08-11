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

## Two modes

**Search only** is the default and needs no key. It is a local full-text search
over your books: results on the left, the passage itself filling the right. No
network call is reachable from this path — nothing leaves the machine.

**Written answers** turn on when a key is present, either in `config.json`, in
`ANTHROPIC_API_KEY`, or typed into `File → Set API key…`. The window becomes a
conversation: transcript on the left, sections and their text on the right,
citations you can click.

A key is checked against the API before it is accepted, so a typo fails at the
dialog rather than at your first question. The dialog offers to save it to
`config.json`; leave that off on a shared machine and it lasts only for the
session. `Mode → Written answers (AI)` turns answers off again without
discarding the key, and `File → Remove API key…` drops it, optionally deleting
it from the file.

## Requirements

- Python 3.14+ with Tk (`sudo apt install python3-tk` on Debian/Ubuntu; already
  present in python.org and Windows installs)
- A bookmarked PDF of the rulebook. The outline is what section splitting relies
  on; a PDF without one will index poorly.
- An [Anthropic API key](https://console.anthropic.com/), optional, for written
  answers.

## Setup

```sh
git clone https://github.com/rjosefsberg/rule_buddy.git
cd rule_buddy
uv sync                      # or: pip install pymupdf

python -m rulebuddy.indexer index yourbook.pdf --db books/yourbook.db
cp config.example.json config.json    # then put your key in it
python -m rulebuddy
```

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

Type a question and press **Ask**.

Questions work best in the book's own words — "cover ranged attack" beats "can I
shoot someone who is hiding", because retrieval is keyword-driven and the book does
not use your phrasing.

- **New question** starts a fresh thread.
- **Ask more** keeps the current thread and answers over everything found so far.
- Clicking a `[#412 p.87]` marker opens that excerpt.

The sidebar on the left lists every collection in `books_dir`, each with a cover.
Clicking one switches to it, which clears the conversation — excerpt IDs mean
something different in another index. Collapse the panel with **Ctrl+B**, or the
chevron in its header.

Right-clicking a row opens a menu that acts on the row under the pointer without
changing which collection is open:

- **Rename…** — a collection, or a single book inside one
- **Add a book to this collection…** — index another PDF into it
- **Reimport from PDF…** — rebuild every book from source, or just the one clicked
- **Remove this book…** — take one book out, leaving the rest
- **Delete collection…** — erase the index file

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

Standard library only, and under a second: no PDFs and no Tk. Indexing a real
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
    app.py                 the Tk window
    core.py                retrieval and the API call
    indexer.py             PDF extraction, chunking, index building, the CLI
    assets/rulebuddy.ico
```

Indexes, source PDFs, build output, and the real `config.json` are gitignored.

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

Covers are stored taller than they are shown, because Tk can only shrink an
image by whole-number steps.

## A note on books

The index is built from a PDF you supply. No rulebook text is included in this
repository, and the databases it produces are derivative of a copyrighted book —
keep them to yourself.
