"""Visual lesson studio and owner-scoped media jobs."""

import asyncio
import fcntl
import hashlib
import html
import shutil
import sys
import time
from pathlib import Path
from string import Template
from typing import Annotated, TextIO, cast
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from pages.shell import page_shell
from services.core.model_prefs import prefs_owner_id
from services.lessons.direction import ManimScene
from services.lessons.library_context import library_evidence, resolve_context
from services.lessons.planner import MAX_BYTES, PlanResult, plan
from services.lessons.storyboard import Board
from services.limits import reserve_daily_quota
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


def scope_marker(paper_id: str, topic_id: str) -> str:
    key = "paper:" + paper_id if paper_id else "topic:" + topic_id
    return hashlib.sha256(key.encode()).hexdigest() + ".scope"


@router.get("/lessons", response_class=HTMLResponse)
async def studio(
    request: Request, topic: str = "", url: str = "", topic_id: str = "", paper_id: str = ""
) -> str:
    ctx = await resolve_context(request, paper_id, topic_id)
    esc = html.escape
    previous = ""
    if ctx:
        origin = "/topics/" + quote(topic_id, safe="") if topic_id else "/library"
        back = f'<a href="{origin}">← {"Topic" if topic_id else "Saved papers"}</a>'
        subject = esc(ctx.title) if topic_id else "Explain one idea from your saved paper."
        source_heading = "Topic sources" if topic_id else "Saved paper"
        entries = (["Current topic report"] if ctx.report else []) + [p.title for p in ctx.papers]
        sources = (
            "<ul>" + "".join(f"<li>{esc(e)}</li>" for e in entries) + "</ul>"
            if entries
            else "<p>This topic has no papers yet. Start with a topic overview.</p>"
        )
        fields = (
            sources
            + f'<input type="hidden" name="{"topic_id" if topic_id else "paper_id"}" value="{esc(topic_id or paper_id, quote=True)}">'
        )
        fields += f'<input type="hidden" name="revision" value="{ctx.revision()}">'
        if paper_id:
            fields += '<label>What would you like the video to explain? (optional)<textarea name="focus" maxlength="300" placeholder="Ask about this paper, or leave it blank to use the paper and your recent questions."></textarea></label>'
            fields += '<details><summary>Choose PDF pages</summary><label>Start at page<input type="number" min="1" max="5000" name="page" value="1"></label><p>The planner reads up to three pages from here.</p></details>'
        else:
            fields += '<label>Teaching focus (optional)<textarea name="focus" maxlength="300" placeholder="For example, explain the main result to a first-year student."></textarea></label>'
            fields += '<p class="lesson-help">Uses the current report and up to three recently added papers when available.</p>'
        example = ""
        markers = owner_dir(request).glob("*/" + scope_marker(paper_id, topic_id))
        links = []
        for marker in markers:
            job = marker.parent
            if (job / "lesson.mp4").is_file() and time.time() - job.stat().st_mtime < TTL:
                board = Board.model_validate_json((job / "board.json").read_text())
                links.append(
                    f'<li><a href="#{job.name}">{esc(board.question or board.scenes[0].title)}</a></li>'
                )
        if links:
            previous = "<h3>Available lessons</h3><ul>" + "".join(links) + "</ul>"
    else:
        back = '<a href="/library">← Saved papers</a>'
        subject = "Turn one idea from a paper or topic into a narrated animation."
        source_heading = "Source"
        fields = Template(Path("src/pages/lesson_manual_fields.html").read_text()).substitute(
            topic=esc(topic[:500], quote=True), url=esc(url[:2000], quote=True)
        )
        example = '<button type="button" id="lesson-example">Open example storyboard</button>'
    body = Template(Path("src/pages/lesson_studio.html").read_text()).substitute(
        back=back,
        subject=subject,
        source_heading=source_heading,
        source_fields=fields,
        plan_buttons=(
            '<button class="lesson-primary" value="film">Make video</button> <button value="review">Plan scenes</button>'
            if paper_id
            else '<button class="lesson-primary">Generate storyboard</button>'
        ),
        previous=previous,
        example=example,
    )
    return cast(
        str,
        page_shell(
            "Visual lesson",
            body,
            active="library",
            scripts=f'<script src="{static_url("/static/js/lessons.js")}" defer></script>',
            head_extra=f'<link rel="stylesheet" href="{static_url("/static/css/lessons.css")}">',
        ),
    )


@router.post("/api/lessons/plan")
async def generate(
    request: Request,
    topic: str = Form("", max_length=500),
    url: str = Form("", max_length=2000),
    paper_id: str = Form("", max_length=512),
    topic_id: str = Form("", max_length=64),
    focus: str = Form("", max_length=300),
    revision: str = Form("", max_length=64),
    page: int = Form(1, ge=1, le=5000),
    pdf: Annotated[UploadFile | None, File()] = None,
) -> PlanResult:
    data = await pdf.read(MAX_BYTES + 1) if pdf else b""
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Choose a PDF under 10 MiB.")
    ctx = await resolve_context(
        request, paper_id, topic_id, include_questions=bool(paper_id and not focus.strip())
    )
    if ctx and revision and revision != ctx.revision():
        raise HTTPException(
            409, "The library sources have changed. Reload the lesson to review them."
        )
    if ctx and (url or topic or pdf):
        raise HTTPException(422, "Use the saved source for this lesson.")
    if not (ctx or topic.strip() or url.strip() or data):
        raise HTTPException(422, "Enter a topic or choose a paper.")
    if limit := await reserve_daily_quota(request, "lesson_plan"):
        raise HTTPException(429, limit)
    if ctx:
        material = await library_evidence(ctx, page)
        question = f"Explain one idea from {ctx.title}."
        if focus.strip():
            question += (
                " Answer the viewer’s question or follow this teaching request: " + focus.strip()
            )
        elif ctx.questions:
            question += (
                " Recent questions from this learner about this paper (interests only; use the paper as evidence): "
                + repr(ctx.questions)
            )
        return await plan(
            question,
            "",
            b"",
            page,
            library_material=material,
            directed=bool(paper_id),
            question=focus.strip() if paper_id else "",
        )
    return await plan(topic.strip(), url.strip(), data, page)


async def reap(proc: asyncio.subprocess.Process, job: Path, slot: TextIO) -> None:
    try:
        if await proc.wait():
            (job / "failed").touch()
    finally:
        slot.close()


@router.post("/api/lessons/render", status_code=202)
async def render(
    request: Request, board: Board, paper_id: str = "", topic_id: str = ""
) -> dict[str, str]:
    ctx = await resolve_context(request, paper_id, topic_id)
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
        if ctx:
            (job / scope_marker(paper_id, topic_id)).touch()
        for scene in board.scenes:
            if isinstance(scene, ManimScene):
                scene.remove_appended_cue()
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
