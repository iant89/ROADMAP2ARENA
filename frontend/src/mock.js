// ALL mock data for the ROADMAP2ARENA mock frontend lives in this file.
// Nothing here talks to a real backend; src/lib/api.js reads from it.
//
// Canned responses use "@@@" as a stand-in for a triple-backtick fence so the
// text stays readable inside JS template literals; fence() converts it.

const fence = (s) => s.replace(/@@@/g, '```').replace(/^\n/, '')

export const MOCK_MODE_NOTICE = 'Mock mode - all data is simulated, no backend connected'

export const DEFAULT_CONFIG = {
  arena_url: 'http://localhost:9090',
  model: 'gpt-4o',
  step_delay_seconds: 2,
}

export const MODEL_SUGGESTIONS = ['gpt-4o', 'claude-3-5-sonnet', 'gemini-1.5-pro', 'deepseek-v3']

export const SAMPLE_PROJECT_CONTEXT = `Tasky is a small REST API for personal todo lists.
Stack: Python 3.12, FastAPI, SQLModel on SQLite, pytest.
Keep it dependency-light, typed, and runnable with a single uvicorn command.`

export const SAMPLE_ROADMAP = `# Tasky - FastAPI todo API

A tiny todo service used to try out ROADMAP2ARENA. Plain paragraphs, other
headings and checked boxes outside a ### block are ignored by the parser.

## Setup

- [x] Pick a project name and license
- [ ] Project skeleton

### Data models
Define the Todo table model and the request/response schemas
(TodoCreate, TodoUpdate, TodoRead) with SQLModel.

### Database layer
Add app/db.py with a SQLite engine, init_db() and a get_session dependency.

### CRUD endpoints
Implement /todos routes (list, create, read, update, delete) in app/routes/todos.py
and wire them into app/main.py with a lifespan hook that creates tables.

### Settings from environment
Read the database URL and debug flag from TASKY_* environment variables
in app/config.py, use them in app/db.py, and ship an .env.example.

### Tests for the todo routes
Add pytest fixtures with an in-memory SQLite database and tests covering
create, list, update and delete.

### Docker and docs
Add a Dockerfile, docker-compose.yml and a README with run instructions.
`

