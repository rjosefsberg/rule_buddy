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

Without an API key the app still works as a search tool — you get the matching
sections and their text, just no prose answer.

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

The sidebar on the left lists every book in `books_dir`, each with the cover of
the PDF it was built from. Clicking one switches books, which clears the
conversation — excerpt IDs mean something different in another index. Collapse
the panel with **Ctrl+B**, or the chevron in its header.

## Command line

The indexer is usable on its own, without the window or an API key:

```sh
python -m rulebuddy.indexer search "sustained action"   # keyword search
python -m rulebuddy.indexer show 42        # one chunk and its cross-references
python -m rulebuddy.indexer page 271       # sections on a page
python -m rulebuddy.indexer refs 14.3      # sections citing a rule number
python -m rulebuddy.indexer toc            # the outline
python -m rulebuddy.indexer --db books/x.db cover book.pdf   # backfill a cover
```

Every subcommand takes `--db` to pick which index it works on.

## Packaging a standalone build

To hand the app to someone who does not have Python, PyInstaller produces a folder
they can double-click:

```sh
pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed \
  --name "Rule Buddy" --icon src/rulebuddy/assets/rulebuddy.ico --paths src \
  --collect-all pymupdf \
  --distpath build_out/dist --workpath build_out/work --specpath build_out \
  run.py
```

Then copy `config.json` and a `books/` folder next to the built `Rule Buddy.exe`.

The build goes through `run.py`, not `rulebuddy/__main__.py`: a frozen entry
script runs as a top-level module with no package context, so the relative
imports inside the package fail there. `run.py` puts the package on the path and
calls `main()` by absolute import. `core.app_dir()` is what lets both forms find
`config.json` and `books/` — the exe's folder when frozen, the project root when
not — and startup failures surface in a dialog rather than on a console that is
not there.

The result is a folder, not a single file — the exe needs the `_internal` directory
beside it. Ship the whole folder together.

**If the build embeds a real key, treat the folder as a secret.** `config.json`
travels in plaintext and anyone holding it can spend against your account. Rotate
the key when the loan ends.

## Layout

```
rule_buddy/
  config.example.json      settings template
  run.py                   entry point PyInstaller builds from
  books/                   the .db indexes, one per system
  src/rulebuddy/
    __main__.py            python -m rulebuddy
    app.py                 the Tk window
    core.py                retrieval and the API call
    indexer.py             PDF extraction, chunking, index building, the CLI
    assets/rulebuddy.ico
```

Indexes, source PDFs, build output, and the real `config.json` are gitignored.

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
