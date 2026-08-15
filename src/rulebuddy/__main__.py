#!/usr/bin/env python3
"""__main__.py - entry point for both `python -m rulebuddy` and the packaged exe.

A double-clicked exe does not get a useful working directory, and config.json
sits beside the exe rather than inside the bundle. core.app_dir() resolves that;
this module makes it the working directory and reports startup failures in a
dialog, since a windowed build has no console to print to.
"""

import os
import sys


def complain(message):
    """A windowed build has no console, so failures need a real dialog."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Rule Buddy", message)
        root.destroy()
    except Exception:
        pass
    sys.exit(1)


def first_index(shelf):
    """Any index on the shelf, by name. Returns a full path, or None."""
    try:
        names = sorted(n for n in os.listdir(shelf) if n.lower().endswith(".db"))
    except OSError:
        return None
    return os.path.join(shelf, names[0]) if names else None


def main():
    from . import core

    base = core.app_dir()
    os.chdir(base)
    core.load_config()

    db = core.DB["path"]
    if not os.path.isabs(db):
        db = os.path.join(base, db)
    core.DB["path"] = db

    if not os.path.exists(db):
        # The recorded index can be deleted, renamed, or built somewhere else.
        # The shelf usually holds another one, and opening that beats refusing
        # to start, because the window can open any index once it is up.
        spare = first_index(os.path.join(base, core.CONFIG["books_dir"]))
        if not spare:
            complain(f"The rulebook index is missing.\n\nExpected it here:\n{db}"
                     "\n\nCopy the whole Rule Buddy folder off the drive and "
                     "try again.")
        db = core.DB["path"] = spare

    from .app import App
    App(db, core.CONFIG["model"]).mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as err:
        complain(f"Rule Buddy could not start.\n\n{type(err).__name__}: {err}")
