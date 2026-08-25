"""HTTP front door for the software company.

Each node's output goes out as it lands, because watching the developer write
something, the tester break it, and the developer fix it is the whole point.
A finished result posted at the end would hide the interesting part.

Every request gets its own sandbox directory, which is deleted when the run
ends — including when the client disconnects halfway through.
"""

import asyncio
import json
import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from company import build_graph, initial_state, MAX_FIX_ATTEMPTS
from sandbox import Workspace, TIMEOUT_SECONDS

app = FastAPI(title="Soft Comp")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.vercel\.app|http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

one_at_a_time = asyncio.Semaphore(1)
MAX_RETRY_WAIT = 65

# What each node is doing, in words the page can show before its output exists.
DOING = {
    "manager": "Deciding what to focus on",
    "research": "Noting the practices that apply",
    "developer": "Writing the program",
    "tester": "Running it",
    "documentation": "Writing it up",
}


class Task(BaseModel):
    task: str = Field(min_length=8, max_length=400)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "gemini_configured": bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")),
        "busy": one_at_a_time.locked(),
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_fix_attempts": MAX_FIX_ATTEMPTS,
    }


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def rate_limit_wait(error: Exception) -> int | None:
    text = str(error)
    if "429" not in text and "RESOURCE_EXHAUSTED" not in text:
        return None
    match = re.search(r"'retryDelay':\s*'(\d+)s'", text)
    return min(int(match.group(1)) + 2, MAX_RETRY_WAIT) if match else 30


async def run_company(task: str):
    graph = build_graph()

    # The workspace lives for exactly one run and is removed afterwards, even
    # if the browser goes away mid-stream.
    with Workspace() as workspace:
        yield sse("start", {"task": task})

        state = initial_state(task, workspace)
        final = state
        round_number = 0

        async for update in graph.astream(state, stream_mode="updates"):
            for node, delta in update.items():
                if delta is None:
                    continue
                final = delta

                if node == "manager":
                    yield sse("manager", {"plan": delta["plan"]})

                elif node == "research":
                    yield sse("research", {"notes": delta["notes"]})

                elif node == "developer":
                    round_number += 1
                    yield sse("developer", {
                        "round": round_number,
                        "filename": delta["filename"],
                        "code": delta["code"],
                        "fixing": round_number > 1,
                    })

                elif node == "tester":
                    yield sse("tester", {
                        "round": round_number,
                        "passed": delta["status"] == "passed",
                        "output": delta["output"],
                        "attempts": delta["attempts"],
                        "willRetry": (delta["status"] == "failed"
                                      and delta["attempts"] < MAX_FIX_ATTEMPTS),
                    })

                elif node == "documentation":
                    yield sse("documentation", {"docs": delta["docs"]})

        yield sse("done", {
            "filename": final["filename"],
            "code": final["code"],
            "passed": final["status"] == "passed",
            "attempts": final["attempts"],
            "output": final["output"],
            "docs": final["docs"],
            "files": workspace.files(),
        })


@app.post("/build")
async def build(body: Task):
    async def stream():
        yield sse("queued", {})
        async with one_at_a_time:
            for attempt in (1, 2):
                try:
                    async for chunk in run_company(body.task):
                        yield chunk
                    return
                except Exception as exc:
                    wait = rate_limit_wait(exc)
                    if wait is None or attempt == 2:
                        yield sse("error", {
                            "message": ("The free tier is busy. Try again in a minute."
                                        if wait else f"{type(exc).__name__}: {exc}")
                        })
                        return
                    yield sse("retry", {"seconds": wait})
                    await asyncio.sleep(wait)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
