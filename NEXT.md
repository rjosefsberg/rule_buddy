# Where to pick up

Branch `webview-ui`. The webview window works, the drive builds, and the frozen
exe runs. Two faults stop it from replacing the Tk window.

## Fault 1: the window locks up when it is moved

Drag the title bar and the window freezes. Windows has to kill it.

**Leading theory.** `Api.tell()` in `shell.py` calls `window.evaluate_js` from a
worker thread. pywebview marshals that onto the UI thread and waits for the
result. Windows runs a modal message loop while a window is dragged, so the UI
thread cannot answer until the drag ends. The worker holds its lock, the UI
thread is inside the drag loop, and the two wait on each other.

This fits the evidence: the freeze needs a drag, and the window is full of
worker threads that push events (`ask_work`, `index_work`, `add_work`,
`check_folder`).

**What to try, in order.**

1. Reproduce without any worker: open the window, ask nothing, drag it. If it
   still freezes, the theory is wrong and it is pywebview's WinForms host.
2. Reproduce with a worker running: start an Ask, drag during "Waiting for the
   model…". If only this freezes, the theory holds.
3. Replace the synchronous call. pywebview 6 has a fire-and-forget path that
   does not wait for a return value. `tell()` never uses the return value, so it
   should not be waiting for one. Check `window.run_js` in the installed
   version: `python -c "import webview; help(webview.Window.run_js)"`.
4. If there is no such call, do not push from Python at all. Keep a queue on the
   `Api` and let the page pull: a `poll()` method the page calls on a timer, or
   a request that returns when the next event is ready. The page already handles
   every event through `window.onEvent`, so only the transport changes.

## Fault 2: the window does not open centred

`create_window` is called with no `x` and `y`, so pywebview places it. It lands
wrong. Give it a position: read the work area, subtract the window size, halve
it. Watch for a second monitor and for display scaling, which are the usual
reasons the sum comes out wrong.

## After those two

- Check the whole window against the Tk one. The tabs are Search, Ask, Charm
  Library, Bookmarks, Import.
- Delete `app.py`, `bookmarks.py`, `importer.py`, `library.py`, and the `--tk`
  branch in `__main__.py`. Not before.
- The Charm Library only knows Exalted's Charm block. Aeon writes Powers the
  same way, so the same parser should read them; nothing has tested that.
- The library page is the section's first page, so a Charm late in a long
  section can be a page early.
- 1.6% of Charm blocks are still missed: a field over 90 characters, or a name
  that cannot be recovered.

## Facts worth keeping

- `evaluate_js` hangs on an expression that returns an array or an object
  direct. Wrap every probe as `JSON.stringify((function(){ ... })())`.
- `uv sync` prunes anything not in `pyproject.toml`. `pywebview` is declared now;
  it was not, and syncing would have removed it and dropped the app back to Tk.
- `python -m rulebuddy` needs the package installed. `python run.py` does not.
- WebView2 on this machine: 151.0.4129.86, at
  `C:\Program Files (x86)\Microsoft\EdgeWebView\Application\`. The fixed version
  runtime is a separate cab download, not an installer, and goes in `WebView2/`.
