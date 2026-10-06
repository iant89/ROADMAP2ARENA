"""Routes for the Job history detail view: transcript (JSON + standalone HTML
export), per-file listing/download, and clone source data."""
from __future__ import annotations

import html
import posixpath
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from artifact_extractor import clean_zip_path
from database import db
from orchestrator import now_iso

router = APIRouter(prefix="/api")

FENCE_RE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.M | re.S)


async def _job_or_404(job_id: str) -> dict:
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0, "log": 0})
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


async def _transcript(job_id: str) -> dict:
    job = await _job_or_404(job_id)
    steps = await db.steps.find({"job_id": job_id}, {"_id": 0, "artifacts.content": 0}).sort("index", 1).to_list(None)
    turns = []
    for s in steps:
        if s["status"] == "pending" and not s.get("prompt"):
            continue
        turns.append({
            "step_index": s["index"], "step_title": s["title"], "status": s["status"],
            "prompt": s.get("prompt", ""), "response": s.get("response", ""), "error": s.get("error"),
            "artifact_paths": [a["path"] for a in s.get("artifacts", [])],
            "started_at": s.get("started_at"), "finished_at": s.get("finished_at"),
        })
    return {
        "job_id": job_id, "title": job.get("title"), "status": job["status"], "model": job["model"],
        "arena_url": job["arena_url"], "provider": job.get("provider"), "project_context": job.get("project_context", ""),
        "created_at": job["created_at"], "finished_at": job.get("finished_at"),
        "step_total": job["step_total"], "steps_done": job["steps_done"], "turns": turns,
    }


@router.get("/jobs/{job_id}/transcript")
async def get_transcript(job_id: str):
    """Every sent step as a user (prompt) / assistant (response) turn, in order."""
    return await _transcript(job_id)


# ------------------------------------------------------------------ HTML export
CSS = """
*{box-sizing:border-box}body{margin:0;background:#F5F3EE;color:#1B1E23;font:15px/1.55 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 20px 60px}h1{font-size:22px;margin:0 0 6px}
.meta{color:#565C69;font-size:13px}.meta code{font-size:12px}.card{background:#fff;border:1px solid #DAD5CA;border-radius:12px;padding:16px 18px;margin:16px 0}
.badge{display:inline-block;border:1px solid #DAD5CA;border-radius:999px;padding:1px 9px;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase}
.s-done{color:#0B6F65;background:#DDEFEB}.s-error{color:#B03A24;background:#F9E1DA}.s-stopped{color:#6E5F44;background:#EEE8DC}
.s-running{color:#8F5A00;background:#F8ECCF}.s-pending,.s-cancelled{color:#6B7585;background:#EDEAE3}.s-queued{color:#4D5B78;background:#E5E8EF}.s-paused{color:#8A6B3E;background:#F3ECDF}
h2{font-size:16px;margin:28px 0 8px;display:flex;gap:10px;align-items:center}.turn{border-radius:12px;padding:12px 14px;margin:10px 0;border:1px solid #DAD5CA}
.user{background:#FBFAF7}.assistant{background:#fff}.role{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#565C69;margin-bottom:6px}
.text{white-space:pre-wrap;word-wrap:break-word}pre{background:#171A1F;color:#E7E2D8;border-radius:8px;padding:12px 14px;overflow:auto;font:12.5px/1.6 'JetBrains Mono',Menlo,Consolas,monospace;margin:8px 0}
.lang{font:600 11px/1 Menlo,Consolas,monospace;color:#565C69;margin-top:10px}.err{color:#B03A24;background:#F9E1DA;border-radius:8px;padding:8px 12px}
.files{font-size:12.5px;color:#0B6F65}footer{color:#565C69;font-size:12px;margin-top:40px}
"""


def _render_text(text: str) -> str:
    """Escape text; fenced blocks become <pre>, the rest keeps its line breaks."""
    out, pos = [], 0
    for m in FENCE_RE.finditer(text):
        if m.start() > pos:
            out.append(f'<div class="text">{html.escape(text[pos:m.start()].strip(chr(10)))}</div>')
        info = m.group(1).strip()
        if info:
            out.append(f'<div class="lang">{html.escape(info)}</div>')
        out.append(f"<pre><code>{html.escape(m.group(2))}</code></pre>")
        pos = m.end()
    if pos < len(text):
        out.append(f'<div class="text">{html.escape(text[pos:].strip(chr(10)))}</div>')
    return "".join(out)


def _badge(status: str) -> str:
    return f'<span class="badge s-{html.escape(status)}">{html.escape(status)}</span>'


