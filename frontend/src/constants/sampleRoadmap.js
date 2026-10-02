// Sample inputs for the 'Load sample roadmap' button.

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
