#AI Software Company with Multi Agents: Manager, Research, Developer, Tester, Documentation agents.
import os
import re
import subprocess
from langchain_core.tools import tool

@tool
def read_file(filepath: str) -> str:
    """Read and return the contents of a file."""
    try:
        with open(filepath, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"

@tool
def write_file(filepath: str, content: str) -> str:
    """Write content to a file, overwriting it."""
    try:
        with open(filepath, "w") as f:
            f.write(content)
        return f"Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing file: {e}"

@tool
def run_python(filepath: str) -> str:
    """Run a Python script and return its output or error."""
    try:
        result = subprocess.run(
            ["python", filepath],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return f"Success! Output:\n{result.stdout}"
        else:
            return f"Error occurred:\n{result.stderr}"
    except Exception as e:
        return f"Error running script: {e}"


from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.graph import StateGraph, END
from typing import TypedDict, List

# Models: hermes3:8b for tool-heavy work, llama3.1:8b for reasoning/writing
reasoning_llm = ChatOllama(model="llama3.1:8b", temperature=0.3)
tool_llm = ChatOllama(model="hermes3:8b", temperature=0)

# Developer and Tester get real tools (read/write/run files)
developer_agent = create_agent(tool_llm, [read_file, write_file, run_python])
tester_agent = create_agent(tool_llm, [read_file, write_file, run_python])

# 1. Shared state that flows through the whole graph
class CompanyState(TypedDict):
    task: str
    research_notes: str
    filepath: str
    code_status: str      # "untested", "passed", "failed"
    test_output: str
    fix_attempts: int
    documentation: str
    final_summary: str

# 2. Manager node — kicks things off, decides the file to work in
def manager_node(state: CompanyState) -> CompanyState:
    print("\n[Manager] Planning task...")
    prompt = f"A software task has come in: '{state['task']}'. In one short sentence, what should the developer focus on first?"
    response = reasoning_llm.invoke(prompt)
    print(f"[Manager] {response.content}")
    return state

# 3. Research node — looks up anything relevant before coding starts
def research_node(state: CompanyState) -> CompanyState:
    print("\n[Research] Gathering notes...")
    prompt = f"Task: {state['task']}. Give 2-3 brief best-practice notes a developer should follow when implementing this in Python."
    response = reasoning_llm.invoke(prompt)
    state["research_notes"] = response.content
    print(f"[Research] {response.content}")
    return state


def _extract_and_sanitize_code(last_message: str):
    """
    Pull the first ```python ... ``` (or ``` ... ```) block out of a model response
    and strip out any lines where the model narrates a fake tool call (e.g. writes
    'write_file(...)' as literal text inside the code block instead of actually
    invoking the tool). Returns None if no usable code was found.
    """
    code_match = re.search(r"```(?:python)?\n(.*?)```", last_message, re.DOTALL)
    if not code_match:
        return None

    code = code_match.group(1)

    # Some models narrate a fake tool call INSIDE the code block instead of
    # actually invoking write_file/read_file/run_python. Cut the code off right
    # before the first occurrence of any of these, since everything after tends
    # to be bogus "saving" narration rather than real code.
    for bad_call in ("write_file(", "read_file(", "run_python("):
        idx = code.find(bad_call)
        if idx != -1:
            code = code[:idx]

    code = code.strip()
    return code if code else None


DEVELOPER_MAX_INTERNAL_RETRIES = 3  # retries within one node call, before giving up on this round


# 4. Developer node — writes/fixes the code
def developer_node(state: CompanyState) -> CompanyState:
    print(f"\n[Developer] Working on {state['filepath']} (attempt {state['fix_attempts'] + 1})...")

    # Branch on whether the file actually exists on disk — NOT just on code_status.
    # code_status can be "failed" even when the file was never successfully written
    # (e.g. the model returned a blank response last round), and in that case asking
    # the model to "read the file and fix the bug" just confuses it, since there is
    # no file and no bug to fix — there's just nothing written yet.
    file_exists = os.path.exists(state["filepath"])

    if state["code_status"] == "failed" and file_exists:
        # Real fix mode — a file exists, and it failed for a real reason
        base_prompt = (
            f"Task: {state['task']}\n"
            f"Research notes: {state['research_notes']}\n\n"
            f"The following code in {state['filepath']} failed with this error:\n{state['test_output']}\n\n"
            f"Read the file, fix the bug, and you MUST call the write_file tool with the corrected "
            f"code to actually save it — do not just show the code in your response, call the tool. "
            f"Do not write calls to write_file, read_file, or run_python as literal text in your code — "
            f"those are tools you invoke, not Python functions available inside the script. "
            f"Then briefly confirm what you changed."
        )
    else:
        # First pass, OR a "failed" status where no file actually exists yet
        # (previous round never managed to save anything) — treat as a fresh write.
        if state["code_status"] == "failed" and not file_exists:
            print(f"[Developer] Note: {state['filepath']} does not exist yet (previous attempt saved nothing) — writing fresh instead of fixing.")
        base_prompt = (
            f"Task: {state['task']}\n"
            f"Research notes: {state['research_notes']}\n\n"
            f"Write a complete Python solution. You MUST call the write_file tool with "
            f"filepath='{state['filepath']}' to actually save it — do not just show the code in "
            f"your response, calling the tool is the only way it gets saved. Keep it simple and "
            f"runnable standalone. Do not write calls to write_file, read_file, or run_python as "
            f"literal text inside the code — those are tools you invoke, not Python functions "
            f"available inside the script."
        )

    # Internal retry loop: hermes3:8b occasionally returns a totally blank
    # response or fails to call the tool AND has no code block. Retry a few
    # times in-node before giving up, so a single flaky generation doesn't
    # burn a whole fix_attempt (and, worse, leave the file missing entirely).
    for attempt in range(1, DEVELOPER_MAX_INTERNAL_RETRIES + 1):
        prompt = base_prompt
        if attempt > 1:
            prompt += (
                f"\n\n(Retry {attempt}: your previous response did not call the write_file tool "
                f"and contained no usable code. Please call the write_file tool directly this time.)"
            )

        result = developer_agent.invoke({"messages": [("user", prompt)]})
        last_message = result["messages"][-1].content
        print(f"[Developer] {last_message}")

        tool_was_called = any(
            getattr(m, "name", None) == "write_file"
            for m in result["messages"]
        )

        if tool_was_called:
            break  # success — real tool call happened, nothing more to do

        if not last_message.strip():
            print(f"[Developer] ⚠ Empty response from model (internal retry {attempt}/{DEVELOPER_MAX_INTERNAL_RETRIES}).")
            continue

        print("[Developer] ⚠ write_file was not called by the agent — extracting code manually.")
        code = _extract_and_sanitize_code(last_message)
        if code:
            write_file.invoke({"filepath": state["filepath"], "content": code})
            print(f"[Developer] Fallback: wrote sanitized code to {state['filepath']}")
            break  # success — something usable was saved

        print(f"[Developer] ⚠ No usable code block found in response (internal retry {attempt}/{DEVELOPER_MAX_INTERNAL_RETRIES}).")
    else:
        print(f"[Developer] ⚠ Gave up after {DEVELOPER_MAX_INTERNAL_RETRIES} internal retries — nothing was saved this round.")

    return state

# 5. Tester node — runs the code, checks for errors
def tester_node(state: CompanyState) -> CompanyState:
    print(f"\n[Tester] Running {state['filepath']}...")
    output = run_python.invoke({"filepath": state["filepath"]})
    print(f"[Tester] {output}")

    state["test_output"] = output
    if output.startswith("Success"):
        state["code_status"] = "passed"
    else:
        state["code_status"] = "failed"
        state["fix_attempts"] += 1
    return state

# 6. Documentation node — writes a README once tests pass
def documentation_node(state: CompanyState) -> CompanyState:
    print("\n[Documentation] Writing README...")
    code = read_file.invoke({"filepath": state["filepath"]})
    prompt = (
        f"Task: {state['task']}\n\nHere is the final working code:\n{code}\n\n"
        f"Write a short README.md explaining what it does, how to run it, and any dependencies."
    )
    response = reasoning_llm.invoke(prompt)
    state["documentation"] = response.content
    write_file.invoke({"filepath": "README.md", "content": response.content})
    print(f"[Documentation] README.md written")
    return state

# 7. Manager final node — wraps up with a summary
def manager_summary_node(state: CompanyState) -> CompanyState:
    print("\n[Manager] Writing final summary...")
    success = state["code_status"] == "passed"
    prompt = (
        f"Task: {state['task']}\n"
        f"Fix attempts needed: {state['fix_attempts']}\n"
        f"Final status: {'PASSED — code runs successfully' if success else 'FAILED — code still does not run'}\n"
        f"Last test output: {state['test_output']}\n\n"
        f"Write a 2-3 sentence executive summary of how this went for a stakeholder. "
        f"Be honest and accurate about the final status above — "
        f"{'do not describe this as a failure' if success else 'do NOT describe this as a success or say it was delivered working; state plainly that it failed'}."
    )
    response = reasoning_llm.invoke(prompt)
    state["final_summary"] = response.content
    print(f"[Manager] {response.content}")
    return state
def route_after_test(state: CompanyState) -> str:
    if state["code_status"] == "passed":
        return "documentation"
    if state["fix_attempts"] >= 3:
        print("\n[System] Max fix attempts reached — escalating to Manager as failed.")
        return "manager_summary"
    return "developer"  # loop back to fix

graph = StateGraph(CompanyState)

graph.add_node("manager", manager_node)
graph.add_node("research", research_node)
graph.add_node("developer", developer_node)
graph.add_node("tester", tester_node)
graph.add_node("documentation", documentation_node)
graph.add_node("manager_summary", manager_summary_node)

graph.set_entry_point("manager")
graph.add_edge("manager", "research")
graph.add_edge("research", "developer")
graph.add_edge("developer", "tester")

graph.add_conditional_edges(
    "tester",
    route_after_test,
    {
        "developer": "developer",
        "documentation": "documentation",
        "manager_summary": "manager_summary",
    }
)

graph.add_edge("documentation", "manager_summary")
graph.add_edge("manager_summary", END)

app = graph.compile()
if __name__ == "__main__":
    initial_state = {
        "task": "Write a function that checks if a number is prime, with a few test cases",
        "research_notes": "",
        "filepath": "prime_checker.py",
        "code_status": "untested",
        "test_output": "",
        "fix_attempts": 0,
        "documentation": "",
        "final_summary": "",
    }
    final_state = app.invoke(initial_state)
    print("\n=== DONE ===")
    print(final_state["final_summary"])