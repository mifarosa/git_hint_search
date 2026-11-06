#!/usr/bin/env python3
"""
Search a git repository's history for a hint (deleted files / removed code).

Flow (all menus: Up/Down to move, Space to mark, Enter to confirm, q to cancel):
    1. browse to a folder inside a git repository (search is limited to that folder)
    2. type the hint (file name part or content snippet)
    3. pick the file format(s) to look for (or "Any")
    4. pick results to view or save (recover)

Usage:
    python git_hint_search.py              # fully interactive
    python git_hint_search.py "hint"       # hint as argument
    python git_hint_search.py "hint" -C /path/to/folder

Recovered files are written to ./recovered (never over your working tree).
When stdin is not a terminal, menus fall back to numbered prompts.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

try:
    import readline  # Tab completion for typed paths (Linux/macOS)
except ImportError:
    readline = None

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty

AUTHOR = "Mehmet Faruk Gül"
WEBSITE = "https://mifarosa.com"
GITHUB = "https://github.com/mifarosa"

MAX_BLOB_SIZE = 2 * 1024 * 1024  # skip dangling blobs bigger than 2 MB
OUT_DIR = "recovered"
MENU_PAGE = 12  # visible rows in a menu

# Format guessed from the file extension
EXT_FORMATS = {
    ".py": "Python source", ".js": "JavaScript source", ".ts": "TypeScript source",
    ".java": "Java source", ".vue": "Vue component", ".html": "HTML", ".css": "CSS",
    ".json": "JSON", ".xml": "XML", ".yml": "YAML", ".yaml": "YAML", ".md": "Markdown",
    ".txt": "Plain text", ".sql": "SQL", ".sh": "Shell script", ".properties": "Properties",
    ".csv": "CSV", ".xlsx": "Excel workbook", ".docx": "Word document",
    ".pdf": "PDF document", ".png": "PNG image", ".jpg": "JPEG image", ".jpeg": "JPEG image",
    ".gif": "GIF image", ".svg": "SVG image", ".zip": "ZIP archive", ".jar": "Java archive",
    ".gz": "Gzip archive", ".har": "HAR (HTTP archive, JSON)",
}
ZIP_BASED = {"ZIP archive", "Java archive", "Word document", "Excel workbook"}

# Format guessed from the first bytes (used when there is no file name)
MAGIC_FORMATS = [
    (b"\x89PNG", "PNG image"), (b"\xff\xd8\xff", "JPEG image"), (b"GIF8", "GIF image"),
    (b"%PDF", "PDF document"), (b"PK\x03\x04", "ZIP-based (zip/jar/docx/xlsx)"),
    (b"\x1f\x8b", "Gzip archive"), (b"\x7fELF", "ELF executable"),
    (b"SQLite format 3", "SQLite database"), (b"<?xml", "XML"),
    (b"<!DOCTYPE html", "HTML"), (b"<html", "HTML"),
]


# ---------------------------------------------------------------- git helpers
def git(repo, *args):
    """Run a git command and return stdout as text."""
    res = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return res.stdout


def git_bytes(repo, *args):
    """Run a git command and return (returncode, raw stdout bytes)."""
    res = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    return res.returncode, res.stdout


def format_from_name(path):
    """Guess the format from the file extension only."""
    ext = os.path.splitext(path)[1].lower()
    return EXT_FORMATS.get(ext, f"{ext or 'no extension'} (unknown)")


def format_from_bytes(data, path=None):
    """Guess the format from magic bytes, then text/binary check, then extension."""
    head = data[:512]
    for magic, name in MAGIC_FORMATS:
        if head.startswith(magic) or head.lstrip().lower().startswith(magic.lower()):
            return name
    if b"\x00" in head:
        return "Binary (unknown)"
    if path:
        return format_from_name(path)
    if head.lstrip()[:1] in (b"{", b"["):
        return "Text, looks like JSON"
    return "Text (unknown type)"


def parse_commit(text):
    """'sha|date|author|subject' -> dict."""
    sha, date, author, subject = (text.split("|", 3) + ["", "", ""])[:4]
    return {"sha": sha, "date": date, "author": author, "subject": subject}


# ---------------------------------------------------------------- keyboard + menus
KEYMAP = {"\r": "enter", "\n": "enter", " ": "space", "\x03": "ctrl-c", "k": "up", "j": "down"}


def read_key():
    """Read one key press and return a normalized name (up, down, enter, space, esc, ...)."""
    if os.name == "nt":
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # special key prefix
            return {"H": "up", "P": "down", "G": "home", "O": "end"}.get(msvcrt.getwch(), "")
        return KEYMAP.get(ch, ch)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b"\x1b":
            if select.select([fd], [], [], 0.05)[0]:  # escape sequence (arrow keys)
                seq = os.read(fd, 2)
                return {b"[A": "up", b"[B": "down", b"OA": "up", b"OB": "down",
                        b"[H": "home", b"[F": "end"}.get(seq, "")
            return "esc"  # plain Esc key
        text = ch.decode(errors="ignore")
        return KEYMAP.get(text, text)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def parse_selection(text, total):
    """'1,3,5-7' or 'all' -> sorted list of valid indexes (1-based)."""
    text = text.strip().lower()
    if text in ("a", "all"):
        return list(range(1, total + 1))
    chosen = set()
    for part in text.replace(" ", "").split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                chosen.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            chosen.add(int(part))
    return sorted(n for n in chosen if 1 <= n <= total)


def menu_fallback(title, options, multi):
    """Numbered prompt used when stdin is not a terminal."""
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f" [{i:>2}] {opt}")
    if multi:
        ans = input("Numbers (e.g. 1,3 or 2-4 or all), empty = cancel: ").strip()
        picked = parse_selection(ans, len(options)) if ans else []
        return [n - 1 for n in picked] or None
    ans = input("Number [1]: ").strip()
    if not ans:
        return 0
    return int(ans) - 1 if ans.isdigit() and 1 <= int(ans) <= len(options) else None


def menu(title, options, multi=False, details=None, start=0, summary=True):
    """
    Arrow-key menu.
    Returns the chosen index (single), a list of indexes (multi), or None if cancelled.
    Multi mode: Space marks items; Enter confirms (the highlighted item if none is marked).
    """
    if not sys.stdin.isatty():
        return menu_fallback(title, options, multi)
    if os.name == "nt":
        os.system("")  # enable ANSI escape codes on Windows 10+

    n = len(options)
    cursor, top, marked, drawn, result = min(start, n - 1), 0, set(), 0, None
    keys = ("Up/Down move  Space mark  a all/none  Enter confirm  q cancel" if multi
            else "Up/Down move  Enter select  q cancel")
    out = sys.stdout
    out.write("\x1b[?25l")  # hide the text cursor while the menu is open
    try:
        while True:
            width = max(shutil.get_terminal_size((100, 24)).columns - 1, 20)
            if cursor < top:
                top = cursor
            if cursor >= top + MENU_PAGE:
                top = cursor - MENU_PAGE + 1

            lines = [title, "  " + keys, ""]
            for i in range(top, min(top + MENU_PAGE, n)):
                box = ("[x] " if i in marked else "[ ] ") if multi else ""
                row = f"{'>' if i == cursor else ' '} {box}{options[i]}"[:width]
                lines.append(f"\x1b[7m{row}\x1b[0m" if i == cursor else row)
            lines.append(f"  ({cursor + 1}/{n})" + (f"  marked: {len(marked)}" if multi else ""))
            if details:
                lines.append(("  " + details(cursor))[:width])

            if drawn:
                out.write(f"\x1b[{drawn}A")  # move back to the top of the menu
            out.write("\x1b[J" + "\n".join(lines) + "\n")
            out.flush()
            drawn = len(lines)

            key = read_key()
            if key == "ctrl-c":
                raise KeyboardInterrupt
            if key == "up":
                cursor = (cursor - 1) % n
            elif key == "down":
                cursor = (cursor + 1) % n
            elif key == "home":
                cursor = 0
            elif key == "end":
                cursor = n - 1
            elif key == "space" and multi:
                marked ^= {cursor}
            elif key == "a" and multi:
                marked = set() if len(marked) == n else set(range(n))
            elif key == "enter":
                result = (sorted(marked) or [cursor]) if multi else cursor
                break
            elif key in ("q", "esc"):
                result = None
                break
    finally:
        if drawn:
            out.write(f"\x1b[{drawn}A\x1b[J")  # erase the menu
        out.write("\x1b[?25h")  # show the text cursor again
        out.flush()

    if summary and result is not None:  # leave a one-line summary of the choice
        names = [options[i] for i in (result if multi else [result])]
        shown = names[0] if len(names) == 1 else f"{len(names)} selected"
        print(f"{title} -> {shown}"[:shutil.get_terminal_size((100, 24)).columns - 1])
    return result


# ---------------------------------------------------------------- folder picker
def ask_path_manually():
    """Prompt for a folder path with Tab completion."""
    if readline:
        def complete(text, state):
            matches = [m + (os.sep if os.path.isdir(m) else "")
                       for m in glob.glob(os.path.expanduser(text) + "*")]
            return matches[state] if state < len(matches) else None

        readline.set_completer_delims(" \t\n")
        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")
    try:
        while True:
            path = os.path.abspath(os.path.expanduser(input("Folder path (Tab completes): ").strip()))
            if os.path.isdir(path):
                return path
            print(f"  Not a folder: {path}")
    finally:
        if readline:
            readline.set_completer(None)  # do not affect later prompts


def browse_folder(start):
    """Arrow-key folder browser. Returns the chosen folder, or None if cancelled."""
    cur = os.path.abspath(start)
    while True:
        try:
            subdirs = sorted((d for d in os.listdir(cur)
                              if d != ".git" and os.path.isdir(os.path.join(cur, d))),
                             key=str.lower)
        except OSError:
            subdirs = []  # unreadable folder: only allow going up / selecting
        can_up = os.path.dirname(cur) != cur
        entries = ["[ Select THIS folder ]"]
        if can_up:
            entries.append("..  (go up)")
        entries += [d + os.sep for d in subdirs]
        entries.append("[ Type a path ... ]")

        idx = menu(f"Folder: {cur}", entries, summary=False)
        if idx is None:
            return None
        if idx == 0:
            return cur
        if idx == len(entries) - 1:
            cur = ask_path_manually()
        elif can_up and idx == 1:
            cur = os.path.dirname(cur)
        else:
            cur = os.path.join(cur, subdirs[idx - (2 if can_up else 1)])


def locate(folder):
    """Return (repo_root, scope) for a folder inside a git repo, else None.
    scope is the folder relative to the repo root ('' means the whole repo)."""
    folder = os.path.realpath(folder)
    top = git(folder, "rev-parse", "--show-toplevel").strip()
    if not top:
        return None
    top = os.path.realpath(top)  # git prints forward slashes on Windows
    rel = os.path.relpath(folder, top).replace(os.sep, "/")
    return top, ("" if rel == "." else rel)


def choose_folder(start):
    """Browse until the user picks a folder that is inside a git repository."""
    while True:
        folder = browse_folder(start)
        if folder is None:
            sys.exit("Cancelled.")
        found = locate(folder)
        if found:
            return found
        print(f"  Not inside a git repository: {folder}")
        start = folder


# ---------------------------------------------------------------- format picker
def choose_formats():
    """Pick one or more formats. Returns (selected format names, extension list); empty = any."""
    names = sorted(set(EXT_FORMATS.values()))
    idx = menu("Which file format?", ["Any format (no filter)"] + names, multi=True)
    if idx is None:
        sys.exit("Cancelled.")
    if 0 in idx:
        return set(), []
    chosen = {names[i - 1] for i in idx}
    return chosen, sorted(e for e, f in EXT_FORMATS.items() if f in chosen)


def pathspec(exts, scope=""):
    """git pathspec limiting a command to a folder and/or the chosen extensions."""
    if exts:
        specs = [f"{scope}/*{e}" if scope else f"*{e}" for e in exts]
    else:
        specs = [scope] if scope else []
    return ["--", *specs] if specs else []


# ---------------------------------------------------------------- collectors
def collect_file_events(repo, hint, spec):
    """Files whose PATH matches the hint: when each was added / deleted."""
    items = []
    out = git(
        repo, "log", "--all", "--reverse", "--no-renames",
        "--diff-filter=AD", "--name-status",
        "--pretty=format:<<C>>%h|%ad|%an|%s", "--date=short", *spec,
    )
    commit = None
    for line in out.splitlines():
        if line.startswith("<<C>>"):
            commit = parse_commit(line[5:])
        elif line.strip() and commit:
            status, _, path = line.partition("\t")
            if hint.lower() in path.lower():
                items.append({
                    **commit, "kind": "ADDED" if status == "A" else "DELETED",
                    "path": path, "note": "file name match",
                })
    return items


def collect_content_events(repo, hint, spec):
    """Commits where a line containing the hint was added / removed."""
    out = git(
        repo, "log", "--all", "--reverse", "--no-renames", "-i", f"-S{hint}", "-p",
        "--no-color", "--pretty=format:<<C>>%h|%ad|%an|%s", "--date=short", *spec,
    )
    found = {}  # (sha, path, kind) -> item, so each file/commit appears once
    commit, path = None, None
    for line in out.splitlines():
        if line.startswith("<<C>>"):
            commit = parse_commit(line[5:])
        elif line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
        elif line.startswith(("+++", "---")):
            continue
        elif line[:1] in ("+", "-") and hint.lower() in line.lower() and commit and path:
            kind = "ADDED" if line[0] == "+" else "REMOVED"
            key = (commit["sha"], path, kind)
            if key in found:
                found[key]["count"] += 1
            else:
                found[key] = {**commit, "kind": kind, "path": path,
                              "note": line[1:].strip()[:100], "count": 1}
    return list(found.values())


def collect_blobs(repo, hint, chosen):
    """Unreachable blobs whose content contains the hint (filtered by detected format)."""
    items = []
    out = git(repo, "fsck", "--unreachable", "--no-reflogs", "--no-progress")
    needle = hint.lower().encode("utf-8")
    for line in out.splitlines():
        if not line.startswith("unreachable blob"):
            continue
        sha = line.split()[-1]
        size = git(repo, "cat-file", "-s", sha).strip()
        if not size.isdigit() or int(size) > MAX_BLOB_SIZE:
            continue
        _, data = git_bytes(repo, "cat-file", "-p", sha)
        if needle not in data.lower():
            continue
        fmt = format_from_bytes(data)
        if chosen and fmt not in chosen and not (fmt.startswith("ZIP-based") and chosen & ZIP_BASED):
            continue
        text = data.decode("utf-8", errors="replace")
        first = next((l.strip() for l in text.splitlines() if hint.lower() in l.lower()), "")
        items.append({
            "sha": sha[:10], "full": sha, "date": "", "author": "", "subject": "",
            "kind": "BLOB", "path": "(no name)", "note": first[:100], "format": fmt,
        })
    return items


# ---------------------------------------------------------------- actions
def fetch_content(repo, item):
    """Return the file bytes for an item, or None if not available."""
    if item["kind"] == "BLOB":
        code, data = git_bytes(repo, "cat-file", "-p", item["full"])
    elif item["kind"] == "ADDED":
        code, data = git_bytes(repo, "show", f"{item['sha']}:{item['path']}")
    else:  # DELETED / REMOVED -> version just before the removal
        code, data = git_bytes(repo, "show", f"{item['sha']}^:{item['path']}")
    return data if code == 0 else None


def view_item(repo, item):
    data = fetch_content(repo, item)
    if data is None:
        print("    (could not read this version)")
        return
    if b"\x00" in data[:512]:
        print(f"    (binary, {len(data)} bytes, {format_from_bytes(data, item['path'])})")
        return
    lines = data.decode("utf-8", errors="replace").splitlines()
    for l in lines[:40]:
        print("    | " + l)
    if len(lines) > 40:
        print(f"    | ... ({len(lines) - 40} more lines)")


def save_item(repo, item):
    data = fetch_content(repo, item)
    if data is None:
        print("    (could not read this version)")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.basename(item["path"]) if item["path"] != "(no name)" else "blob.txt"
    dest = os.path.join(OUT_DIR, f"{item['sha'][:8]}_{item['kind'].lower()}_{base}")
    with open(dest, "wb") as f:
        f.write(data)
    print(f"    saved -> {dest}")


def restore_item(repo, scope, item):
    """Put the file back into the project (working tree); never silently overwrite."""
    data = fetch_content(repo, item)
    if data is None:
        print("    (could not read this version)")
        return

    if item["path"] == "(no name)":  # dangling blob: ask for a name, place it in the chosen folder
        name = input("    File name for this blob [recovered_%s.txt]: " % item["sha"][:8]).strip()
        rel = "/".join(p for p in (scope, name or f"recovered_{item['sha'][:8]}.txt") if p)
    else:
        rel = item["path"]

    dest = os.path.realpath(os.path.join(repo, rel))
    if os.path.commonpath([dest, os.path.realpath(repo)]) != os.path.realpath(repo):
        print("    (refusing to write outside the repository)")
        return

    if os.path.exists(dest):
        root, ext = os.path.splitext(dest)
        copy = f"{root}.restored_{item['sha'][:8]}{ext}"
        choice = menu(f"Already exists: {rel}",
                      ["Overwrite it", f"Keep both (write {os.path.basename(copy)})", "Skip"],
                      summary=False)
        if choice in (None, 2):
            print("    skipped")
            return
        if choice == 1:
            dest = copy

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print(f"    restored -> {os.path.relpath(dest, repo)}  (not staged; use git add / git status)")


# ---------------------------------------------------------------- main
def result_label(it):
    fmt = it.get("format") or format_from_name(it["path"])
    when = f"{it['date']} {it['sha']} {it['author']}".strip()
    return f"{it['kind']:<7} {it['path']}  <{fmt}>  {when}"


def result_details(it):
    return " | ".join(x for x in (it["subject"][:40], it.get("note", "")) if x)


def main():
    parser = argparse.ArgumentParser(description="Trace a hint through git history.")
    parser.add_argument("hint", nargs="?", help="file name part or content snippet")
    parser.add_argument("-C", dest="repo", default=None, help="folder to search (skips the picker)")
    args = parser.parse_args()

    # 1) folder (the search is limited to it)
    if args.repo:
        found = locate(os.path.abspath(os.path.expanduser(args.repo)))
        if not found:
            sys.exit(f"Not inside a git repository: {args.repo}")
    else:
        found = choose_folder(os.getcwd())
    repo, scope = found

    # 2) hint
    hint = args.hint or input("\nHint (file name or content): ").strip()
    if not hint:
        sys.exit("Empty hint.")

    # 3) format
    chosen, exts = choose_formats()

    # 4) search (only inside the chosen folder)
    spec = pathspec(exts, scope)
    print(f"Searching {scope or '(whole repo)'} in {repo} for {hint!r} ...")
    items = collect_file_events(repo, hint, spec) + collect_content_events(repo, hint, spec)
    if scope:
        print("  (unreachable blobs are skipped: they have no folder)")
    else:
        items += collect_blobs(repo, hint, chosen)
    if not items:
        sys.exit("No match found.")

    # 5) results
    labels = [result_label(it) for it in items]
    cursor = 0
    while True:
        picked = menu(f"Results for {hint!r} ({len(items)} found)", labels, multi=True,
                      details=lambda i: result_details(items[i]), start=cursor)
        if picked is None:
            break
        cursor = picked[0]
        action = menu("What to do with the selected?",
                      ["View on screen",
                       "Save a copy to ./recovered",
                       "Restore into the project (original location)",
                       "Back"])
        if action in (None, 3):
            continue
        for n in picked:
            it = items[n]
            print(f"\n  {it['kind']} {it['path']} @ {it['sha']}")
            if action == 0:
                view_item(repo, it)
            elif action == 1:
                save_item(repo, it)
            else:
                restore_item(repo, scope, it)
        input("\nPress Enter to continue ...")


def goodbye():
    """Sign-off shown whenever the program ends."""
    print("\nBye bye!")
    print(f"Developed by {AUTHOR}")
    print(f"  Web:    {WEBSITE}")
    print(f"  GitHub: {GITHUB}")


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()  # leave the prompt line cleanly
    except SystemExit as e:
        if isinstance(e.code, str):  # message exits, e.g. "No match found."
            print(e.code)
            exit_code = 1
        else:
            exit_code = e.code or 0
    goodbye()
    sys.exit(exit_code)
