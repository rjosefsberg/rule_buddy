# Where to pick up

Branch `webview-ui`. The window works, the drive builds, and the frozen exe
runs. Everything the Tk window did is ported.

## Before merging to main

- Rebuild the drive. `RuleBuddy-Drive/` still holds a build from before the
  drag fix, so the exe in it locks up.
- Delete `app.py`, `bookmarks.py`, `importer.py`, `library.py`, and the `--tk`
  branch in `__main__.py`. About 2600 lines. Only when the window has been used
  enough to be sure.

## Known faults

- The Charm parser has only been tested on Exalted. Aeon writes Powers to the
  same skeleton, so it should read them, but nothing has checked.
- A Charm's page is the section's first page, so one late in a long section can
  be a page early.
- 1.6% of Charm blocks are missed: a field over 90 characters, or a name that
  cannot be recovered.

## Facts worth keeping

- Python must never call into the window from a worker thread. `evaluate_js`
  and `run_js` both end in a synchronous cross-thread Invoke in
  `webview/platforms/edgechromium.py`, and pythonnet holds the GIL across it.
  Windows runs a modal loop for as long as a window is dragged, so the UI
  thread cannot answer and the whole process stops. `tell()` queues; the page
  pulls with `poll()`.
- The window must own the main thread. PyCharm's "Run with Python Console"
  starts the script on the console's thread, and the window then paints once
  and freezes. `start()` says so now instead of hanging.
- `evaluate_js` hangs on an expression that returns an array or an object
  direct. Wrap every probe as `JSON.stringify((function(){ ... })())`.
- `uv sync` prunes anything not in `pyproject.toml`.
- `python -m rulebuddy` needs the package installed. `python run.py` does not.
- WebView2 here: 151.0.4129.86, in
  `C:\Program Files (x86)\Microsoft\EdgeWebView\Application\`. The fixed
  version runtime is a separate cab download, not an installer, and goes in
  `WebView2/`.
