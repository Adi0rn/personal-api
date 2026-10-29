"""User-supplied bookmarks stored locally; saving never fetches the URL."""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, HttpUrl, StrictBool, StringConstraints, field_validator, model_validator

from tasks import database
from spotify_connection import local_request

router = APIRouter(prefix="/links", tags=["Saved links"], dependencies=[Depends(local_request)])
Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]


class LinkFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    title: Title = ""
    tags: list[Tag] = []

    @field_validator("url")
    @classmethod
    def safe_url(cls, url):
        if url.username or url.password or len(str(url)) > 4096:
            raise ValueError("Use an HTTP or HTTPS link without embedded credentials, up to 4096 characters.")
        return url

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags):
        if len(tags) > 10:
            raise ValueError("Use up to 10 tags.")
        return list(dict.fromkeys(tag.casefold() for tag in tags))


class LinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Title | None = None
    tags: list[Tag] | None = None
    is_read: StrictBool | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set or any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Provide at least one non-null field to update.")
        if self.tags is not None:
            self.tags = LinkFields.clean_tags(self.tags)
        return self


def init_db():
    with database() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS saved_links (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "url TEXT NOT NULL UNIQUE, title TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]', "
                           "is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")


def result(row):
    return {**dict(row), "tags": json.loads(row["tags"]), "is_read": bool(row["is_read"])}


@router.get("")
def list_links(q: str = Query("", max_length=200), tag: str = Query("", max_length=30), is_read: bool | None = None):
    with database() as connection:
        rows = connection.execute("SELECT * FROM saved_links ORDER BY id DESC").fetchall()
    links = [result(row) for row in rows]
    query = q.strip().casefold()
    return [link for link in links if (is_read is None or link["is_read"] == is_read)
            and (not tag or tag.strip().casefold() in link["tags"])
            and (not query or query in " ".join([link["title"], link["url"], *link["tags"]]).casefold())]


@router.post("", status_code=201)
def create_link(body: LinkFields):
    try:
        with database() as connection:
            cursor = connection.execute("INSERT INTO saved_links (url,title,tags,created_at) VALUES (?,?,?,?)",
                                        (str(body.url), body.title or body.url.host, json.dumps(body.tags),
                                         datetime.now(timezone.utc).isoformat()))
            return result(connection.execute("SELECT * FROM saved_links WHERE id=?", (cursor.lastrowid,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, "This link is already saved.") from None


@router.patch("/{link_id}")
def update_link(link_id: int, body: LinkUpdate):
    changes = body.model_dump(exclude_unset=True)
    if "tags" in changes:
        changes["tags"] = json.dumps(changes["tags"])
    if "is_read" in changes:
        changes["is_read"] = int(changes["is_read"])
    with database() as connection:
        if connection.execute("UPDATE saved_links SET " + ",".join(name + "=?" for name in changes) + " WHERE id=?",
                              (*changes.values(), link_id)).rowcount == 0:
            raise HTTPException(404, "Saved link not found.")
        return result(connection.execute("SELECT * FROM saved_links WHERE id=?", (link_id,)).fetchone())


@router.delete("/{link_id}", status_code=204)
def delete_link(link_id: int):
    with database() as connection:
        if connection.execute("DELETE FROM saved_links WHERE id=?", (link_id,)).rowcount == 0:
            raise HTTPException(404, "Saved link not found.")
    return Response(status_code=204)