// Canned arena2api responses for SAMPLE_ROADMAP, one per step (index 0 = step 1).
export const CANNED_RESPONSES = [
  fence(`
Here is the project skeleton. The app package is importable and exposes a health check.

@@@toml:pyproject.toml
[project]
name = "tasky"
version = "0.1.0"
description = "A tiny todo REST API"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlmodel>=0.0.22",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]
@@@

@@@python:app/__init__.py
"""Tasky - a tiny todo REST API."""

__version__ = "0.1.0"
@@@

@@@python:app/main.py
from fastapi import FastAPI

app = FastAPI(title="Tasky")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
@@@

Run it locally with:

@@@bash
pip install -e ".[dev]"
uvicorn app.main:app --reload
@@@
`),
  fence(`
The models below use SQLModel so one class can serve as both table and schema base.

@@@python:app/models.py
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TodoBase(SQLModel):
    title: str = Field(min_length=1, max_length=200)
    done: bool = False


class Todo(TodoBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow)
@@@

@@@python
# filename: app/schemas.py
from datetime import datetime

from sqlmodel import SQLModel

from app.models import TodoBase


class TodoCreate(TodoBase):
    pass


class TodoUpdate(SQLModel):
    title: str | None = None
    done: bool | None = None


class TodoRead(TodoBase):
    id: int
    created_at: datetime
@@@
`),
  fence(`
A single module owns the engine and the session dependency.

@@@python:app/db.py
from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL = "sqlite:///./tasky.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
@@@
`),
  fence(`
Routes live in their own module; app/main.py is updated to include them and create tables on startup.

@@@python:app/routes/__init__.py
@@@

@@@python:app/routes/todos.py
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models import Todo
from app.schemas import TodoCreate, TodoRead, TodoUpdate

router = APIRouter(prefix="/todos", tags=["todos"])


@router.get("", response_model=list[TodoRead])
def list_todos(session: Session = Depends(get_session)) -> list[Todo]:
    return list(session.exec(select(Todo).order_by(Todo.id)))


@router.post("", response_model=TodoRead, status_code=201)
def create_todo(payload: TodoCreate, session: Session = Depends(get_session)) -> Todo:
    todo = Todo.model_validate(payload)
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo


def _get_or_404(session: Session, todo_id: int) -> Todo:
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todo


@router.get("/{todo_id}", response_model=TodoRead)
def read_todo(todo_id: int, session: Session = Depends(get_session)) -> Todo:
    return _get_or_404(session, todo_id)


@router.patch("/{todo_id}", response_model=TodoRead)
def update_todo(todo_id: int, payload: TodoUpdate, session: Session = Depends(get_session)) -> Todo:
    todo = _get_or_404(session, todo_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(todo, key, value)
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo


@router.delete("/{todo_id}", status_code=204)
def delete_todo(todo_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(_get_or_404(session, todo_id))
    session.commit()
@@@

@@@python:app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.routes.todos import router as todos_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Tasky", lifespan=lifespan)
app.include_router(todos_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
@@@
`),
  fence(`
Settings come from environment variables with safe defaults. The database module now reads them.

@@@python:app/config.py
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("TASKY_DATABASE_URL", "sqlite:///./tasky.db")
    debug: bool = os.getenv("TASKY_DEBUG", "0") == "1"


settings = Settings()
@@@

@@@python:app/db.py
from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=settings.debug, connect_args=connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
@@@

@@@dotenv:./.env.example
TASKY_DATABASE_URL=sqlite:///./tasky.db
TASKY_DEBUG=0
@@@
`),
  fence(`
Tests use an in-memory SQLite database and override the session dependency.

@@@python:tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
@@@

@@@python:tests/test_todos.py
def test_create_and_list(client):
    res = client.post("/todos", json={"title": "Buy milk"})
    assert res.status_code == 201
    assert res.json()["done"] is False

    items = client.get("/todos").json()
    assert [t["title"] for t in items] == ["Buy milk"]


def test_update_and_delete(client):
    todo_id = client.post("/todos", json={"title": "Write docs"}).json()["id"]
    res = client.patch(f"/todos/{todo_id}", json={"done": True})
    assert res.json()["done"] is True

    assert client.delete(f"/todos/{todo_id}").status_code == 204
    assert client.get(f"/todos/{todo_id}").status_code == 404
@@@

Run the suite with:

@@@bash
pytest -q
@@@
`),
  fence(`
Container setup plus a README. The README replaces nothing earlier; it is new.

@@@dockerfile:Dockerfile
FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
@@@

@@@yaml:docker-compose.yml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      TASKY_DATABASE_URL: sqlite:////data/tasky.db
    volumes:
      - tasky-data:/data
volumes:
  tasky-data:
@@@

@@@markdown
<!-- path: README.md -->
# Tasky

A tiny todo REST API built with FastAPI and SQLModel.

## Run locally

    pip install -e ".[dev]"
    uvicorn app.main:app --reload

## Run with Docker

    docker compose up --build

## Endpoints

- GET /health
- GET, POST /todos
- GET, PATCH, DELETE /todos/{id}
@@@
`),
]

// Response templates for user-pasted roadmaps; picked by step index (cycled).
// Each receives { title, index, slug } and returns markdown with fenced blocks.
export const GENERIC_RESPONSE_TEMPLATES = [
  ({ title, slug }) => fence(`
Starting with "${title}". This adds the entry module and a short note.

@@@python:src/${slug}.py
"""${title}."""


def run() -> None:
    print("${slug}: ready")


if __name__ == "__main__":
    run()
@@@

@@@markdown:docs/${slug}.md
# ${title}

Implemented in src/${slug}.py. Run with python -m src.${slug}.
@@@
`),
  ({ title, slug }) => fence(`
For "${title}" I added a helper module and updated the shared changelog.

@@@python:src/${slug}_utils.py
def describe() -> str:
    return "${title}"
@@@

@@@markdown:CHANGELOG.md
# Changelog

- ${title}
@@@
`),
  ({ title, slug }) => fence(`
"${title}" needs a config file and a quick manual check.

@@@json:config/${slug}.json
{
  "step": "${slug}",
  "enabled": true
}
@@@

@@@bash
python -m src.${slug} --check
@@@
`),
]

