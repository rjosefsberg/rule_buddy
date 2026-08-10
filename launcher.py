#!/usr/bin/env python3
"""launcher.py - entry point for the packaged build.

A double-clicked exe does not get a useful working directory, and config.json
sits next to the exe rather than inside the bundle. This resolves both against
the exe's own folder, then hands off to the normal app.
"""

import os
import sys


def home():
    """The folder the user sees: the exe's folder when frozen, else the source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


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


def main():
    base = home()
    os.chdir(base)

    import rulebook_core as core
    core.DEFAULT_CONFIG = os.path.join(base, "config.json")
    core.DB["path"] = os.path.join(base, "rulebook.db")

    import rulebook_app

    data = core.load_config(core.DEFAULT_CONFIG)
    db = data.get("db") or core.DB["path"]
    if not os.path.isabs(db):
        db = os.path.join(base, db)
    core.DB["path"] = db

    if not os.path.exists(db):
        complain(f"The rulebook index is missing.\n\nExpected it here:\n{db}\n\n"
                 "Copy the whole Rule Buddy folder off the drive and try again.")

    app = rulebook_app.App(db, core.CONFIG["model"])
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as err:
        complain(f"Rule Buddy could not start.\n\n{type(err).__name__}: {err}")
