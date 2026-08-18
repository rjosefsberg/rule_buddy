# Where to pick up

Branch `webview-ui`. The webview window works, the drive builds, and the frozen
exe runs. Two faults stop it from replacing the Tk window.

## Both faults are fixed

**The lock up.** `evaluate_js` and `run_js` both end in the same place in
`webview/platforms/edgechromium.py`:

    self.webview.Invoke(...)   # synchronous, onto the UI thread
    semaphore.acquire()        # then waits for the callback

pythonnet holds the GIL across that Invoke. Windows runs a modal loop for the
whole time a title bar is dragged, so the UI thread cannot answer it, and no
Python thread can run either. The process locks up, not just the window.

`tell()` now only puts the event on a queue, and the page pulls with `poll()`
every 150ms. Python never calls into the window. Measured with the window held
in the move loop: Python threads keep running, the UI thread keeps answering,
and the page still talks afterwards.

**The centring.** `centred()` in `shell.py` measures the work area, not the
screen, so the task bar is allowed for.

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
