#!/usr/bin/env python3
"""
Single-file Python IDE with built-in Git and GitHub workflows.

Source layout rule: this project is intentionally kept as one file: ide.py.
The file bootstraps and re-runs itself inside a virtual environment stored in
the user's data directory, so the source folder can remain clean.
"""

from __future__ import annotations

import dataclasses
import json
import os
import platform
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterable


APP_NAME = "ForgeIDE"
APP_VERSION = "0.1.0"


def required_runtime_packages() -> tuple[tuple[str, str], ...]:
    packages = [("PySide6-Essentials>=6.7,<7", "PySide6")]
    if os.name == "nt":
        packages.append(("pywinpty>=2,<4", "winpty"))
    return tuple(packages)


def app_data_dir() -> Path:
    override = os.environ.get("FORGEIDE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def venv_dir() -> Path:
    override = os.environ.get("FORGEIDE_VENV_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return app_data_dir() / "venv"


def in_virtualenv() -> bool:
    return getattr(sys, "base_prefix", sys.prefix) != sys.prefix or hasattr(sys, "real_prefix")


def venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def bootstrap_venv_if_needed() -> None:
    if os.environ.get("FORGEIDE_SKIP_VENV") == "1":
        return
    if in_virtualenv():
        return

    target_venv = venv_dir()
    target_python = venv_python(target_venv)
    if not target_python.exists():
        target_venv.parent.mkdir(parents=True, exist_ok=True)
        print(f"{APP_NAME}: creating virtual environment at {target_venv}")
        subprocess.check_call([sys.executable, "-m", "venv", str(target_venv)])

    print(f"{APP_NAME}: restarting inside virtual environment")
    os.execv(str(target_python), [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def ensure_gui_dependencies() -> None:
    for package, import_name in required_runtime_packages():
        try:
            __import__(import_name)
        except ModuleNotFoundError:
            print(f"{APP_NAME}: installing {package} in the virtual environment")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])


if __name__ == "__main__":
    bootstrap_venv_if_needed()
    ensure_gui_dependencies()


from PySide6.QtCore import (  # noqa: E402
    QByteArray,
    QDir,
    QEvent,
    QModelIndex,
    QProcess,
    QRegularExpression,
    QSize,
    Qt,
    Signal,
    QTimer,
)
from PySide6.QtGui import (  # noqa: E402
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QSyntaxHighlighter,
)
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


def quote_command(args: Iterable[str]) -> str:
    values = [str(arg) for arg in args]
    if os.name == "nt":
        return subprocess.list2cmdline(values)
    return shlex.join(values)


def executable_exists(name: str) -> bool:
    return shutil.which(name) is not None


@dataclasses.dataclass(frozen=True)
class CommandResult:
    args: list[str]
    cwd: Path | None
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined_output(self) -> str:
        text = "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)
        return text.strip()


def run_command(args: Iterable[str], cwd: Path | None = None, timeout: int = 60) -> CommandResult:
    arg_list = [str(arg) for arg in args]
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    try:
        completed = subprocess.run(
            arg_list,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **kwargs,
        )
        return CommandResult(arg_list, cwd, completed.returncode, completed.stdout, completed.stderr)
    except FileNotFoundError as exc:
        return CommandResult(arg_list, cwd, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(arg_list, cwd, 124, stdout, stderr or f"Timed out after {timeout} seconds.")


class SettingsStore:
    def __init__(self) -> None:
        self.path = app_data_dir() / "settings.json"
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"{APP_NAME}: could not save settings to {self.path}: {exc}", file=sys.stderr)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def add_recent_workspace(self, path: Path) -> None:
        value = str(path)
        recent = [item for item in self.data.get("recent_workspaces", []) if item != value]
        recent.insert(0, value)
        self.data["recent_workspaces"] = recent[:10]
        self.save()


@dataclasses.dataclass(frozen=True)
class GitFileStatus:
    path: str
    index: str
    worktree: str
    old_path: str | None = None

    @property
    def code(self) -> str:
        return f"{self.index}{self.worktree}"

    @property
    def is_untracked(self) -> bool:
        return self.index == "?" and self.worktree == "?"

    @property
    def is_staged(self) -> bool:
        return self.index not in (" ", "?")

    @property
    def is_unstaged(self) -> bool:
        return self.worktree not in (" ", "?")

    @property
    def label(self) -> str:
        words: list[str] = []
        mapping = {
            "M": "modified",
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "U": "conflict",
            "?": "untracked",
            "!": "ignored",
        }
        for char in (self.index, self.worktree):
            word = mapping.get(char)
            if word and word not in words:
                words.append(word)
        return ", ".join(words) if words else "clean"


def parse_porcelain_status(output: str) -> list[GitFileStatus]:
    statuses: list[GitFileStatus] = []
    for line in output.splitlines():
        if not line or len(line) < 3:
            continue
        index = line[0]
        worktree = line[1]
        path_text = line[3:]
        old_path: str | None = None
        if " -> " in path_text:
            old_path, path_text = path_text.split(" -> ", 1)
        statuses.append(GitFileStatus(path=path_text, index=index, worktree=worktree, old_path=old_path))
    return statuses


class GitService:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace

    def set_workspace(self, workspace: Path | None) -> None:
        self.workspace = workspace

    def _cwd(self) -> Path | None:
        return self.workspace if self.workspace and self.workspace.exists() else None

    def run(self, *args: str, timeout: int = 60) -> CommandResult:
        return run_command(["git", *args], cwd=self._cwd(), timeout=timeout)

    def is_available(self) -> bool:
        return executable_exists("git")

    def is_repo(self) -> bool:
        result = self.run("rev-parse", "--is-inside-work-tree", timeout=10)
        return result.ok and result.stdout.strip() == "true"

    def init_repo(self) -> CommandResult:
        result = self.run("init", "-b", "main", timeout=60)
        if result.ok:
            return result
        fallback = self.run("init", timeout=60)
        if fallback.ok:
            self.run("branch", "-M", "main", timeout=30)
        return fallback

    def repository_root(self) -> Path | None:
        result = self.run("rev-parse", "--show-toplevel", timeout=10)
        if not result.ok:
            return None
        return Path(result.stdout.strip())

    def current_branch(self) -> str:
        result = self.run("branch", "--show-current", timeout=10)
        if result.ok and result.stdout.strip():
            return result.stdout.strip()
        result = self.run("rev-parse", "--short", "HEAD", timeout=10)
        return result.stdout.strip() if result.ok else "-"

    def status(self) -> list[GitFileStatus]:
        result = self.run("status", "--porcelain=v1", timeout=20)
        if not result.ok:
            return []
        return parse_porcelain_status(result.stdout)

    def has_changes(self) -> bool:
        return bool(self.status())

    def has_commits(self) -> bool:
        result = self.run("rev-parse", "--verify", "HEAD", timeout=10)
        return result.ok

    def branches(self) -> list[str]:
        result = self.run("branch", "--format=%(refname:short)", timeout=20)
        if not result.ok:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def remotes(self) -> list[str]:
        result = self.run("remote", timeout=20)
        if not result.ok:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def has_remote(self, name: str = "origin") -> bool:
        return name in self.remotes()

    def upstream(self) -> str | None:
        result = self.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=10)
        if not result.ok:
            return None
        value = result.stdout.strip()
        return value or None

    def config_get(self, key: str) -> str:
        result = self.run("config", "--get", key, timeout=10)
        return result.stdout.strip() if result.ok else ""

    def config_set_local(self, key: str, value: str) -> CommandResult:
        return self.run("config", "--local", key, value, timeout=20)

    def diff(self, path: str | None = None) -> CommandResult:
        args = ["diff"]
        if path:
            args.extend(["--", path])
        return self.run(*args, timeout=60)

    def staged_diff(self, path: str | None = None) -> CommandResult:
        args = ["diff", "--cached"]
        if path:
            args.extend(["--", path])
        return self.run(*args, timeout=60)

    def stage(self, path: str | None = None) -> CommandResult:
        if path:
            return self.run("add", "--", path, timeout=60)
        return self.run("add", "-A", timeout=60)

    def unstage(self, path: str | None = None) -> CommandResult:
        if path:
            return self.run("restore", "--staged", "--", path, timeout=60)
        return self.run("reset", timeout=60)

    def discard(self, status: GitFileStatus) -> CommandResult:
        if status.is_untracked:
            return self.run("clean", "-f", "--", status.path, timeout=60)
        return self.run("restore", "--worktree", "--", status.path, timeout=60)

    def commit(self, message: str) -> CommandResult:
        return self.run("commit", "-m", message, timeout=120)

    def commit_all(self, message: str) -> CommandResult:
        stage_result = self.stage()
        if not stage_result.ok:
            return stage_result
        return self.commit(message)

    def checkout(self, branch: str) -> CommandResult:
        return self.run("checkout", branch, timeout=60)

    def new_branch(self, branch: str) -> CommandResult:
        return self.run("checkout", "-b", branch, timeout=60)

    def pull_rebase(self) -> CommandResult:
        return self.run("pull", "--rebase", timeout=600)

    def push_current(self, set_upstream: bool = False) -> CommandResult:
        branch = self.current_branch()
        if set_upstream and branch and branch != "-":
            return self.run("push", "-u", "origin", branch, timeout=600)
        return self.run("push", timeout=600)


@dataclasses.dataclass(frozen=True)
class GitHubRepo:
    name_with_owner: str
    description: str
    is_private: bool
    url: str
    updated_at: str


class GitHubService:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace

    def set_workspace(self, workspace: Path | None) -> None:
        self.workspace = workspace

    def is_available(self) -> bool:
        return executable_exists("gh")

    def auth_status(self) -> CommandResult:
        return run_command(["gh", "auth", "status"], cwd=self.workspace, timeout=30)

    def current_user(self) -> tuple[str, str, CommandResult]:
        result = run_command(["gh", "api", "user", "--jq", "{login: .login, id: .id}"], cwd=self.workspace, timeout=60)
        if not result.ok:
            return "", "", result
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            bad = CommandResult(result.args, result.cwd, 1, result.stdout, f"Invalid JSON from gh: {exc}")
            return "", "", bad
        login = str(data.get("login") or "").strip()
        user_id = str(data.get("id") or "").strip()
        return login, user_id, result

    def list_repos(self, limit: int = 50) -> tuple[list[GitHubRepo], CommandResult]:
        fields = "nameWithOwner,description,isPrivate,url,updatedAt"
        result = run_command(
            ["gh", "repo", "list", "--limit", str(limit), "--json", fields],
            cwd=self.workspace,
            timeout=90,
        )
        if not result.ok:
            return [], result
        try:
            raw_items = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            bad = CommandResult(result.args, result.cwd, 1, result.stdout, f"Invalid JSON from gh: {exc}")
            return [], bad
        repos = [
            GitHubRepo(
                name_with_owner=str(item.get("nameWithOwner") or ""),
                description=str(item.get("description") or ""),
                is_private=bool(item.get("isPrivate")),
                url=str(item.get("url") or ""),
                updated_at=str(item.get("updatedAt") or ""),
            )
            for item in raw_items
        ]
        return repos, result

    def clone_repo(self, name_with_owner: str, parent_dir: Path) -> tuple[Path, CommandResult]:
        target = parent_dir / name_with_owner.split("/", 1)[-1]
        result = run_command(["gh", "repo", "clone", name_with_owner, str(target)], cwd=parent_dir, timeout=600)
        return target, result

    def create_repo(self, name: str, source: Path, private: bool, push: bool) -> CommandResult:
        visibility = "--private" if private else "--public"
        args = ["gh", "repo", "create", name, "--source", str(source), "--remote", "origin", visibility]
        if push:
            args.append("--push")
        return run_command(args, cwd=source, timeout=600)


class SyntaxHighlighter(QSyntaxHighlighter):
    def __init__(self, document: Any, suffix: str) -> None:
        super().__init__(document)
        self.suffix = suffix.lower()
        self.rules = self._build_rules()

    @staticmethod
    def fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))
        if bold:
            text_format.setFontWeight(QFont.Weight.Bold)
        if italic:
            text_format.setFontItalic(True)
        return text_format

    def _build_rules(self) -> list[tuple[QRegularExpression, QTextCharFormat]]:
        keyword = self.fmt("#7aa2f7", bold=True)
        string = self.fmt("#9ece6a")
        number = self.fmt("#ff9e64")
        comment = self.fmt("#7f8796", italic=True)
        key = self.fmt("#2ac3de")
        heading = self.fmt("#e0af68", bold=True)

        if self.suffix == ".py":
            keywords = [
                "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class",
                "continue", "def", "del", "elif", "else", "except", "finally", "for", "from",
                "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass",
                "raise", "return", "try", "while", "with", "yield",
            ]
            return [
                (QRegularExpression(r"\b(" + "|".join(keywords) + r")\b"), keyword),
                (QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string),
                (QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), string),
                (QRegularExpression(r"\b\d+(\.\d+)?\b"), number),
                (QRegularExpression(r"#[^\n]*"), comment),
            ]
        if self.suffix in (".json", ".jsonc"):
            return [
                (QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"(?=\s*:)'), key),
                (QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), string),
                (QRegularExpression(r"\b(true|false|null)\b"), keyword),
                (QRegularExpression(r"-?\b\d+(\.\d+)?\b"), number),
            ]
        if self.suffix in (".md", ".markdown"):
            return [
                (QRegularExpression(r"^#{1,6}\s.*$"), heading),
                (QRegularExpression(r"`[^`]+`"), string),
                (QRegularExpression(r"^\s*[-*+]\s+"), keyword),
            ]
        return [
            (QRegularExpression(r"#[^\n]*"), comment),
        ]

    def highlightBlock(self, text: str) -> None:
        for pattern, text_format in self.rules:
            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


class LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event: Any) -> None:
        self.editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self.path = path
        self.highlighter: SyntaxHighlighter | None = None
        self.line_area = LineNumberArea(self)
        self.setFont(QFont("Consolas", 10))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.update_line_number_area_width(0)
        self.highlight_current_line()

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def update_line_number_area_width(self, _: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect: Any, dy: int) -> None:
        if dy:
            self.line_area.scroll(0, dy)
        else:
            self.line_area.update(0, rect.y(), self.line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        contents = self.contentsRect()
        self.line_area.setGeometry(contents.left(), contents.top(), self.line_number_area_width(), contents.height())

    def paint_line_numbers(self, event: Any) -> None:
        painter = QPainter(self.line_area)
        painter.fillRect(event.rect(), QColor("#1f2328"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        painter.setPen(QColor("#808894"))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.drawText(
                    0,
                    top,
                    self.line_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number,
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def highlight_current_line(self) -> None:
        if self.isReadOnly():
            return
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#2b3137"))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def load_path(self, path: Path) -> None:
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("Binary files cannot be opened in this editor.")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        self.path = path
        self.setPlainText(text)
        self.document().setModified(False)
        self.highlighter = SyntaxHighlighter(self.document(), path.suffix)

    def save(self) -> None:
        if not self.path:
            raise ValueError("No file path assigned.")
        self.path.write_text(self.toPlainText(), encoding="utf-8")
        self.document().setModified(False)


class WorkspaceTree(QWidget):
    file_open_requested = Signal(object)
    workspace_dialog_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.workspace: Path | None = None
        self.model = QFileSystemModel(self)
        self.model.setReadOnly(False)
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs)

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.doubleClicked.connect(self.on_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.open_context_menu)

        self.open_button = QPushButton("Open Folder")
        self.open_button.clicked.connect(self.workspace_dialog_requested.emit)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)

        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.refresh_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(buttons)
        layout.addWidget(self.tree)

    def set_workspace(self, path: Path) -> None:
        self.workspace = path
        root_index = self.model.setRootPath(str(path))
        self.tree.setRootIndex(root_index)
        self.tree.setColumnWidth(0, 260)
        for column in range(1, self.model.columnCount()):
            self.tree.hideColumn(column)

    def refresh(self) -> None:
        if self.workspace:
            self.set_workspace(self.workspace)

    def selected_path(self) -> Path | None:
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        return Path(self.model.filePath(index))

    def on_double_clicked(self, index: QModelIndex) -> None:
        path = Path(self.model.filePath(index))
        if path.is_file():
            self.file_open_requested.emit(path)

    def open_context_menu(self, position: Any) -> None:
        path = self.selected_path()
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        new_file_action = menu.addAction("New File")
        new_folder_action = menu.addAction("New Folder")
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        action = menu.exec(self.tree.viewport().mapToGlobal(position))

        if action == open_action and path and path.is_file():
            self.file_open_requested.emit(path)
        elif action == new_file_action:
            self.create_file(path)
        elif action == new_folder_action:
            self.create_folder(path)
        elif action == rename_action and path:
            self.rename_path(path)
        elif action == delete_action and path:
            self.delete_path(path)

    def base_dir_for(self, path: Path | None) -> Path | None:
        if path and path.is_dir():
            return path
        if path and path.parent.exists():
            return path.parent
        return self.workspace

    def create_file(self, path: Path | None) -> None:
        base = self.base_dir_for(path)
        if not base:
            return
        name, ok = QInputDialog.getText(self, "New File", "File name:")
        if not ok or not name.strip():
            return
        target = (base / name.strip()).resolve()
        if target.exists():
            QMessageBox.warning(self, APP_NAME, "That file already exists.")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        self.refresh()
        self.file_open_requested.emit(target)

    def create_folder(self, path: Path | None) -> None:
        base = self.base_dir_for(path)
        if not base:
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        target = (base / name.strip()).resolve()
        target.mkdir(parents=True, exist_ok=False)
        self.refresh()

    def rename_path(self, path: Path) -> None:
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=path.name)
        if not ok or not name.strip() or name.strip() == path.name:
            return
        target = path.with_name(name.strip())
        if target.exists():
            QMessageBox.warning(self, APP_NAME, "Target already exists.")
            return
        path.rename(target)
        self.refresh()

    def delete_path(self, path: Path) -> None:
        answer = QMessageBox.warning(
            self,
            "Delete",
            f"Delete {path.name}? This cannot be undone by ForgeIDE.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        self.refresh()


class TerminalScreen:
    def __init__(self, rows: int = 30, cols: int = 100) -> None:
        self.rows = max(5, rows)
        self.cols = max(20, cols)
        self.scrollback_limit = 800
        self.scrollback: list[str] = []
        self.buffer: list[list[str]] = []
        self.cursor_row = 0
        self.cursor_col = 0
        self.saved_cursor = (0, 0)
        self.state = "normal"
        self.csi = ""
        self.osc = ""
        self.reset()

    def reset(self) -> None:
        self.buffer = [[" "] * self.cols for _ in range(self.rows)]
        self.cursor_row = 0
        self.cursor_col = 0
        self.saved_cursor = (0, 0)
        self.state = "normal"
        self.csi = ""
        self.osc = ""

    def resize(self, rows: int, cols: int) -> None:
        rows = max(5, rows)
        cols = max(20, cols)
        if rows == self.rows and cols == self.cols:
            return
        new_buffer = [[" "] * cols for _ in range(rows)]
        for r in range(min(rows, self.rows)):
            old_line = self.buffer[r]
            for c in range(min(cols, self.cols)):
                new_buffer[r][c] = old_line[c]
        self.rows = rows
        self.cols = cols
        self.buffer = new_buffer
        self.cursor_row = min(self.cursor_row, self.rows - 1)
        self.cursor_col = min(self.cursor_col, self.cols - 1)

    def line_text(self, row: int) -> str:
        return "".join(self.buffer[row]).rstrip()

    def render(self) -> str:
        lines = [*self.scrollback, *[self.line_text(row) for row in range(self.rows)]]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def scroll_up(self, count: int = 1) -> None:
        for _ in range(max(1, count)):
            self.scrollback.append(self.line_text(0))
            self.scrollback = self.scrollback[-self.scrollback_limit :]
            self.buffer.pop(0)
            self.buffer.append([" "] * self.cols)

    def newline(self) -> None:
        self.cursor_row += 1
        if self.cursor_row >= self.rows:
            self.cursor_row = self.rows - 1
            self.scroll_up(1)

    def put_char(self, ch: str) -> None:
        if self.cursor_col >= self.cols:
            self.cursor_col = 0
            self.newline()
        self.buffer[self.cursor_row][self.cursor_col] = ch
        self.cursor_col += 1
        if self.cursor_col >= self.cols:
            self.cursor_col = 0
            self.newline()

    def clear_screen(self, mode: int) -> None:
        if mode in (2, 3):
            self.buffer = [[" "] * self.cols for _ in range(self.rows)]
            self.cursor_row = 0
            self.cursor_col = 0
            return
        if mode == 1:
            for row in range(0, self.cursor_row):
                self.buffer[row] = [" "] * self.cols
            for col in range(0, self.cursor_col + 1):
                self.buffer[self.cursor_row][col] = " "
            return
        for col in range(self.cursor_col, self.cols):
            self.buffer[self.cursor_row][col] = " "
        for row in range(self.cursor_row + 1, self.rows):
            self.buffer[row] = [" "] * self.cols

    def clear_line(self, mode: int) -> None:
        if mode == 2:
            self.buffer[self.cursor_row] = [" "] * self.cols
        elif mode == 1:
            for col in range(0, self.cursor_col + 1):
                self.buffer[self.cursor_row][col] = " "
        else:
            for col in range(self.cursor_col, self.cols):
                self.buffer[self.cursor_row][col] = " "

    def parse_numbers(self, params: str) -> list[int]:
        params = params.lstrip("?").lstrip(">")
        if not params:
            return []
        values: list[int] = []
        for part in params.split(";"):
            digits = re.sub(r"[^0-9-]", "", part)
            values.append(int(digits) if digits not in ("", "-") else 0)
        return values

    def handle_csi(self, params: str, final: str) -> None:
        values = self.parse_numbers(params)
        first = values[0] if values else 0
        amount = first if first > 0 else 1
        private = params.startswith("?")

        if final == "m":
            return
        if final in ("h", "l"):
            if private and "1049" in params and final == "h":
                self.reset()
            return
        if final == "A":
            self.cursor_row = max(0, self.cursor_row - amount)
        elif final == "B":
            self.cursor_row = min(self.rows - 1, self.cursor_row + amount)
        elif final == "C":
            self.cursor_col = min(self.cols - 1, self.cursor_col + amount)
        elif final == "D":
            self.cursor_col = max(0, self.cursor_col - amount)
        elif final == "E":
            self.cursor_row = min(self.rows - 1, self.cursor_row + amount)
            self.cursor_col = 0
        elif final == "F":
            self.cursor_row = max(0, self.cursor_row - amount)
            self.cursor_col = 0
        elif final in ("H", "f"):
            row = values[0] if len(values) >= 1 and values[0] > 0 else 1
            col = values[1] if len(values) >= 2 and values[1] > 0 else 1
            self.cursor_row = min(self.rows - 1, max(0, row - 1))
            self.cursor_col = min(self.cols - 1, max(0, col - 1))
        elif final == "G":
            col = amount
            self.cursor_col = min(self.cols - 1, max(0, col - 1))
        elif final == "d":
            row = amount
            self.cursor_row = min(self.rows - 1, max(0, row - 1))
        elif final == "J":
            self.clear_screen(first)
        elif final == "K":
            self.clear_line(first)
        elif final == "s":
            self.saved_cursor = (self.cursor_row, self.cursor_col)
        elif final == "u":
            self.cursor_row, self.cursor_col = self.saved_cursor
        elif final == "P":
            line = self.buffer[self.cursor_row]
            del line[self.cursor_col : self.cursor_col + amount]
            line.extend([" "] * amount)
            self.buffer[self.cursor_row] = line[: self.cols]
        elif final == "@":
            line = self.buffer[self.cursor_row]
            line[self.cursor_col : self.cursor_col] = [" "] * amount
            self.buffer[self.cursor_row] = line[: self.cols]
        elif final == "L":
            for _ in range(amount):
                self.buffer.insert(self.cursor_row, [" "] * self.cols)
                self.buffer.pop()
        elif final == "M":
            for _ in range(amount):
                self.buffer.pop(self.cursor_row)
                self.buffer.append([" "] * self.cols)

    def process(self, text: str) -> None:
        text = text.replace("\u01b0m", "").replace("\u01b0", "")
        for ch in text:
            if self.state == "normal":
                if ch == "\x1b":
                    self.state = "esc"
                elif ch == "\r":
                    self.cursor_col = 0
                elif ch == "\n":
                    self.newline()
                elif ch == "\b":
                    self.cursor_col = max(0, self.cursor_col - 1)
                elif ch == "\t":
                    spaces = 8 - (self.cursor_col % 8)
                    for _ in range(spaces):
                        self.put_char(" ")
                elif ch in ("\x00", "\x07"):
                    pass
                elif ord(ch) >= 32:
                    self.put_char(ch)
            elif self.state == "esc":
                if ch == "[":
                    self.csi = ""
                    self.state = "csi"
                elif ch == "]":
                    self.osc = ""
                    self.state = "osc"
                elif ch == "7":
                    self.saved_cursor = (self.cursor_row, self.cursor_col)
                    self.state = "normal"
                elif ch == "8":
                    self.cursor_row, self.cursor_col = self.saved_cursor
                    self.state = "normal"
                elif ch == "c":
                    self.reset()
                elif ch in ("(", ")"):
                    self.state = "esc_ignore_one"
                else:
                    self.state = "normal"
            elif self.state == "esc_ignore_one":
                self.state = "normal"
            elif self.state == "csi":
                if "@" <= ch <= "~":
                    self.handle_csi(self.csi, ch)
                    self.state = "normal"
                else:
                    self.csi += ch
            elif self.state == "osc":
                if ch == "\x07":
                    self.osc = ""
                    self.state = "normal"
                elif ch == "\x1b":
                    self.state = "osc_esc"
                else:
                    self.osc += ch
            elif self.state == "osc_esc":
                self.osc = ""
                self.state = "normal"


class TerminalInput(QLineEdit):
    submit = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.history: list[str] = []
        self.history_index = 0
        self.setPlaceholderText("Command")
        self.returnPressed.connect(self.on_return)

    def on_return(self) -> None:
        command = self.text()
        if command.strip():
            self.history.append(command)
            self.history_index = len(self.history)
        self.clear()
        self.submit.emit(command)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Up and self.history:
            self.history_index = max(0, self.history_index - 1)
            self.setText(self.history[self.history_index])
            return
        if event.key() == Qt.Key.Key_Down and self.history:
            self.history_index = min(len(self.history), self.history_index + 1)
            self.setText("" if self.history_index == len(self.history) else self.history[self.history_index])
            return
        super().keyPressEvent(event)


class TerminalPanel(QWidget):
    process_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.workspace: Path | None = None
        self.pty_process: Any | None = None
        self.pty_class: Any | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.reader_stop = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.use_pty = self.load_pty_backend()

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 10))
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.screen = TerminalScreen(*self.terminal_dimensions())

        self.input = TerminalInput()
        self.input.submit.connect(self.submit)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.restart_button = QPushButton("Restart")
        self.restart_button.clicked.connect(self.restart_shell)
        self.external_button = QPushButton("OS Terminal")
        self.external_button.clicked.connect(self.open_external_terminal)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.output.clear)

        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.stop_button)
        row.addWidget(self.restart_button)
        row.addWidget(self.external_button)
        row.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.output)
        layout.addLayout(row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.drain_output_queue)
        self.timer.start(30)

    def set_workspace(self, path: Path | None) -> None:
        self.workspace = path
        if path and self.is_shell_alive():
            self.write_to_shell(self.cd_command(path) + "\r\n")
        elif path:
            self.start_shell()

    def load_pty_backend(self) -> bool:
        if os.name != "nt":
            return False
        try:
            from winpty import PtyProcess

            self.pty_class = PtyProcess
            return True
        except Exception as exc:
            print(f"{APP_NAME}: pywinpty unavailable, terminal will use command-runner fallback: {exc}", file=sys.stderr)
            return False

    def terminal_dimensions(self) -> tuple[int, int]:
        metrics = self.output.fontMetrics() if hasattr(self, "output") else None
        char_width = max(1, metrics.horizontalAdvance("M") if metrics else 9)
        char_height = max(1, metrics.height() if metrics else 16)
        cols = max(100, self.output.viewport().width() // char_width if hasattr(self, "output") else 100)
        rows = max(16, self.output.viewport().height() // char_height if hasattr(self, "output") else 30)
        return rows, cols

    def shell_argv(self) -> list[str]:
        if os.name == "nt":
            powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or "powershell.exe"
            if Path(powershell).name.lower().startswith("powershell"):
                return [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NoExit",
                    "-Command",
                    "try { Set-PSReadLineOption -HistorySaveStyle SaveNothing -ErrorAction SilentlyContinue } catch {}",
                ]
            return [powershell, "-NoLogo", "-NoProfile"]
        return [os.environ.get("SHELL", "/bin/sh")]

    def cd_command(self, path: Path) -> str:
        if os.name == "nt":
            escaped = str(path).replace("'", "''")
            return f"Set-Location -LiteralPath '{escaped}'"
        return "cd " + shlex.quote(str(path))

    def start_shell(self) -> None:
        self.shutdown_shell()
        self.screen.reset()
        self.render_screen()
        cwd = self.workspace if self.workspace and self.workspace.exists() else Path.cwd()
        if self.use_pty and self.pty_class is not None:
            rows, cols = self.terminal_dimensions()
            try:
                self.pty_process = self.pty_class.spawn(
                    self.shell_argv(),
                    cwd=str(cwd),
                    dimensions=(rows, cols),
                )
            except Exception as exc:
                self.append_raw(f"Could not start PTY terminal: {exc}\n")
                self.use_pty = False
                self.pty_process = None
            else:
                self.reader_stop.clear()
                self.reader_thread = threading.Thread(target=self.reader_loop, daemon=True)
                self.reader_thread.start()
                return
        self.append_raw("PTY terminal is unavailable. Use the OS Terminal button for fully interactive commands.\n")

    def restart_shell(self) -> None:
        self.append_raw("\n[restarting terminal]\n")
        self.start_shell()

    def shutdown_shell(self) -> None:
        self.reader_stop.set()
        process = self.pty_process
        self.pty_process = None
        if process is not None:
            try:
                if process.isalive():
                    process.terminate(force=True)
            except Exception:
                try:
                    process.kill(9)
                except Exception:
                    pass

    def is_shell_alive(self) -> bool:
        process = self.pty_process
        if process is None:
            return False
        try:
            return bool(process.isalive())
        except Exception:
            return False

    def reader_loop(self) -> None:
        process = self.pty_process
        while process is not None and not self.reader_stop.is_set():
            try:
                if not process.isalive():
                    break
                text = process.read(1024)
            except EOFError:
                break
            except Exception as exc:
                self.output_queue.put(f"\n[terminal read error: {exc}]\n")
                break
            if text:
                self.output_queue.put(str(text))
        self.output_queue.put("\n[terminal session ended]\n")

    def render_screen(self) -> None:
        scrollbar = self.output.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        self.output.setPlainText(self.screen.render())
        if at_bottom:
            cursor = self.output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.output.setTextCursor(cursor)

    def append_raw(self, text: str) -> None:
        if not text:
            return
        self.screen.process(text)
        self.render_screen()

    def append(self, text: str) -> None:
        self.append_raw(text + ("\n" if text and not text.endswith("\n") else ""))

    def drain_output_queue(self) -> None:
        parts: list[str] = []
        while True:
            try:
                parts.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        if parts:
            self.screen.process("".join(parts))
            self.render_screen()

    def write_to_shell(self, text: str) -> None:
        if not self.is_shell_alive():
            self.start_shell()
        process = self.pty_process
        if process is None:
            self.screen.process(text)
            self.render_screen()
            return
        try:
            process.write(text)
        except Exception as exc:
            self.append_raw(f"\n[terminal write error: {exc}]\n")

    def run(self, command: str) -> None:
        if not command.strip():
            return
        self.write_to_shell(command + "\r\n")

    def submit(self, text: str) -> None:
        self.write_to_shell(text + "\r\n")

    def stop(self) -> None:
        process = self.pty_process
        if process is None:
            return
        try:
            process.sendintr()
        except Exception:
            try:
                process.write("\x03")
            except Exception:
                pass

    def open_external_terminal(self) -> None:
        cwd = self.workspace if self.workspace and self.workspace.exists() else Path.cwd()
        if os.name == "nt":
            wt = shutil.which("wt.exe")
            if wt:
                subprocess.Popen([wt, "-d", str(cwd)], shell=False)
                return
            powershell = shutil.which("powershell.exe") or "powershell.exe"
            subprocess.Popen([powershell, "-NoLogo", "-NoExit", "-Command", self.cd_command(cwd)], shell=False)
            return
        terminal = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("konsole")
        if terminal:
            subprocess.Popen([terminal], cwd=str(cwd))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        process = self.pty_process
        if process is None:
            return
        rows, cols = self.terminal_dimensions()
        self.screen.resize(rows, cols)
        self.render_screen()
        try:
            process.setwinsize(rows, cols)
        except Exception:
            pass

    def closeEvent(self, event: Any) -> None:
        self.shutdown_shell()
        super().closeEvent(event)


class GitPanel(QWidget):
    terminal_command_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.service = GitService()
        self.workspace: Path | None = None
        self.save_all_callback: Any = lambda: True

        self.repo_label = QLabel("No workspace")
        self.branch_label = QLabel("Branch: -")

        self.simple_title = QLabel("Simple Git")
        self.simple_title.setObjectName("simpleTitle")
        self.simple_status = QLabel("Open a folder to start.")
        self.simple_status.setWordWrap(True)
        self.primary_git_button = QPushButton("Make Private GitHub Repo")
        self.primary_git_button.setObjectName("primaryButton")
        self.primary_git_button.clicked.connect(self.primary_git_action)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)

        self.files = QListWidget()
        self.files.currentItemChanged.connect(self.update_diff)

        self.stage_button = QPushButton("Stage")
        self.stage_button.clicked.connect(self.stage_selected)
        self.unstage_button = QPushButton("Unstage")
        self.unstage_button.clicked.connect(self.unstage_selected)
        self.stage_all_button = QPushButton("Stage All")
        self.stage_all_button.clicked.connect(self.stage_all)
        self.unstage_all_button = QPushButton("Unstage All")
        self.unstage_all_button.clicked.connect(self.unstage_all)
        self.discard_button = QPushButton("Discard")
        self.discard_button.clicked.connect(self.discard_selected)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Consolas", 10))
        self.diff_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.commit_message = QLineEdit()
        self.commit_message.setPlaceholderText("Commit message (optional)")
        self.commit_button = QPushButton("Commit All")
        self.commit_button.clicked.connect(self.commit)

        self.branch_combo = QComboBox()
        self.checkout_button = QPushButton("Checkout")
        self.checkout_button.clicked.connect(self.checkout_branch)
        self.new_branch_button = QPushButton("New Branch")
        self.new_branch_button.clicked.connect(self.new_branch)

        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.clicked.connect(lambda: self.run_in_terminal("git fetch --all --prune"))
        self.pull_button = QPushButton("Pull")
        self.pull_button.clicked.connect(lambda: self.run_in_terminal("git pull"))
        self.push_button = QPushButton("Push")
        self.push_button.clicked.connect(lambda: self.run_in_terminal("git push"))

        top = QHBoxLayout()
        top.addWidget(self.refresh_button)
        top.addStretch(1)

        simple_box = QFrame()
        simple_box.setObjectName("simpleGitBox")
        simple_layout = QVBoxLayout(simple_box)
        simple_layout.setContentsMargins(10, 10, 10, 10)
        simple_layout.setSpacing(8)
        simple_layout.addWidget(self.simple_title)
        simple_layout.addWidget(self.simple_status)
        simple_layout.addWidget(self.primary_git_button)

        action_row = QHBoxLayout()
        for widget in (
            self.stage_button,
            self.unstage_button,
            self.stage_all_button,
            self.unstage_all_button,
            self.discard_button,
        ):
            action_row.addWidget(widget)

        commit_row = QHBoxLayout()
        commit_row.addWidget(self.commit_message, 1)
        commit_row.addWidget(self.commit_button)

        branch_row = QHBoxLayout()
        branch_row.addWidget(self.branch_combo, 1)
        branch_row.addWidget(self.checkout_button)
        branch_row.addWidget(self.new_branch_button)

        remote_row = QHBoxLayout()
        remote_row.addWidget(self.fetch_button)
        remote_row.addWidget(self.pull_button)
        remote_row.addWidget(self.push_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(simple_box)
        layout.addWidget(self.repo_label)
        layout.addWidget(self.branch_label)
        layout.addWidget(QLabel("Changed files (included automatically)"))
        layout.addWidget(self.files, 1)
        layout.addLayout(action_row)
        layout.addWidget(QLabel("Diff"))
        layout.addWidget(self.diff_view, 2)
        layout.addLayout(commit_row)
        layout.addLayout(branch_row)
        layout.addLayout(remote_row)

    def set_workspace(self, path: Path | None) -> None:
        self.workspace = path
        self.service.set_workspace(path)
        self.refresh()

    def selected_status(self) -> GitFileStatus | None:
        item = self.files.currentItem()
        if not item:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, GitFileStatus) else None

    def default_repo_name(self) -> str:
        if not self.workspace:
            return "repo"
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.workspace.name).strip(".-")
        return name or "repo"

    def default_commit_message(self) -> str:
        message = self.commit_message.text().strip()
        if message:
            return message
        if self.service.is_repo() and not self.service.has_commits():
            return "Initial commit"
        return "Update project"

    def save_open_files(self) -> bool:
        try:
            return bool(self.save_all_callback())
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not save open files: {exc}")
            return False

    def github_ready(self) -> bool:
        github = GitHubService(self.workspace)
        if not github.is_available():
            QMessageBox.warning(self, APP_NAME, "GitHub CLI 'gh' was not found.")
            return False
        auth = github.auth_status()
        if not auth.ok:
            QMessageBox.warning(
                self,
                APP_NAME,
                "GitHub CLI is not logged in. Login command was sent to the terminal.",
            )
            self.terminal_command_requested.emit("gh auth login --web")
            return False
        return True

    def ensure_git_identity(self, log: list[str] | None = None) -> bool:
        name = self.service.config_get("user.name")
        email = self.service.config_get("user.email")
        if name and email:
            return True

        github = GitHubService(self.workspace)
        if not github.is_available():
            QMessageBox.warning(self, APP_NAME, "Git identity is missing and GitHub CLI 'gh' is unavailable.")
            return False
        login, user_id, result = github.current_user()
        if not result.ok or not login:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Git identity is missing and GitHub user data could not be read. Run gh auth login first.",
            )
            self.terminal_command_requested.emit("gh auth login --web")
            return False

        if not name:
            set_name = self.service.config_set_local("user.name", login)
            if not set_name.ok:
                QMessageBox.warning(self, APP_NAME, set_name.combined_output)
                return False
            if log is not None:
                log.append(f"Set local git user.name: {login}")
        if not email:
            safe_email = f"{user_id}+{login}@users.noreply.github.com" if user_id else f"{login}@users.noreply.github.com"
            set_email = self.service.config_set_local("user.email", safe_email)
            if not set_email.ok:
                QMessageBox.warning(self, APP_NAME, set_email.combined_output)
                return False
            if log is not None:
                log.append(f"Set local git user.email: {safe_email}")
        return True

    def update_simple_state(self) -> None:
        if not self.service.is_available():
            self.simple_status.setText("Git is not installed or not available in PATH.")
            self.primary_git_button.setText("Git Missing")
            self.primary_git_button.setEnabled(False)
            return
        if not self.workspace:
            self.simple_status.setText("Open a folder to use Git.")
            self.primary_git_button.setText("Open Workspace First")
            self.primary_git_button.setEnabled(False)
            return
        self.primary_git_button.setEnabled(True)
        if not self.service.is_repo():
            self.simple_status.setText(
                "This folder is not a repo. One click will initialize Git, commit all files, "
                "create a private GitHub repo, add origin, and push."
            )
            self.primary_git_button.setText("Make Private GitHub Repo")
            return
        if not self.service.has_remote("origin"):
            self.simple_status.setText(
                "This is a local repo without GitHub remote. One click will commit all changes "
                "and publish it as a private GitHub repo."
            )
            self.primary_git_button.setText("Publish To GitHub")
            return
        if self.service.has_changes():
            self.simple_status.setText("Changes found. Commit All + Sync will include every changed file automatically.")
        else:
            self.simple_status.setText("Repo is clean. Commit All + Sync will just pull/push with origin.")
        self.primary_git_button.setText("Commit All + Sync")

    def ensure_repo(self) -> bool:
        if not self.service.is_available():
            self.repo_label.setText("Git executable not found.")
            return False
        if not self.workspace:
            self.repo_label.setText("No workspace selected.")
            return False
        if not self.service.is_repo():
            self.repo_label.setText("Workspace is not a Git repository.")
            self.branch_label.setText("Branch: -")
            self.files.clear()
            self.diff_view.clear()
            self.branch_combo.clear()
            return False
        return True

    def refresh(self) -> None:
        self.update_simple_state()
        if not self.ensure_repo():
            return
        root = self.service.repository_root()
        self.repo_label.setText(f"Repo: {root if root else self.workspace}")
        self.branch_label.setText(f"Branch: {self.service.current_branch()}")
        self.files.clear()
        for status in self.service.status():
            text = f"{status.code}  {status.path}  ({status.label})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, status)
            self.files.addItem(item)
        if self.files.count() == 0:
            self.diff_view.setPlainText("No changes.")
        self.branch_combo.blockSignals(True)
        self.branch_combo.clear()
        self.branch_combo.addItems(self.service.branches())
        current = self.service.current_branch()
        index = self.branch_combo.findText(current)
        if index >= 0:
            self.branch_combo.setCurrentIndex(index)
        self.branch_combo.blockSignals(False)

    def show_result(self, result: CommandResult, success_title: str = "Done") -> None:
        text = result.combined_output or success_title
        if result.ok:
            QMessageBox.information(self, APP_NAME, text)
        else:
            QMessageBox.warning(self, APP_NAME, text)

    def require_step(self, title: str, result: CommandResult, log: list[str]) -> None:
        output = result.combined_output or "ok"
        log.append(f"{title}: {output}")
        if not result.ok:
            raise RuntimeError(f"{title} failed:\n{output}")

    def show_log(self, title: str, log: list[str]) -> None:
        QMessageBox.information(self, title, "\n\n".join(log) if log else "Done.")

    def primary_git_action(self) -> None:
        if not self.workspace:
            return
        if not self.service.is_repo() or not self.service.has_remote("origin"):
            self.make_private_github_repo()
        else:
            self.commit_all_and_sync()

    def make_private_github_repo(self) -> None:
        if not self.workspace:
            return
        if not self.github_ready():
            return
        if not self.save_open_files():
            return
        log: list[str] = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if not self.service.is_repo():
                self.require_step("Initialize Git", self.service.init_repo(), log)
            if self.service.has_changes():
                if not self.ensure_git_identity(log):
                    return
                self.require_step("Commit all files", self.service.commit_all(self.default_commit_message()), log)
            elif not self.service.has_commits():
                log.append("Commit all files: skipped because the folder has no files to commit.")

            if not self.service.has_remote("origin"):
                github = GitHubService(self.workspace)
                repo_name = self.default_repo_name()
                create = github.create_repo(repo_name, self.workspace, private=True, push=False)
                self.require_step(f"Create private GitHub repo '{repo_name}'", create, log)

            if self.service.has_commits():
                self.require_step("Push to origin", self.service.push_current(set_upstream=True), log)
            else:
                log.append("Push to origin: skipped because there is no local commit yet.")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh()
        if log:
            self.show_log("GitHub Repo Ready", log)

    def commit_all_and_sync(self) -> None:
        if not self.ensure_repo():
            self.make_private_github_repo()
            return
        if not self.save_open_files():
            return
        log: list[str] = []
        publish_after_commit = False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self.service.has_changes():
                if not self.ensure_git_identity(log):
                    return
                self.require_step("Commit all changes", self.service.commit_all(self.default_commit_message()), log)
                self.commit_message.clear()
            else:
                log.append("Commit all changes: skipped because the repo is clean.")

            if not self.service.has_remote("origin"):
                publish_after_commit = True
            else:
                if self.service.upstream():
                    self.require_step("Pull with rebase", self.service.pull_rebase(), log)
                    self.require_step("Push", self.service.push_current(set_upstream=False), log)
                else:
                    self.require_step("Push and set upstream", self.service.push_current(set_upstream=True), log)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh()
        if publish_after_commit:
            self.make_private_github_repo()
            return
        if log:
            self.show_log("Sync Done", log)

    def update_diff(self) -> None:
        status = self.selected_status()
        if not status:
            return
        parts: list[str] = []
        staged = self.service.staged_diff(status.path)
        unstaged = self.service.diff(status.path)
        if staged.stdout.strip():
            parts.append("[staged]\n" + staged.stdout)
        if unstaged.stdout.strip():
            parts.append("[unstaged]\n" + unstaged.stdout)
        if not parts:
            parts.append("No textual diff for this file.")
        self.diff_view.setPlainText("\n".join(parts))

    def stage_selected(self) -> None:
        status = self.selected_status()
        if not status:
            return
        result = self.service.stage(status.path)
        if not result.ok:
            self.show_result(result)
        self.refresh()

    def unstage_selected(self) -> None:
        status = self.selected_status()
        if not status:
            return
        result = self.service.unstage(status.path)
        if not result.ok:
            self.show_result(result)
        self.refresh()

    def stage_all(self) -> None:
        result = self.service.stage()
        if not result.ok:
            self.show_result(result)
        self.refresh()

    def unstage_all(self) -> None:
        result = self.service.unstage()
        if not result.ok:
            self.show_result(result)
        self.refresh()

    def discard_selected(self) -> None:
        status = self.selected_status()
        if not status:
            return
        answer = QMessageBox.warning(
            self,
            "Discard",
            f"Discard changes for {status.path}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.service.discard(status)
        if not result.ok:
            self.show_result(result)
        self.refresh()

    def commit(self) -> None:
        if not self.save_open_files():
            return
        log: list[str] = []
        if not self.ensure_git_identity(log):
            return
        result = self.service.commit_all(self.default_commit_message())
        self.show_result(result, "Commit created.")
        if result.ok:
            self.commit_message.clear()
        self.refresh()

    def checkout_branch(self) -> None:
        branch = self.branch_combo.currentText().strip()
        if not branch:
            return
        result = self.service.checkout(branch)
        self.show_result(result, f"Checked out {branch}.")
        self.refresh()

    def new_branch(self) -> None:
        branch, ok = QInputDialog.getText(self, "New Branch", "Branch name:")
        if not ok or not branch.strip():
            return
        result = self.service.new_branch(branch.strip())
        self.show_result(result, f"Created {branch.strip()}.")
        self.refresh()

    def run_in_terminal(self, command: str) -> None:
        if not self.ensure_repo():
            return
        self.terminal_command_requested.emit(command)


class GitHubPanel(QWidget):
    terminal_command_requested = Signal(str)
    workspace_open_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.workspace: Path | None = None
        self.service = GitHubService()

        self.auth_button = QPushButton("Auth Status")
        self.auth_button.clicked.connect(self.refresh_auth)
        self.login_button = QPushButton("Login")
        self.login_button.clicked.connect(lambda: self.terminal_command_requested.emit("gh auth login --web"))
        self.repo_button = QPushButton("List Repos")
        self.repo_button.clicked.connect(self.load_repos)
        self.clone_button = QPushButton("Clone Selected")
        self.clone_button.clicked.connect(self.clone_selected)
        self.create_button = QPushButton("Create Repo From Workspace")
        self.create_button.clicked.connect(self.create_repo)

        self.auth_output = QPlainTextEdit()
        self.auth_output.setReadOnly(True)
        self.auth_output.setMaximumHeight(110)

        self.repos = QTableWidget(0, 5)
        self.repos.setHorizontalHeaderLabels(["Repository", "Private", "Updated", "Description", "URL"])
        self.repos.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.repos.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.repos.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.repos.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.repos.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.repos.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.repos.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        top = QHBoxLayout()
        for widget in (self.auth_button, self.login_button, self.repo_button):
            top.addWidget(widget)
        top.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addWidget(self.clone_button)
        bottom.addWidget(self.create_button)
        bottom.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.auth_output)
        layout.addWidget(self.repos, 1)
        layout.addLayout(bottom)

    def set_workspace(self, path: Path | None) -> None:
        self.workspace = path
        self.service.set_workspace(path)

    def ensure_gh(self) -> bool:
        if not self.service.is_available():
            QMessageBox.warning(self, APP_NAME, "GitHub CLI 'gh' was not found.")
            return False
        return True

    def refresh_auth(self) -> None:
        if not self.ensure_gh():
            return
        result = self.service.auth_status()
        self.auth_output.setPlainText(result.combined_output or "Authenticated.")

    def load_repos(self) -> None:
        if not self.ensure_gh():
            return
        repos, result = self.service.list_repos(limit=50)
        if not result.ok:
            QMessageBox.warning(self, APP_NAME, result.combined_output)
            return
        self.repos.setRowCount(0)
        for repo in repos:
            row = self.repos.rowCount()
            self.repos.insertRow(row)
            name_item = QTableWidgetItem(repo.name_with_owner)
            name_item.setData(Qt.ItemDataRole.UserRole, repo)
            self.repos.setItem(row, 0, name_item)
            self.repos.setItem(row, 1, QTableWidgetItem("yes" if repo.is_private else "no"))
            self.repos.setItem(row, 2, QTableWidgetItem(repo.updated_at))
            self.repos.setItem(row, 3, QTableWidgetItem(repo.description))
            self.repos.setItem(row, 4, QTableWidgetItem(repo.url))

    def selected_repo(self) -> GitHubRepo | None:
        row = self.repos.currentRow()
        if row < 0:
            return None
        item = self.repos.item(row, 0)
        if not item:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, GitHubRepo) else None

    def clone_selected(self) -> None:
        repo = self.selected_repo()
        if not repo:
            QMessageBox.warning(self, APP_NAME, "Select a repository first.")
            return
        parent = QFileDialog.getExistingDirectory(self, "Clone Into")
        if not parent:
            return
        target, result = self.service.clone_repo(repo.name_with_owner, Path(parent))
        if result.ok:
            QMessageBox.information(self, APP_NAME, f"Cloned to {target}")
            self.workspace_open_requested.emit(target)
        else:
            QMessageBox.warning(self, APP_NAME, result.combined_output)

    def create_repo(self) -> None:
        if not self.workspace:
            QMessageBox.warning(self, APP_NAME, "Open a workspace first.")
            return
        default_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.workspace.name).strip("-") or "new-repo"
        name, ok = QInputDialog.getText(self, "Create GitHub Repo", "Repository name:", text=default_name)
        if not ok or not name.strip():
            return
        private_answer = QMessageBox.question(
            self,
            "Visibility",
            "Create as private repository? Choose No for public.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        push_answer = QMessageBox.question(
            self,
            "Push",
            "Push current workspace after creating the repository?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        result = self.service.create_repo(
            name=name.strip(),
            source=self.workspace,
            private=private_answer == QMessageBox.StandardButton.Yes,
            push=push_answer == QMessageBox.StandardButton.Yes,
        )
        if result.ok:
            QMessageBox.information(self, APP_NAME, result.combined_output or "Repository created.")
        else:
            QMessageBox.warning(self, APP_NAME, result.combined_output)