def transcript_html(t: dict) -> str:
    e = html.escape
    parts = [
        "<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(t['title'] or 'Untitled roadmap')} - ROADMAP2ARENA transcript</title>",
        f"<style>{CSS}</style></head><body><main>",
        f"<h1>{e(t['title'] or 'Untitled roadmap')}</h1>",
        f'<div class="meta">{_badge(t["status"])} &nbsp;{t["steps_done"]}/{t["step_total"]} steps done &middot; '
        f'model <code>{e(t["model"])}</code> via '
        + (f'{e(t["provider"]["name"])} ' if t.get("provider") else "")
        + f'<code>{e(t["arena_url"])}</code></div>',
        f'<div class="meta">Job <code>{e(t["job_id"])}</code> &middot; created {e(t["created_at"])}'
        + (f' &middot; finished {e(t["finished_at"])}' if t.get("finished_at") else "") + "</div>",
    ]
    if t["project_context"].strip():
        parts.append(f'<div class="card"><div class="role">Project context</div><div class="text">{e(t["project_context"])}</div></div>')
    if not t["turns"]:
        parts.append('<div class="card">No steps were sent yet.</div>')
    for turn in t["turns"]:
        parts.append(f'<section id="step-{turn["step_index"]}"><h2>Step {turn["step_index"]}: {e(turn["step_title"])} {_badge(turn["status"])}</h2>')
        parts.append(f'<div class="turn user"><div class="role">User</div>{_render_text(turn["prompt"] or "(prompt not recorded)")}</div>')
        if turn["response"]:
            parts.append(f'<div class="turn assistant"><div class="role">Assistant</div>{_render_text(turn["response"])}')
            if turn["artifact_paths"]:
                parts.append(f'<div class="files">Files: {e(", ".join(turn["artifact_paths"]))}</div>')
            parts.append("</div>")
        elif turn["status"] == "running":
            parts.append('<div class="turn assistant"><div class="role">Assistant</div><div class="text">(waiting for response)</div></div>')
        if turn["error"]:
            parts.append(f'<div class="err">{e(turn["error"])}</div>')
        parts.append("</section>")
    parts.append(f"<footer>Exported from ROADMAP2ARENA at {e(now_iso())}.</footer></main></body></html>")
    return "\n".join(parts)


@router.get("/jobs/{job_id}/transcript.html")
async def export_transcript(job_id: str):
    """Standalone, self-contained HTML (inline CSS, no scripts or external assets)."""
    t = await _transcript(job_id)
    return Response(transcript_html(t), media_type="text/html; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="roadmap2arena-{job_id[:8]}-transcript.html"'})


# ------------------------------------------------------------------ files
async def _files(job_id: str) -> dict[str, dict]:
    """Latest version of every artifact path (from done steps), with its version history."""
    files: dict[str, dict] = {}
    async for s in db.steps.find({"job_id": job_id, "status": "done"}, {"_id": 0, "index": 1, "artifacts": 1}).sort("index", 1):
        for a in s.get("artifacts", []):
            prev = files.get(a["path"])
            files[a["path"]] = {"path": a["path"], "content": a["content"], "step_index": s["index"],
                                "versions": [*(prev["versions"] if prev else []), s["index"]]}
    return files


@router.get("/jobs/{job_id}/files")
async def list_files(job_id: str):
    await _job_or_404(job_id)
    out = []
    for f in sorted((await _files(job_id)).values(), key=lambda x: x["path"]):
        zip_path, reason = clean_zip_path(f["path"])
        out.append({"path": f["path"], "step_index": f["step_index"], "versions": f["versions"],
                    "size": len(f["content"].encode("utf-8")), "zip_path": None if reason else zip_path,
                    "zip_skip_reason": reason})
    return out


@router.get("/jobs/{job_id}/files/download")
async def download_file(job_id: str, path: str = Query(..., min_length=1), step: int | None = Query(None, ge=1, le=100_000)):
    """One artifact as an attachment: the latest version, or the version from ?step=N."""
    await _job_or_404(job_id)
    if step is None:
        f = (await _files(job_id)).get(path)
        content = f["content"] if f else None
    else:
        s = await db.steps.find_one({"job_id": job_id, "index": step, "status": "done"}, {"_id": 0, "artifacts": 1})
        content = next((a["content"] for a in (s or {}).get("artifacts", []) if a["path"] == path), None)
    if content is None:
        raise HTTPException(status_code=404, detail=f'File "{path}" not found in job {job_id}' + (f" step {step}" if step else ""))
    name = posixpath.basename(path.replace("\\", "/").rstrip("/")) or "file.txt"
    ascii_name = re.sub(r'[^A-Za-z0-9._-]', "_", name)
    return Response(content, media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"})


# ------------------------------------------------------------------ clone
@router.get("/jobs/{job_id}/clone-source")
async def clone_source(job_id: str):
    """Inputs of a job for a prefilled "Clone job" form; POST /api/jobs with cloned_from to submit."""
    job = await _job_or_404(job_id)
    return {"source_job_id": job_id, "title": job.get("title"), "arena_url": job["arena_url"], "model": job["model"],
            "provider": job.get("provider"), "project_id": job.get("project_id"),
            "project_name": job.get("project_name"),
            "project_context": job.get("project_context_override", job.get("project_context", "")),
            "roadmap_md": job.get("roadmap_md", ""), "step_total": job["step_total"]}
