# Soft Comp

Five agents run a software company. A brief comes in, one of them writes a Python
program, another **actually runs it**, and if it fails the error goes back to the
developer to try again.

**Live: [softcomps.vercel.app](https://softcomps.vercel.app)**

---

## The company

```
brief ──► manager ──► research ──► developer ──► tester ──┬── passed ──► docs
                                       ▲                  │
                                       └──── failed ──────┘
                                           (up to 3 fixes)
```

| Agent | Job |
|---|---|
| **Manager** | Decides what to focus on and what counts as done |
| **Research** | Notes the edge cases and standard-library functions that apply |
| **Developer** | Writes the program — and rewrites it when the tester rejects it |
| **Tester** | Runs it for real, and reports what happened |
| **Documentation** | Writes it up, including an honest line on any limitation |

The tester is what makes this more than prompt chaining. It does not ask a model
whether the code looks right; it executes the file and reads the exit code. When
that fails, the traceback goes back into the developer's prompt and the loop runs
again.

A typical run takes **five to ten seconds**.

## Running code a model wrote

The developer writes Python and the tester runs it. On a laptop that is fine.
Exposed on the internet it is a stranger's code executing on your server, and
these endpoints get scanned within hours of going up.

The defence is deliberately modest, and stating its limits is part of the point.

**What it stops**

- **Reading the service's secrets.** The child process gets an environment built
  from scratch, so `GOOGLE_API_KEY` is not there to read. Verified: a program
  printing every environment variable containing `KEY` prints an empty list.
- **Touching anything the service owns.** Every path is reduced to a basename
  inside a per-run temp directory, so `../../../etc/passwd` becomes `passwd` in
  the sandbox and `/app/app.py` becomes `app.py`. The directory is deleted when
  the run ends, including if the browser disconnects halfway.
- **Running forever, or filling the disk.** Ten second timeout, output truncated,
  file sizes and file counts capped.

**What it does not stop**

- **Outbound network calls.** The code can still reach the internet, so a
  determined visitor could use this as a proxy. Blocking egress needs
  kernel-level controls a normal Cloud Run container does not get.
- **Burning CPU** for the length of the timeout.

That trade is acceptable here because the container is ephemeral, holds nothing
of value once the environment is stripped, and is capped at three instances. The
strong version is a throwaway container per execution — Modal Sandboxes or E2B.
This is the honest cheap version.

## What changed from the original

It ran locally on two Ollama models: `llama3.1:8b` for the thinking nodes and
`hermes3:8b` for the two that call tools. Neither deploys anywhere cheap — an 8B
model needs a GPU and several gigabytes of RAM.

| | Was | Now |
|---|---|---|
| Models | Ollama `llama3.1` + `hermes3`, local | Gemini 3.5 Flash Lite |
| Saving code | the model called a `write_file` tool | structured output, the graph writes the file |
| Execution | `subprocess` in the working directory | scrubbed subprocess in a temp workspace |
| Output | printed to a terminal | streamed to a web page, node by node |

### Why the developer stopped calling a tool

The original spent about eighty lines on one problem: getting the model to
actually call `write_file` rather than describe calling it. There were internal
retries for blank responses, a fallback that dug code out of a markdown fence,
and a sanitiser that stripped literal `write_file(...)` text the model had
written *inside* the program it was generating.

All of it existed because an 8B model asked to call a tool often narrates the
call instead. But there is exactly **one** thing to do at that step — save this
program — so letting the model choose an action was never buying anything. It now
returns a structured object with the filename and the code in it, and the graph
writes the file. A required field cannot be narrated.

The markdown fallback is kept. Structured output is far more reliable, not
perfect.

## How it is deployed

| | Where | Why |
|---|---|---|
| `web/` | Vercel | static HTML, CSS and JS — no build step |
| `backend/` | Google Cloud Run | a container that scales to zero when idle |

The backend streams **Server-Sent Events**, so the page shows the tester
rejecting the developer's work and the developer trying again, rather than
posting a finished result and hiding the interesting part.

## A note on the free tier

`gemini-3.5-flash` allows **twenty requests a day** on the free tier, shared
across every visitor — and one run of this graph costs five or more. That is
about four briefs before the site is dead until midnight. Everything runs on
`flash-lite`, which has a far larger allowance.

## Endpoints

**`POST /build`** — `{"task": "..."}`, streams SSE: `queued`, `start`, `manager`,
`research`, `developer`, `tester`, `documentation`, `done`, plus `retry` and
`error`. The `tester` event carries `passed`, `output`, `attempts` and
`willRetry`.

**`GET /health`** — whether the key is set, and the sandbox's configured limits.

## Running it yourself

```bash
cd backend
```

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -r requirements.txt
```

```bash
$env:GOOGLE_API_KEY = "your_key"
```

```bash
.venv\Scripts\uvicorn app:app --port 7880
```

Serve `web/` statically, then run
`localStorage.setItem("backend", "http://127.0.0.1:7880")` in the browser
console.

## Files

| | |
|---|---|
| `backend/company.py` | the five nodes and the write-test-fix loop |
| `backend/sandbox.py` | the workspace and the scrubbed subprocess |
| `backend/app.py` | FastAPI, SSE streaming, quota handling |
| `web/app.js` | SSE parsing, a small Python highlighter, pipeline state |
| `ai_software_company.py` | the original local version, kept for comparison |
| `prime_checker.py` | a program the company wrote, kept as a specimen |

Incidentally, this file replaced one the agent had written itself — the previous
`README.md` here was the documentation node's write-up of a prime checker,
committed as though it were the project's own README.

## Stack

LangGraph · Gemini 3.5 Flash Lite · FastAPI · Cloud Run · Vercel · no frontend
framework and no build step