APP_QSS = """
QMainWindow, QWidget {
    background: #202124;
    color: #e8eaed;
    font-size: 12px;
}
QPlainTextEdit, QTreeView, QListWidget, QTableWidget, QLineEdit, QComboBox {
    background: #151719;
    color: #e8eaed;
    border: 1px solid #383d42;
    selection-background-color: #0f766e;
    selection-color: #ffffff;
}
QPushButton {
    background: #2d3339;
    color: #f3f4f6;
    border: 1px solid #454b52;
    border-radius: 4px;
    padding: 5px 9px;
}
QPushButton:hover {
    background: #374049;
}
QPushButton:pressed {
    background: #0f766e;
}
QPushButton#primaryButton {
    background: #0f766e;
    color: #ffffff;
    border: 1px solid #14b8a6;
    font-weight: 700;
    padding: 8px 10px;
}
QPushButton#primaryButton:hover {
    background: #0d9488;
}
QTabWidget::pane {
    border: 1px solid #383d42;
}
QTabBar::tab {
    background: #262a2f;
    color: #cfd4dc;
    padding: 6px 10px;
    border: 1px solid #383d42;
}
QTabBar::tab:selected {
    background: #151719;
    color: #ffffff;
    border-bottom: 2px solid #e0af68;
}
QHeaderView::section {
    background: #2a2f34;
    color: #e8eaed;
    border: 1px solid #383d42;
    padding: 4px;
}
QFrame#workspaceBar {
    background: #181b1f;
    border-bottom: 1px solid #383d42;
}
QLabel#workspaceName {
    color: #e0af68;
    font-weight: 700;
    padding: 0 8px 0 2px;
}
QFrame#simpleGitBox {
    background: #181b1f;
    border: 1px solid #383d42;
    border-radius: 6px;
}
QLabel#simpleTitle {
    color: #e8eaed;
    font-weight: 700;
    font-size: 13px;
}
QLineEdit#workspacePath {
    background: #101214;
    color: #d7dde5;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 5px 8px;
}
QDockWidget {
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: #2a2f34;
    color: #f3f4f6;
    padding: 5px;
}
QSplitter::handle {
    background: #383d42;
}
QSplitter::handle:hover {
    background: #0f766e;
}
QSplitter::handle:horizontal {
    width: 8px;
}
QSplitter::handle:vertical {
    height: 8px;
}
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = SettingsStore()
        self.workspace: Path | None = None
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1400, 900)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks
        )

        self.file_tree = WorkspaceTree()
        self.file_tree.file_open_requested.connect(self.open_file)
        self.file_tree.workspace_dialog_requested.connect(self.open_workspace_dialog)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)

        self.terminal = TerminalPanel()
        self.git_panel = GitPanel()
        self.github_panel = GitHubPanel()
        self.git_panel.save_all_callback = self.save_all

        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.editor_splitter.addWidget(self.file_tree)
        self.editor_splitter.addWidget(self.tabs)
        self.editor_splitter.setStretchFactor(0, 0)
        self.editor_splitter.setStretchFactor(1, 1)
        self.editor_splitter.setSizes([330, 980])
        self.editor_splitter.setHandleWidth(8)

        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.addWidget(self.editor_splitter)
        self.main_splitter.addWidget(self.terminal)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([680, 220])
        self.main_splitter.setHandleWidth(8)
        self.main_splitter.setCollapsible(0, False)
        self.main_splitter.setCollapsible(1, False)

        self.workspace_name_label = QLabel("No workspace")
        self.workspace_name_label.setObjectName("workspaceName")
        self.workspace_path_display = QLineEdit()
        self.workspace_path_display.setReadOnly(True)
        self.workspace_path_display.setObjectName("workspacePath")
        self.workspace_path_display.setPlaceholderText("Open a workspace")
        self.copy_workspace_button = QPushButton("Copy")
        self.copy_workspace_button.clicked.connect(self.copy_workspace_path)
        self.open_workspace_button = QPushButton("Open")
        self.open_workspace_button.clicked.connect(self.open_workspace_dialog)

        workspace_bar = QFrame()
        workspace_bar.setObjectName("workspaceBar")
        workspace_layout = QHBoxLayout(workspace_bar)
        workspace_layout.setContentsMargins(10, 7, 10, 7)
        workspace_layout.setSpacing(8)
        workspace_layout.addWidget(QLabel("Workspace"))
        workspace_layout.addWidget(self.workspace_name_label)
        workspace_layout.addWidget(self.workspace_path_display, 1)
        workspace_layout.addWidget(self.copy_workspace_button)
        workspace_layout.addWidget(self.open_workspace_button)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(workspace_bar)
        central_layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(central)

        self.git_panel.terminal_command_requested.connect(self.terminal.run)
        self.github_panel.terminal_command_requested.connect(self.terminal.run)
        self.github_panel.workspace_open_requested.connect(self.set_workspace)
        self.terminal.process_finished.connect(self.git_panel.refresh)

        self.add_dock("Git", self.git_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.add_dock("GitHub", self.github_panel, Qt.DockWidgetArea.RightDockWidgetArea)

        self.create_actions()
        self.restore_window_state()

        start_workspace = self.settings.get("last_workspace")
        if start_workspace and Path(start_workspace).exists():
            self.set_workspace(Path(start_workspace))
        else:
            self.set_workspace(Path.cwd())

        self.statusBar().showMessage("Ready")

    def add_dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> None:
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        dock.setObjectName(title.replace(" ", "_").lower())
        self.addDockWidget(area, dock)

    def create_actions(self) -> None:
        style = self.style()
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        file_menu = self.menuBar().addMenu("File")
        git_menu = self.menuBar().addMenu("Git")
        help_menu = self.menuBar().addMenu("Help")

        open_workspace = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Open Workspace", self)
        open_workspace.setShortcut(QKeySequence.StandardKey.Open)
        open_workspace.triggered.connect(self.open_workspace_dialog)

        open_file = QAction("Open File", self)
        open_file.triggered.connect(self.open_file_dialog)

        new_file = QAction("New File", self)
        new_file.setShortcut(QKeySequence.StandardKey.New)
        new_file.triggered.connect(self.new_file)

        save = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Save", self)
        save.setShortcut(QKeySequence.StandardKey.Save)
        save.triggered.connect(self.save_current)

        save_all = QAction("Save All", self)
        save_all.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_all.triggered.connect(self.save_all)

        find = QAction("Find", self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self.find_text)

        run_python = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Run Python File", self)
        run_python.setShortcut(QKeySequence("F5"))
        run_python.triggered.connect(self.run_current_python)

        git_refresh = QAction("Refresh Git", self)
        git_refresh.triggered.connect(self.git_panel.refresh)

        about = QAction("About", self)
        about.triggered.connect(self.show_about)

        for action in (open_workspace, open_file, new_file, save, save_all, find):
            file_menu.addAction(action)
        git_menu.addAction(git_refresh)
        help_menu.addAction(about)

        for action in (open_workspace, save, run_python, git_refresh):
            toolbar.addAction(action)

    def restore_window_state(self) -> None:
        geometry = self.settings.get("geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        state = self.settings.get("window_state")
        if state:
            self.restoreState(QByteArray.fromBase64(state.encode("ascii")))
        main_splitter_state = self.settings.get("main_splitter_state")
        if main_splitter_state:
            self.main_splitter.restoreState(QByteArray.fromBase64(main_splitter_state.encode("ascii")))
        editor_splitter_state = self.settings.get("editor_splitter_state")
        if editor_splitter_state:
            self.editor_splitter.restoreState(QByteArray.fromBase64(editor_splitter_state.encode("ascii")))

    def persist_window_state(self) -> None:
        self.settings.set("geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        self.settings.set("window_state", bytes(self.saveState().toBase64()).decode("ascii"))
        self.settings.set("main_splitter_state", bytes(self.main_splitter.saveState().toBase64()).decode("ascii"))
        self.settings.set("editor_splitter_state", bytes(self.editor_splitter.saveState().toBase64()).decode("ascii"))

    def set_workspace(self, path_obj: Any) -> None:
        path = Path(path_obj).resolve()
        if not path.exists() or not path.is_dir():
            QMessageBox.warning(self, APP_NAME, f"Workspace does not exist: {path}")
            return
        self.workspace = path
        self.workspace_name_label.setText(path.name or str(path))
        self.workspace_path_display.setText(str(path))
        self.workspace_path_display.setCursorPosition(0)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION} - {path}")
        self.settings.set("last_workspace", str(path))
        self.settings.add_recent_workspace(path)
        self.file_tree.set_workspace(path)
        self.terminal.set_workspace(path)
        self.git_panel.set_workspace(path)
        self.github_panel.set_workspace(path)
        self.statusBar().showMessage(f"Workspace: {path}")

    def copy_workspace_path(self) -> None:
        if not self.workspace:
            return
        QApplication.clipboard().setText(str(self.workspace))
        self.statusBar().showMessage("Workspace path copied.", 2500)

    def open_workspace_dialog(self) -> None:
        start = str(self.workspace or Path.cwd())
        selected = QFileDialog.getExistingDirectory(self, "Open Workspace", start)
        if selected:
            self.set_workspace(Path(selected))

    def open_file_dialog(self) -> None:
        start = str(self.workspace or Path.cwd())
        selected, _ = QFileDialog.getOpenFileName(self, "Open File", start)
        if selected:
            self.open_file(Path(selected))

    def new_file(self) -> None:
        if not self.workspace:
            return
        name, ok = QInputDialog.getText(self, "New File", "Path relative to workspace:")
        if not ok or not name.strip():
            return
        target = (self.workspace / name.strip()).resolve()
        try:
            target.relative_to(self.workspace.resolve())
        except ValueError:
            QMessageBox.warning(self, APP_NAME, "File must be inside the workspace.")
            return
        if target.exists():
            QMessageBox.warning(self, APP_NAME, "File already exists.")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        self.file_tree.refresh()
        self.open_file(target)

    def editor_at(self, index: int | None = None) -> CodeEditor | None:
        if index is None:
            index = self.tabs.currentIndex()
        widget = self.tabs.widget(index)
        return widget if isinstance(widget, CodeEditor) else None

    def tab_for_path(self, path: Path) -> int:
        for index in range(self.tabs.count()):
            editor = self.editor_at(index)
            if editor and editor.path and editor.path.resolve() == path.resolve():
                return index
        return -1

    def open_file(self, path_obj: Any) -> None:
        path = Path(path_obj).resolve()
        existing = self.tab_for_path(path)
        if existing >= 0:
            self.tabs.setCurrentIndex(existing)
            return
        editor = CodeEditor()
        try:
            editor.load_path(path)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        editor.document().modificationChanged.connect(lambda _: self.update_tab_title(editor))
        index = self.tabs.addTab(editor, path.name)
        self.tabs.setCurrentIndex(index)
        self.update_tab_title(editor)

    def update_tab_title(self, editor: CodeEditor) -> None:
        index = self.tabs.indexOf(editor)
        if index < 0:
            return
        name = editor.path.name if editor.path else "Untitled"
        if editor.document().isModified():
            name = "*" + name
        self.tabs.setTabText(index, name)
        if editor.path:
            self.tabs.setTabToolTip(index, str(editor.path))

    def save_current(self) -> bool:
        editor = self.editor_at()
        if not editor:
            return True
        try:
            editor.save()
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return False
        self.update_tab_title(editor)
        self.git_panel.refresh()
        return True

    def save_all(self) -> bool:
        for index in range(self.tabs.count()):
            editor = self.editor_at(index)
            if editor and editor.document().isModified():
                self.tabs.setCurrentIndex(index)
                if not self.save_current():
                    return False
        return True

    def prompt_save_if_needed(self, editor: CodeEditor) -> bool:
        if not editor.document().isModified():
            return True
        name = editor.path.name if editor.path else "Untitled"
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved Changes")
        box.setText(f"Save changes to {name}?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            self.tabs.setCurrentWidget(editor)
            return self.save_current()
        if answer == QMessageBox.StandardButton.Discard:
            return True
        return False

    def close_tab(self, index: int) -> None:
        editor = self.editor_at(index)
        if editor and not self.prompt_save_if_needed(editor):
            return
        self.tabs.removeTab(index)

    def find_text(self) -> None:
        editor = self.editor_at()
        if not editor:
            return
        text, ok = QInputDialog.getText(self, "Find", "Text:")
        if ok and text:
            if not editor.find(text):
                cursor = editor.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                editor.setTextCursor(cursor)
                editor.find(text)

    def run_current_python(self) -> None:
        editor = self.editor_at()
        if not editor or not editor.path:
            QMessageBox.warning(self, APP_NAME, "Open a Python file first.")
            return
        if editor.path.suffix.lower() != ".py":
            QMessageBox.warning(self, APP_NAME, "The active file is not a Python file.")
            return
        if editor.document().isModified() and not self.save_current():
            return
        command = quote_command([sys.executable, str(editor.path)])
        self.terminal.run(command)

    def show_about(self) -> None:
        text = (
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Single-file Python IDE.\n"
            "Runtime: PySide6-Essentials in a virtual environment.\n"
            "Git: local git executable.\n"
            "GitHub: local gh executable.\n\n"
            "This app is not affiliated with GitHub."
        )
        QMessageBox.information(self, APP_NAME, text)

    def closeEvent(self, event: Any) -> None:
        for index in range(self.tabs.count()):
            editor = self.editor_at(index)
            if editor and not self.prompt_save_if_needed(editor):
                event.ignore()
                return
        self.terminal.shutdown_shell()
        self.persist_window_state()
        event.accept()


def run_self_test() -> int:
    sample = " M modified.py\nA  staged.py\n?? new.txt\nR  old.py -> renamed.py\n"
    parsed = parse_porcelain_status(sample)
    assert len(parsed) == 4, parsed
    assert parsed[0].path == "modified.py"
    assert parsed[1].is_staged
    assert parsed[2].is_untracked
    assert parsed[3].old_path == "old.py"
    print(f"{APP_NAME} self-test ok")
    print(f"Python: {sys.executable}")
    print(f"Platform: {platform.platform()}")
    print(f"git: {'found' if executable_exists('git') else 'missing'}")
    print(f"gh: {'found' if executable_exists('gh') else 'missing'}")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
