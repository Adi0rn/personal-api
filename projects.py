"""Discover project folders and keep their editable details separately."""

import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tasks import database

PROJECTS_DIR = Path(__file__).resolve().parent.parent
router = APIRouter(prefix="/projects", tags=["Projects"])
Status = Literal["unspecified", "planned", "active", "paused", "completed"]
Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class Project(BaseModel):
    name: str
    path: str
    description: str = ""
    status: Status = "unspecified"
    next_steps: list[str] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    status: Status = "unspecified"
    next_steps: list[Step] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


def project_folders() -> list[Path]:
    try:
        return sorted(
            (folder for folder in PROJECTS_DIR.iterdir()
             if folder.is_dir() and not folder.name.startswith(".")),
            key=lambda folder: folder.name.casefold(),
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Cannot read the projects folder.") from exc


def require_folder(project_name: str) -> Path:
    # Match a discovered direct child; request text is never used to traverse paths.
    for folder in project_folders():
        if folder.name == project_name:
            return folder
    raise HTTPException(status_code=404, detail="Project folder not found.")


def project_result(folder: Path, row=None) -> Project:
    details = {} if row is None else {
        "description": row["description"], "status": row["status"],
        "next_steps": json.loads(row["next_steps"]),
    }
    return Project(name=folder.name, path=str(folder), **details)


@router.get("", response_model=dict[str, list[Project]])
def list_projects():
    folders = project_folders()
    with database() as connection:
        saved = {row["name"]: row for row in connection.execute("SELECT * FROM project_details")}
    return {"projects": [project_result(folder, saved.get(folder.name)) for folder in folders]}


@router.get("/{project_name}", response_model=Project)
def get_project(project_name: str):
    folder = require_folder(project_name)
    with database() as connection:
        row = connection.execute("SELECT * FROM project_details WHERE name = ?", (folder.name,)).fetchone()
    return project_result(folder, row)


@router.patch("/{project_name}", response_model=Project)
def update_project(project_name: str, project: ProjectUpdate):
    folder = require_folder(project_name)
    changes = project.model_dump(exclude_unset=True)
    if "next_steps" in changes:
        changes["next_steps"] = json.dumps(changes["next_steps"])
    assignments = ", ".join(f"{name} = ?" for name in changes)
    with database() as connection:
        connection.execute("INSERT OR IGNORE INTO project_details (name) VALUES (?)", (folder.name,))
        connection.execute(
            f"UPDATE project_details SET {assignments} WHERE name = ?", (*changes.values(), folder.name)
        )
        row = connection.execute("SELECT * FROM project_details WHERE name = ?", (folder.name,)).fetchone()
    return project_result(folder, row)
