"""Visual lesson studio and owner-scoped media jobs."""

import asyncio
import fcntl
import hashlib
import html
import shutil
import sys
import time
from pathlib import Path
from typing import Annotated, TextIO, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from pages.shell import page_shell
from services.core.model_prefs import prefs_owner_id
from services.lessons.planner import MAX_BYTES, PlanResult, plan
from services.lessons.storyboard import Board
from services.limits import reserve_daily_quota
from services.persistence.database import database_available, session_scope
from services.persistence.repositories import get_topic, list_topic_papers
from utils.static_assets import static_url

router = APIRouter()
ROOT = Path("data/visual-lessons")
TTL = 86400
workers: set[asyncio.Task[None]] = set()


def owner_dir(request: Request) -> Path:
    owner = prefs_owner_id(request)
    if not owner:
        raise HTTPException(401, "Reload the page to start a session.")
    return ROOT / hashlib.sha256(owner.encode()).hexdigest()


@router.get("/lessons", response_class=HTMLResponse)
async def studio(request: Request, topic: str = "", url: str = "", topic_id: str = "") -> str:
    options = ""
    if topic_id:
        if not database_available():
            raise HTTPException(503, "Database unavailable")
        owner = prefs_owner_id(request)
        if not owner:
            raise HTTPException(401, "Reload to start a session.")
        async for db in session_scope():
            record = await get_topic(db, owner_id=owner, topic_id=topic_id)
            if not record:
                raise HTTPException(404, "Topic not found")
            topic = record.title
            for row in await list_topic_papers(db, topic_id=topic_id):
                if row.url:
                    if not url:
                        url = row.url
                    options += f'<option value="{html.escape(row.url, quote=True)}">{html.escape(row.title or row.url)}</option>'
    body = Path("src/pages/lesson_studio.html").read_text().replace("{{options}}", options)
    body = body.replace("{{topic}}", html.escape(topic[:500], quote=True)).replace(
        "{{url}}", html.escape(url[:2000], quote=True)
    )
    return cast(
        str,
        page_shell(
            "Visual lesson",
            body,
            active="lessons",
            scripts=f'<script src="{static_url("/static/js/lessons.js")}" defer></script>',
            head_extra=f'<link rel="stylesheet" href="{static_url("/static/css/lessons.css")}">',
        ),
    )


@router.post("/api/lessons/plan")
async def generate(
    request: Request,
    topic: str = Form("", max_length=500),
    url: str = Form("", max_length=2000),
    page: int = Form(1, ge=1, le=5000),
    pdf: Annotated[UploadFile | None, File()] = None,
) -> PlanResult:
    data = await pdf.read(MAX_BYTES + 1) if pdf else b""
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Choose a PDF under 10 MiB.")
    if not (topic.strip() or url.strip() or data):
        raise HTTPException(422, "Enter a topic or choose a paper.")
    if limit := await reserve_daily_quota(request, "lesson_plan"):
        raise HTTPException(429, limit)
    return await plan(topic.strip(), url.strip(), data, page)


async def reap(proc: asyncio.subprocess.Process, job: Path, slot: TextIO) -> None:
    try:
        if await proc.wait():
            (job / "failed").touch()
    finally:
        slot.close()


@router.post("/api/lessons/render", status_code=202)
async def render(request: Request, board: Board) -> dict[str, str]:
    base = owner_dir(request)
    base.mkdir(parents=True, exist_ok=True)
    for old in ROOT.glob("*/*/board.json"):
        if old.exists() and old.stat().st_mtime < time.time() - TTL:
            shutil.rmtree(old.parent, ignore_errors=True)
    for index in range(2):
        slot = (ROOT / f"worker-{index}.lock").open("a")
        try:
            fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            slot.close()
    else:
        raise HTTPException(429, "Both render workers are busy. Try again shortly.")
    try:
        if limit := await reserve_daily_quota(request, "lesson_render"):
            raise HTTPException(429, limit)
        job = base / str(uuid4())
        job.mkdir()
        (job / "board.json").write_text(board.model_dump_json())
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "services.lessons.render",
            str(job.resolve()),
            pass_fds=(slot.fileno(),),
            start_new_session=True,
        )
    except BaseException:
        slot.close()
        raise
    task = asyncio.create_task(reap(proc, job, slot))
    workers.add(task)
    task.add_done_callback(workers.discard)
    return {"id": job.name}


def find_job(request: Request, job_id: UUID) -> Path:
    job = owner_dir(request) / str(job_id)
    if not job.is_dir() or job.stat().st_mtime < time.time() - TTL:
        raise HTTPException(404, "Lesson expired or unavailable.")
    return job


@router.get("/api/lessons/{job_id}")
async def status(request: Request, job_id: UUID) -> dict[str, str | Board]:
    job = find_job(request, job_id)
    if (job / "lesson.mp4").exists():
        return {
            "state": "ready",
            "board": Board.model_validate_json((job / "board.json").read_text()),
        }
    if (job / "failed").exists() or time.time() - job.stat().st_mtime > 900:
        return {"state": "failed"}
    return {"state": "rendering"}


@router.get("/api/lessons/{job_id}/video")
async def media(request: Request, job_id: UUID) -> FileResponse:
    path = find_job(request, job_id) / "lesson.mp4"
    if not path.exists():
        raise HTTPException(404, "Video unavailable.")
    return FileResponse(
        path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"}
    )
