"""A software company of five agents.

A task comes in. The manager decides what to focus on, research writes down the
relevant practices, the developer writes the code, the tester actually runs it,
and if it fails the developer gets the error back and tries again. When it
passes — or the attempts run out — documentation writes it up.

    task ──► manager ──► research ──► developer ──► tester ──┬── passed ──► docs
                                          ▲                  │
                                          └──── failed ──────┘
                                              (up to 3 fixes)

Two things changed from the original, both for the same reason.

It ran on local Ollama models: llama3.1:8b for the thinking nodes and hermes3:8b
for the two that call tools. Neither can be deployed anywhere cheap, so both are
now Gemini.

And the developer no longer *calls a tool* to save its work. It returns a
structured object with the filename and the code in it, and the graph writes the
file. The original spent a lot of effort on this problem — internal retries when
the model returned nothing, a fallback that dug the code out of a markdown
block, a sanitiser that stripped fake `write_file(...)` calls the model had
written as literal text inside the code. All of that existed because an 8B model
asked to call a tool would often narrate calling it instead. There is exactly
one thing to do at that step, so letting the model choose an action was never
buying anything. A required field cannot be narrated.

The fallback that pulls code out of a markdown block is kept. Structured output
is far more reliable, not perfect.
"""

import re
from typing import TypedDict

from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from sandbox import Workspace

# flash-lite throughout: the free tier of gemini-3.5-flash allows twenty
# requests a DAY across all visitors, and one run of this graph costs five or
# more. See the README.
MODEL = "gemini-3.5-flash-lite"
MAX_FIX_ATTEMPTS = 3


class CompanyState(TypedDict):
    task: str
    plan: str
    notes: str
    filename: str
    code: str
    status: str        # "untested" | "passed" | "failed"
    output: str        # whatever the tester saw
    attempts: int
    docs: str
    workspace: object  # the sandbox this run writes into


class Program(BaseModel):
    filename: str = Field(
        description="A short snake_case Python filename, e.g. prime_checker.py"
    )
    code: str = Field(
        description="The complete Python program. It must run standalone with "
                    "no arguments and print something demonstrating it works."
    )
    note: str = Field(
        default="",
        description="One short line on what you wrote, or what you changed."
    )


def _llm():
    # Built per call so the module imports without credentials present.
    return ChatGoogleGenerativeAI(model=MODEL)


def _text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()


def _code_from_prose(text: str) -> str:
    """Last resort: dig a program out of a markdown block.

    Kept from the original. Structured output rarely fails, but when it does
    the model has usually still written perfectly good code inside a fence.
    """
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else ""


# ---------- the five nodes ----------

def manager_node(state: CompanyState) -> CompanyState:
    reply = _llm().invoke(
        f"A software task has come in: {state['task']!r}\n\n"
        "In two sentences, say what the developer should focus on first and "
        "what would count as done. No preamble."
    )
    state["plan"] = _text(reply)
    return state


def research_node(state: CompanyState) -> CompanyState:
    reply = _llm().invoke(
        f"Task: {state['task']}\n\n"
        "Give two or three brief, concrete notes a Python developer should "
        "follow implementing this — edge cases, a standard library function "
        "worth using, a common mistake. Bullet points, no preamble."
    )
    state["notes"] = _text(reply)
    return state


def developer_node(state: CompanyState) -> CompanyState:
    if state["status"] == "failed" and state["code"]:
        prompt = (
            f"Task: {state['task']}\n"
            f"Notes: {state['notes']}\n\n"
            f"This program failed:\n```python\n{state['code']}\n```\n\n"
            f"It was run and produced:\n{state['output']}\n\n"
            "Return the whole corrected program, not a patch. Keep the same "
            "filename."
        )
    else:
        prompt = (
            f"Task: {state['task']}\n"
            f"Focus: {state['plan']}\n"
            f"Notes: {state['notes']}\n\n"
            "Write a complete Python program. It must run standalone with no "
            "arguments, use only the standard library, and print output that "
            "demonstrates it works."
        )

    try:
        program = _llm().with_structured_output(Program).invoke(prompt)
        filename, code, note = program.filename, program.code, program.note
    except Exception:
        # Structured output failed outright — fall back to plain prose and dig
        # the code out of it.
        raw = _text(_llm().invoke(prompt))
        filename, code, note = state["filename"] or "main.py", _code_from_prose(raw), ""

    if not code.strip():
        state["output"] = "The developer returned nothing usable."
        state["status"] = "failed"
        state["attempts"] += 1
        return state

    state["filename"] = state["filename"] or (filename or "main.py")
    state["code"] = code
    state["plan"] = state["plan"] or note
    state["workspace"].write_file(state["filename"], code)
    return state


def tester_node(state: CompanyState) -> CompanyState:
    output = state["workspace"].run_python(state["filename"])
    state["output"] = output
    state["status"] = "passed" if output.startswith("Success") else "failed"
    if state["status"] == "failed":
        state["attempts"] += 1
    return state


def docs_node(state: CompanyState) -> CompanyState:
    verdict = ("It ran and passed." if state["status"] == "passed"
               else f"It still fails after {state['attempts']} attempts.")
    reply = _llm().invoke(
        f"Task: {state['task']}\n\n"
        f"Final program:\n```python\n{state['code']}\n```\n\n"
        f"Test result: {verdict}\n{state['output'][:1500]}\n\n"
        "Write short markdown documentation: what it does, how to run it, and "
        "one honest line on any limitation. No headings above level 3."
    )
    state["docs"] = _text(reply)
    return state


def after_test(state: CompanyState) -> str:
    if state["status"] == "passed":
        return "document"
    return "fix" if state["attempts"] < MAX_FIX_ATTEMPTS else "document"


def build_graph():
    graph = StateGraph(CompanyState)
    graph.add_node("manager", manager_node)
    graph.add_node("research", research_node)
    graph.add_node("developer", developer_node)
    graph.add_node("tester", tester_node)
    graph.add_node("documentation", docs_node)

    graph.set_entry_point("manager")
    graph.add_edge("manager", "research")
    graph.add_edge("research", "developer")
    graph.add_edge("developer", "tester")
    graph.add_conditional_edges("tester", after_test,
                                {"fix": "developer", "document": "documentation"})
    graph.add_edge("documentation", END)
    return graph.compile()


def initial_state(task: str, workspace: Workspace) -> CompanyState:
    return {
        "task": task,
        "plan": "",
        "notes": "",
        "filename": "",
        "code": "",
        "status": "untested",
        "output": "",
        "attempts": 0,
        "docs": "",
        "workspace": workspace,
    }
