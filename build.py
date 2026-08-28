#!/usr/bin/env python3
"""build.py - make the double-clickable folder that goes on a jump drive.

    python build.py                      everything, key included
    python build.py --strip-key          ship it without the API key
    python build.py --books exalted.db   only these collections
    python build.py --skip-exe           reassemble the folder, no rebuild

Produces RuleBuddy-Drive/, which holds the READ ME and a "Rule Buddy" folder:
the exe, its _internal directory, config.json and books/. All four have to
travel together.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DRIVE = os.path.join(ROOT, "RuleBuddy-Drive")
APP = os.path.join(DRIVE, "Rule Buddy")
WORK = os.path.join(ROOT, "build_out")
NAME = "Rule Buddy"
ICON = os.path.join(ROOT, "src", "rulebuddy", "assets", "rulebuddy.ico")


def say(step):
    print(f"\n=== {step}")


def build_exe():
    """Run PyInstaller over run.py, the entry that works when frozen."""
    say("Building the exe")
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", NAME, "--icon", ICON, "--paths", os.path.join(ROOT, "src"),
        "--collect-all", "pymupdf",
        # The window is HTML, so the page has to travel with the exe. Without
        # this the window opens on a blank white rectangle.
        "--add-data", f"{os.path.join(ROOT, 'src', 'rulebuddy', 'ui')}{os.pathsep}rulebuddy/ui",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pythonnet",
        # shell is imported inside a function, so the analysis can miss it.
        "--hidden-import", "rulebuddy.shell",
        "--distpath", os.path.join(WORK, "dist"),
        "--workpath", os.path.join(WORK, "work"),
        "--specpath", WORK,
        os.path.join(ROOT, "run.py"),
    ]
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        sys.exit("PyInstaller failed. Nothing was assembled.")


def pick_books(only):
    """The collections to ship, as full paths."""
    shelf = os.path.join(ROOT, "books")
    if not os.path.isdir(shelf):
        return []
    names = sorted(n for n in os.listdir(shelf) if n.lower().endswith(".db"))
    if only:
        wanted = {os.path.basename(n).lower() for n in only}
        missing = wanted - {n.lower() for n in names}
        if missing:
            sys.exit(f"Not in books/: {', '.join(sorted(missing))}")
        names = [n for n in names if n.lower() in wanted]
    return [os.path.join(shelf, n) for n in names]


def write_config(strip_key):
    """Copy config.json across, pointing it at the first book we shipped."""
    source = os.path.join(ROOT, "config.json")
    if not os.path.exists(source):
        source = os.path.join(ROOT, "config.example.json")
    with open(source, encoding="utf-8") as handle:
        data = json.load(handle)

    shipped = sorted(n for n in os.listdir(os.path.join(APP, "books"))
                     if n.lower().endswith(".db"))
    if shipped:
        current = os.path.basename(str(data.get("db", "")))
        # Keep the configured book if it made it onto the drive; otherwise the
        # exe would start by complaining about a file that is not there.
        data["db"] = f"books/{current if current in shipped else shipped[0]}"
    data["books_dir"] = "books"
    if strip_key:
        data.pop("api_key", None)

    with open(os.path.join(APP, "config.json"), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    return data.get("db"), bool(data.get("api_key"))


def assemble(books, strip_key):
    say("Assembling the drive folder")
    built = os.path.join(WORK, "dist", NAME)
    if not os.path.isdir(built):
        sys.exit(f"No build at {built}. Run without --skip-exe first.")

    if os.path.isdir(APP):
        shutil.rmtree(APP)
    os.makedirs(os.path.join(APP, "books"), exist_ok=True)
    shutil.copytree(built, APP, dirs_exist_ok=True)
    for book in books:
        shutil.copy(book, os.path.join(APP, "books", os.path.basename(book)))
        print(f"  book: {os.path.basename(book)}")

    # A machine with Edge already has WebView2. Drop the fixed version runtime
    # in WebView2/ at the project root and it travels with the drive, so the
    # window opens on a machine that has neither.
    runtime = os.path.join(ROOT, "WebView2")
    if os.path.isdir(runtime):
        shutil.copytree(runtime, os.path.join(APP, "WebView2"), dirs_exist_ok=True)
        print(f"  WebView2 runtime: {folder_size(runtime):.0f} MB")
    else:
        print("  WebView2 runtime: not carried. The machine must have Edge.")

    db, has_key = write_config(strip_key)
    return db, has_key


def folder_size(path):
    total = sum(os.path.getsize(os.path.join(where, name))
                for where, _, names in os.walk(path) for name in names)
    return total / 1_000_000


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strip-key", action="store_true",
                        help="leave the API key out of the shipped config.json")
    parser.add_argument("--books", nargs="+", metavar="NAME", default=None,
                        help="only these collections from books/ (default: all)")
    parser.add_argument("--skip-exe", action="store_true",
                        help="reuse the last build and only assemble the folder")
    args = parser.parse_args()

    books = pick_books(args.books)
    if not books:
        sys.exit("No collections in books/. Index one first, or pass --books.")
    if not args.skip_exe:
        build_exe()
    db, has_key = assemble(books, args.strip_key)

    say("Done")
    print(f"  folder: {DRIVE}")
    print(f"  size:   {folder_size(DRIVE):.0f} MB")
    print(f"  opens:  {db}")
    if has_key:
        print("\n  The API key is in this folder in plain text. Anyone holding the\n"
              "  drive can spend against it. Use --strip-key to leave it out.")
    print(f"\n  Check it: \"{os.path.join(APP, NAME + '.exe')}\"")


if __name__ == "__main__":
    main()