export const MOCK_ERROR = {
  message: 'arena2api returned 503 Service Unavailable: no upstream session token available',
  hint: 'arena2api returned 503 - check that the arena2api Chrome tab is open and pushing tokens',
}

// Two pre-recorded jobs shown in Recent jobs. Log entries are
// [seconds after created_at, level, message]. Prompts and artifacts are derived
// from the roadmap + responses when the job is loaded (see src/lib/mockEngine.js).
export const SAMPLE_JOBS = [
  {
    id: 'job-sample-done',
    created_at: '2026-09-30T14:02:11.000Z',
    finished_at: '2026-09-30T14:02:29.000Z',
    status: 'done',
    config: { arena_url: 'http://localhost:9090', model: 'gpt-4o', step_delay_seconds: 2 },
    project_context: SAMPLE_PROJECT_CONTEXT,
    roadmap_md: SAMPLE_ROADMAP,
    failed_step: null,
    error: null,
    log: [
      [0, 'info', 'Job job-sample-done created (7 steps, model gpt-4o)'],
      [0, 'info', 'POST http://localhost:9090/v1/chat/completions - step 1 "Project skeleton"'],
      [2, 'ok', 'Step 1 done - 3 files (1 unnamed block kept in transcript)'],
      [2, 'info', 'POST http://localhost:9090/v1/chat/completions - step 2 "Data models"'],
      [5, 'ok', 'Step 2 done - 2 files'],
      [5, 'info', 'POST http://localhost:9090/v1/chat/completions - step 3 "Database layer"'],
      [7, 'ok', 'Step 3 done - 1 file'],
      [7, 'info', 'POST http://localhost:9090/v1/chat/completions - step 4 "CRUD endpoints"'],
      [10, 'ok', 'Step 4 done - 3 files (replaced app/main.py from step 1)'],
      [10, 'info', 'POST http://localhost:9090/v1/chat/completions - step 5 "Settings from environment"'],
      [12, 'ok', 'Step 5 done - 3 files (replaced app/db.py from step 3)'],
      [12, 'info', 'POST http://localhost:9090/v1/chat/completions - step 6 "Tests for the todo routes"'],
      [15, 'ok', 'Step 6 done - 2 files (1 unnamed block kept in transcript)'],
      [15, 'info', 'POST http://localhost:9090/v1/chat/completions - step 7 "Docker and docs"'],
      [18, 'ok', 'Step 7 done - 3 files'],
      [18, 'ok', 'Job finished - 7/7 steps, 15 files ready for ZIP'],
    ],
  },
  {
    id: 'job-sample-error',
    created_at: '2026-09-30T16:40:05.000Z',
    finished_at: '2026-09-30T16:40:38.000Z',
    status: 'error',
    config: { arena_url: 'http://localhost:9090', model: 'claude-3-5-sonnet', step_delay_seconds: 2 },
    project_context: SAMPLE_PROJECT_CONTEXT,
    roadmap_md: SAMPLE_ROADMAP,
    failed_step: 3,
    error: MOCK_ERROR.message,
    log: [
      [0, 'info', 'Job job-sample-error created (7 steps, model claude-3-5-sonnet)'],
      [0, 'info', 'POST http://localhost:9090/v1/chat/completions - step 1 "Project skeleton"'],
      [3, 'ok', 'Step 1 done - 3 files (1 unnamed block kept in transcript)'],
      [3, 'info', 'POST http://localhost:9090/v1/chat/completions - step 2 "Data models"'],
      [6, 'ok', 'Step 2 done - 2 files'],
      [6, 'info', 'POST http://localhost:9090/v1/chat/completions - step 3 "Database layer"'],
      [16, 'warn', 'arena2api 503 on step 3 - retrying in 5s (attempt 1 of 3)'],
      [26, 'warn', 'arena2api 503 on step 3 - retrying in 5s (attempt 2 of 3)'],
      [33, 'error', 'Step 3 failed: ' + MOCK_ERROR.message],
      [33, 'error', 'Job stopped - steps 4-7 left pending'],
    ],
  },
]
