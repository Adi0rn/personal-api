"""Local, persistent tasks and project metadata stored in SQLite."""

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints, field_validator, model_validator

DB_PATH = Path(__file__).resolve().parent / "data" / "tasks.db"
router = APIRouter(prefix="/tasks", tags=["Tasks"])
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Priority = Literal["low", "medium", "high"]


class DueDateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_date: date | None = None

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_due_date(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use a date in YYYY-MM-DD format, or null to clear it.")
        return date.fromisoformat(value)


class TaskCreate(DueDateModel):
    title: Title
    priority: Priority = "medium"


class TaskUpdate(DueDateModel):
    title: Title | None = None
    priority: Priority | None = None
    completed: StrictBool | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        for name in ("title", "priority", "completed"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class Task(BaseModel):
    id: int
    title: str
    completed: bool
    created_at: str
    due_date: date | None
    priority: Priority


@contextmanager
def database():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with database() as connection:
        connection.execute("BEGIN")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200), "
            "completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0, 1)), "
            "created_at TEXT NOT NULL)"
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
        if "due_date" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
        if "priority" not in columns:
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium' "
                "CHECK(priority IN ('low', 'medium', 'high'))"
            )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS project_details ("
            "name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT 'unspecified', next_steps TEXT NOT NULL DEFAULT '[]')"
        )


@router.get("", response_model=list[Task])
def list_tasks(completed: bool | None = None, priority: Priority | None = None, due_before: date | None = None):
    """Filter tasks by completion, priority, or a due date (inclusive)."""
    conditions, values = [], []
    if completed is not None:
        conditions.append("completed = ?")
        values.append(int(completed))
    if priority is not None:
        conditions.append("priority = ?")
        values.append(priority)
    if due_before is not None:
        conditions.append("due_date <= ?")
        values.append(due_before.isoformat())
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with database() as connection:
        rows = connection.execute("SELECT * FROM tasks" + where + " ORDER BY id DESC", values).fetchall()
    return [dict(row) for row in rows]


@router.post("", response_model=Task, status_code=201)
def create_task(task: TaskCreate):
    with database() as connection:
        cursor = connection.execute(
            "INSERT INTO tasks (title, created_at, due_date, priority) VALUES (?, ?, ?, ?)",
            (task.title, datetime.now(timezone.utc).isoformat(),
             task.due_date.isoformat() if task.due_date else None, task.priority),
        )
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@router.patch("/{task_id}", response_model=Task)
def update_task(task_id: int, task: TaskUpdate):
    changes = task.model_dump(mode="json", exclude_unset=True)
    if "completed" in changes:
        changes["completed"] = int(changes["completed"])
    # Field names come only from the validated TaskUpdate model; values are bound separately.
    assignments = ", ".join(f"{name} = ?" for name in changes)
    with database() as connection:
        cursor = connection.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ?", (*changes.values(), task_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Task not found.")
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row)
