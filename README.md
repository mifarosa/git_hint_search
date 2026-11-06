# git_hint_search

Interactive terminal tool to find deleted files and removed code in git history, then restore them.

You remember *a piece* of a file (a name, a function, a string) but not where it went. Give the tool that hint and it shows **where it was added and where it was removed**, then lets you view it, save a copy, or put it back into your project.

## Features

- **Folder-scoped search**: browse to a folder with the arrow keys; only that folder is searched.
- **Hint search**: matches file names and file contents (case-insensitive, all branches).
- **Timeline**: every match is marked `ADDED`, `REMOVED` or `DELETED`, with commit, date and author.
- **Format filter**: pick one or more file formats (Python, JSON, PDF, ...) or "Any".
- **Unreachable blobs**: finds content no commit points to anymore (e.g. after `git reset --hard`).
- **Safe recovery**: view on screen, save a copy to `./recovered`, or restore to the original location.
- **No dependencies**: pure Python standard library.

## Requirements

- Python 3.8+
- `git` available on your `PATH`

## Usage

```bash
python git_hint_search.py                      # fully interactive
python git_hint_search.py "login"              # hint as argument
python git_hint_search.py "login" -C ./src     # skip the folder picker
```

### Flow

1. **Pick a folder** inside a git repository.
2. **Type a hint** (part of a file name, or a code/text snippet).
3. **Pick file formats** to look for (or "Any format").
4. **Pick results**, then choose what to do with them:

| Action | What it does |
| --- | --- |
| View on screen | Prints the first 40 lines of that version |
| Save a copy to `./recovered` | Writes the file to a separate folder, nothing in your project changes |
| Restore into the project | Writes the file back to its original path |

### Keys

| Key | Action |
| --- | --- |
| `Up` / `Down` (or `k` / `j`) | Move |
| `Space` | Mark / unmark (multi-select menus) |
| `a` | Mark all / none |
| `Enter` | Confirm (acts on the highlighted item if nothing is marked) |
| `q` / `Esc` | Cancel |

When stdin is not a terminal, the menus fall back to numbered prompts.

## Notes

- **Which version is restored:** for `DELETED` and `REMOVED` entries you get the version right before the removal; for `ADDED` entries, the version at the time it was added.
- **Nothing is overwritten silently:** if the target file already exists you can overwrite it, keep both (the old version is written next to it as `name.restored_<commit>.ext`), or skip.
- **Restored files are not staged:** run `git status` / `git add` yourself.
- **Unreachable blobs have no path**, so they are only searched when you pick the repository root, and restoring one asks you for a file name.
- Large unreachable blobs (over 2 MB) are skipped.

## Author

Developed by Mehmet Faruk Gül
[mifarosa.com](https://mifarosa.com) · [github.com/mifarosa](https://github.com/mifarosa)
