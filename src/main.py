#!/usr/bin/env python3
"""TaskFlow - a small command-line task manager.

Tasks are stored as JSON. The storage file location is resolved in this order:

1. The ``--file`` command-line option.
2. The ``TASKFLOW_FILE`` environment variable.
3. ``~/.taskflow/tasks.json``.

Examples::

    python src/main.py add "Write the report"
    python src/main.py list
    python src/main.py list --status done --json
    python src/main.py done 1 3
    python src/main.py delete 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

__version__ = "1.0.0"

SCHEMA_VERSION = 1
ENV_FILE_VAR = "TASKFLOW_FILE"
DEFAULT_FILE = Path.home() / ".taskflow" / "tasks.json"
MAX_TITLE_LENGTH = 500

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


class TaskFlowError(Exception):
    """Base error for expected, user-facing failures."""


class StorageError(TaskFlowError):
    """Raised when the task file cannot be read or written."""


class TaskNotFoundError(TaskFlowError):
    """Raised when a task ID does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Task:
    id: int
    title: str
    done: bool = False
    created_at: str = ""
    completed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        try:
            task_id = data["id"]
            title = data["title"]
        except KeyError as exc:
            raise StorageError(f"task entry is missing field {exc}") from None
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
            raise StorageError(f"invalid task id: {task_id!r}")
        if not isinstance(title, str):
            raise StorageError(f"invalid title for task {task_id}")
        return cls(
            id=task_id,
            title=title,
            done=bool(data.get("done", False)),
            created_at=str(data.get("created_at", "")),
            completed_at=data.get("completed_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskStore:
    """Loads and persists tasks in a JSON file using atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.tasks: list[Task] = []
        self.next_id = 1
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"cannot read {self.path}: {exc.strerror or exc}") from exc
        if not raw.strip():
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StorageError(
                f"{self.path} is not valid JSON (line {exc.lineno}, column {exc.colno}); "
                "fix or remove the file"
            ) from exc

        # Accept a bare list for compatibility with simple hand-written files.
        if isinstance(data, list):
            entries, stored_next_id = data, None
        elif isinstance(data, dict):
            entries = data.get("tasks", [])
            stored_next_id = data.get("next_id")
            version = data.get("version", SCHEMA_VERSION)
            if isinstance(version, int) and version > SCHEMA_VERSION:
                raise StorageError(
                    f"{self.path} uses schema version {version}; "
                    f"this TaskFlow supports up to {SCHEMA_VERSION}"
                )
        else:
            raise StorageError(f"{self.path} has an unexpected format")

        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise StorageError(f"{self.path} has an invalid 'tasks' list")

        self.tasks = [Task.from_dict(entry) for entry in entries]
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise StorageError(f"{self.path} contains duplicate task IDs")

        highest = max(ids, default=0)
        if isinstance(stored_next_id, int) and stored_next_id > highest:
            self.next_id = stored_next_id
        else:
            self.next_id = highest + 1

    def save(self) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "next_id": self.next_id,
            "tasks": [task.to_dict() for task in self.tasks],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temp file in the same directory, then atomically replace,
            # so a crash mid-write never leaves a truncated task file behind.
            fd, tmp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, self.path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            raise StorageError(f"cannot write {self.path}: {exc.strerror or exc}") from exc

    def get(self, task_id: int) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise TaskNotFoundError(f"task {task_id} not found")


def normalize_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if not cleaned:
        raise TaskFlowError("task title cannot be empty")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise TaskFlowError(f"task title exceeds {MAX_TITLE_LENGTH} characters")
    return cleaned


def add_task(store: TaskStore, title: str) -> Task:
    """Create a new pending task, persist it, and return it."""
    task = Task(id=store.next_id, title=normalize_title(title), created_at=_now())
    store.tasks.append(task)
    store.next_id += 1
    store.save()
    return task


def list_tasks(store: TaskStore, status: str = "all") -> list[Task]:
    """Return tasks filtered by status: 'all', 'pending' or 'done'."""
    if status == "pending":
        return [t for t in store.tasks if not t.done]
    if status == "done":
        return [t for t in store.tasks if t.done]
    if status == "all":
        return list(store.tasks)
    raise ValueError(f"unknown status filter: {status!r}")


def mark_done(store: TaskStore, task_ids: Iterable[int]) -> tuple[list[Task], list[Task]]:
    """Mark tasks as done.

    Returns ``(newly_completed, already_done)``. All IDs are validated before
    anything is changed, so an unknown ID leaves the file untouched.
    """
    unique_ids = list(dict.fromkeys(task_ids))
    tasks = [store.get(task_id) for task_id in unique_ids]

    completed: list[Task] = []
    already: list[Task] = []
    timestamp = _now()
    for task in tasks:
        if task.done:
            already.append(task)
        else:
            task.done = True
            task.completed_at = timestamp
            completed.append(task)

    if completed:
        store.save()
    return completed, already


def delete_tasks(store: TaskStore, task_ids: Iterable[int]) -> list[Task]:
    """Delete tasks and return them.

    All IDs are validated before anything is removed, so an unknown ID leaves
    the file untouched. Deleted IDs are never reused.
    """
    unique_ids = list(dict.fromkeys(task_ids))
    removed = [store.get(task_id) for task_id in unique_ids]
    doomed = set(unique_ids)
    store.tasks = [t for t in store.tasks if t.id not in doomed]
    store.save()
    return removed


def format_task(task: Task) -> str:
    mark = "x" if task.done else " "
    return f"[{mark}] {task.id:>3}  {task.title}"


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid task id: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"task id must be a positive integer: {value!r}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskflow",
        description="TaskFlow - manage your tasks from the command line.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help=f"path to the tasks JSON file (default: ${ENV_FILE_VAR} or {DEFAULT_FILE})",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p_add = sub.add_parser("add", help="add a new task", description="Add a new task.")
    p_add.add_argument("title", nargs="+", help="task title (quotes optional)")

    p_list = sub.add_parser("list", aliases=["ls"], help="list tasks", description="List tasks.")
    p_list.add_argument(
        "-s",
        "--status",
        choices=("all", "pending", "done"),
        default="all",
        help="filter by status (default: all)",
    )
    p_list.add_argument("--json", action="store_true", help="output tasks as JSON")

    p_done = sub.add_parser(
        "done", help="mark tasks as done", description="Mark one or more tasks as done."
    )
    p_done.add_argument("ids", nargs="+", type=_positive_int, metavar="ID", help="task ID(s)")

    p_delete = sub.add_parser(
        "delete",
        aliases=["rm"],
        help="delete tasks",
        description="Permanently delete one or more tasks.",
    )
    p_delete.add_argument("ids", nargs="+", type=_positive_int, metavar="ID", help="task ID(s)")

    return parser


def resolve_path(cli_path: Path | None) -> Path:
    if cli_path is not None:
        return cli_path.expanduser()
    env_path = os.environ.get(ENV_FILE_VAR)
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_FILE


def _cmd_add(store: TaskStore, args: argparse.Namespace) -> int:
    task = add_task(store, " ".join(args.title))
    print(f"Added task {task.id}: {task.title}")
    return EXIT_OK


def _cmd_list(store: TaskStore, args: argparse.Namespace) -> int:
    tasks = list_tasks(store, args.status)
    if args.json:
        print(json.dumps([t.to_dict() for t in tasks], indent=2, ensure_ascii=False))
        return EXIT_OK
    if not tasks:
        print("No tasks found." if args.status == "all" else f"No {args.status} tasks.")
        return EXIT_OK
    for task in tasks:
        print(format_task(task))
    pending = sum(1 for t in store.tasks if not t.done)
    print(f"\n{len(store.tasks)} total, {pending} pending, {len(store.tasks) - pending} done")
    return EXIT_OK


def _cmd_done(store: TaskStore, args: argparse.Namespace) -> int:
    completed, already = mark_done(store, args.ids)
    for task in completed:
        print(f"Completed task {task.id}: {task.title}")
    for task in already:
        print(f"Task {task.id} was already done: {task.title}")
    return EXIT_OK


def _cmd_delete(store: TaskStore, args: argparse.Namespace) -> int:
    for task in delete_tasks(store, args.ids):
        print(f"Deleted task {task.id}: {task.title}")
    return EXIT_OK


COMMANDS = {
    "add": _cmd_add,
    "list": _cmd_list,
    "ls": _cmd_list,
    "done": _cmd_done,
    "delete": _cmd_delete,
    "rm": _cmd_delete,
}


def main(argv: Sequence[str] | None = None) -> int:
    # Always emit UTF-8: on Windows, piped output otherwise uses the ANSI codepage
    # (e.g. cp1252) regardless of LANG, which garbles accented task titles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        store = TaskStore(resolve_path(args.file))
        return COMMANDS[args.command](store, args)
    except TaskFlowError as exc:
        print(f"taskflow: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\ntaskflow: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
