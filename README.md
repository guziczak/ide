# ForgeIDE

A complete desktop IDE in a **single Python file** — with a real Git/GitHub client baked in.

No Electron, no plugin zoo, no install ritual. One `ide.py` (~2,650 lines) that
boots its own virtual environment on first run and gives you an editor, a terminal,
and a full Git workflow in one window.

> Built as a single self-contained file on purpose — because the interesting
> constraint isn't "use an IDE", it's "fit one into 2,650 readable lines."

<!-- TODO: drop a screenshot here — it's the single biggest credibility win.
     ![ForgeIDE](docs/screenshot.png)  -->
*(screenshot coming)*

## What it does

- **Code editor** — line numbers, syntax highlighting (Python, JSON, Markdown), find/replace
- **Tabbed files** + a **file-tree browser** with folder navigation and context menus
- **Integrated terminal** — real PTY with ANSI escape handling (Windows via `winpty`)
- **Full Git client** — stage, commit, branch, view history, **squash**, **force-with-lease push**
- **GitHub integration** — authenticate, list / clone / create repositories
- **Diff viewer** for staged and unstaged changes, with file-level commit history
- **Dark theme** (Tokyonight palette via QSS)
- **Self-bootstrapping** — sets up its own venv; settings & recent workspaces persisted

## Stack

Python · [PySide6](https://doc.qt.io/qtforpython/) (Qt) · [`pyte`](https://github.com/selectel/pyte) (terminal emulation) · `winpty` (PTY on Windows). Standard library for the rest.

## Run

```bash
python ide.py
```

First launch bootstraps the virtual environment automatically. Requires Python 3.10+.
Terminal PTY support is currently Windows-only (`winpty`).

## Status

Personal project — actively built. Single-file by design.

## License

<!-- TODO: add a LICENSE file (MIT is the easy, expected choice for a portfolio repo). -->
MIT (planned).
