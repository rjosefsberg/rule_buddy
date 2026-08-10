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

python rulebook.py index yourbook.pdf
cp config.example.json config.json    # then put your key in it
python rulebook_app.py
```

Indexing a full-size rulebook takes a few minutes and produces `rulebook.db`.

## Configuration

`config.json` sits next to the code and is read at startup:

```json
{
  "api_key": "sk-ant-...",
  "model": "claude-sonnet-5",
  "db": "rulebook.db"
}
```

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

## Command line

`rulebook.py` is usable on its own, without the window or an API key:

```sh
python rulebook.py search "sustained action"   # keyword search
python rulebook.py show 42                     # one chunk and its cross-references
python rulebook.py page 271                    # sections on a page
python rulebook.py refs 14.3                   # sections citing a rule number
python rulebook.py toc                         # the outline
```

## Packaging a standalone build

To hand the app to someone who does not have Python, PyInstaller produces a folder
they can double-click:

```sh
pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed \
  --name "Rule Buddy" --icon rulebuddy.ico --paths . \
  --collect-all pymupdf --hidden-import rulebook \
  --distpath build_out/dist --workpath build_out/work --specpath build_out \
  launcher.py
```

Then copy `config.json` and `rulebook.db` next to the built `Rule Buddy.exe`.
`launcher.py` exists for this build: a double-clicked exe gets an arbitrary working
directory, so it resolves both files against the exe's own folder and reports
failures in a dialog rather than to a console that is not there.

The result is a folder, not a single file — the exe needs the `_internal` directory
beside it. Ship the whole folder together.

**If the build embeds a real key, treat the folder as a secret.** `config.json`
travels in plaintext and anyone holding it can spend against your account. Rotate
the key when the loan ends.

## Layout

| File | |
| --- | --- |
| `rulebook.py` | PDF extraction, chunking, index building, and the CLI |
| `rulebook_core.py` | retrieval and the API call, shared by the app |
| `rulebook_app.py` | the Tk window |
| `launcher.py` | entry point for the packaged build |
| `config.example.json` | settings template |

Indexes (`*.db`), source PDFs, build output, and the real `config.json` are
gitignored.

## A note on books

The index is built from a PDF you supply. No rulebook text is included in this
repository, and the databases it produces are derivative of a copyrighted book —
keep them to yourself.
