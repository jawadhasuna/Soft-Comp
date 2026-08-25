/* Soft Comp — front end
 *
 * Five agents, streamed one at a time. The page narrates the pipeline as it
 * runs rather than posting a finished result, because the interesting part is
 * the tester rejecting the developer's work and the developer trying again.
 */

const BACKEND =
  localStorage.getItem("backend") ||
  "https://soft-comp-419840293627.us-central1.run.app";

const $ = (id) => document.getElementById(id);
const input = $("task");
const goButton = $("go");
const status = $("status");

const STAGES = ["manager", "research", "developer", "tester", "documentation"];

/* ---------- text helpers ---------- */

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function markdown(text) {
  const lines = escapeHtml(text).split("\n");
  const out = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };

  for (let line of lines) {
    line = line.replace(/`([^`]+)`/g, "<code>$1</code>");
    line = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);

    if (heading) { closeList(); out.push(`<h3>${heading[1]}</h3>`); }
    else if (bullet) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${bullet[1]}</li>`);
    } else if (line.trim() === "") { closeList(); }
    else { closeList(); out.push(`<p>${line}</p>`); }
  }
  closeList();
  return out.join("");
}

/* ---------- a very small Python highlighter ----------
 *
 * One pass, one regex, alternatives in priority order — comments and strings
 * are matched before keywords, so a keyword inside a string stays a string.
 * Trying to colour each kind in a separate pass corrupts the ones already done.
 */

const KEYWORDS = new RegExp(
  "\\b(?:def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|" +
  "try|except|finally|raise|with|lambda|yield|pass|break|continue|True|False|" +
  "None|is|global|nonlocal|assert|del|async|await)\\b"
);

const TOKEN = new RegExp(
  [
    "(#[^\\n]*)",                                        // comment
    "('''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\")",         // triple-quoted
    "('(?:\\\\.|[^'\\\\\\n])*'|\"(?:\\\\.|[^\"\\\\\\n])*\")",  // string
    "\\b(\\d+\\.?\\d*)\\b",                              // number
    KEYWORDS.source,                                     // keyword
    "\\b(?:def|class)\\s+([A-Za-z_]\\w*)",               // name after def/class
  ].join("|"),
  "g"
);

function highlight(code) {
  const source = String(code);
  let out = "";
  let last = 0;

  source.replace(TOKEN, (match, comment, triple, str, num, name, offset) => {
    out += escapeHtml(source.slice(last, offset));
    const body = escapeHtml(match);
    if (comment) out += `<span class="tok-com">${body}</span>`;
    else if (triple || str) out += `<span class="tok-str">${body}</span>`;
    else if (num) out += `<span class="tok-num">${body}</span>`;
    else if (name) {
      const [word, rest] = [match.split(/\s+/)[0], match.slice(match.split(/\s+/)[0].length)];
      out += `<span class="tok-kw">${escapeHtml(word)}</span>` +
             escapeHtml(rest.slice(0, rest.length - name.length)) +
             `<span class="tok-def">${escapeHtml(name)}</span>`;
    } else out += `<span class="tok-kw">${body}</span>`;
    last = offset + match.length;
    return match;
  });

  return out + escapeHtml(source.slice(last));
}

/* ---------- pipeline state ---------- */

function setStage(stage, state) {
  const el = document.querySelector(`[data-stage="${stage}"]`);
  if (el) el.dataset.state = state;
}

function reset() {
  $("pipeline").hidden = false;
  for (const s of STAGES) setStage(s, "");
  for (const id of ["card-manager", "card-research", "card-docs", "card-code", "card-test"]) {
    $(id).hidden = true;
  }
  $("out-manager").textContent = "";
  $("out-research").innerHTML = "";
  $("out-docs").innerHTML = "";
  $("out-code").innerHTML = "";
  $("out-test").textContent = "";
  $("round-tag").textContent = "";
  $("verdict").textContent = "";
  $("verdict").className = "verdict";
}

/* ---------- running a brief ---------- */

async function build() {
  const task = input.value.trim();
  if (task.length < 8) {
    status.textContent = "Describe the program you want in a sentence.";
    status.classList.add("error");
    input.focus();
    return;
  }

  status.classList.remove("error");
  status.textContent = "Sending the brief…";
  goButton.disabled = true;
  input.disabled = true;
  reset();
  setStage("manager", "working");

  try {
    const response = await fetch(`${BACKEND}/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task }),
    });
    if (!response.ok) throw new Error(`Backend returned ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop();
      for (const chunk of chunks) {
        const name = (chunk.match(/^event: (.+)$/m) || [])[1];
        const raw = (chunk.match(/^data: (.+)$/m) || [])[1];
        if (name && raw) handle(name, JSON.parse(raw));
      }
    }
  } catch (error) {
    status.textContent =
      `Couldn't reach the company — ${error.message}. It sleeps when idle; give it a moment and try again.`;
    status.classList.add("error");
    for (const s of STAGES) {
      const el = document.querySelector(`[data-stage="${s}"]`);
      if (el && el.dataset.state === "working") el.dataset.state = "";
    }
  } finally {
    goButton.disabled = false;
    input.disabled = false;
  }
}

function handle(name, data) {
  if (name === "queued") { status.textContent = "Waiting for a free slot…"; return; }

  if (name === "start") { status.textContent = "The manager is reading the brief…"; return; }

  if (name === "manager") {
    setStage("manager", "done");
    setStage("research", "working");
    $("out-manager").textContent = data.plan;
    $("card-manager").hidden = false;
    status.textContent = "Research is noting what applies…";
    return;
  }

  if (name === "research") {
    setStage("research", "done");
    setStage("developer", "working");
    $("out-research").innerHTML = markdown(data.notes);
    $("card-research").hidden = false;
    status.textContent = "The developer is writing it…";
    return;
  }

  if (name === "developer") {
    setStage("developer", "done");
    setStage("tester", "working");
    $("filename").textContent = data.filename;
    $("run-cmd").textContent = `python ${data.filename}`;
    $("out-code").innerHTML = highlight(data.code);
    $("round-tag").textContent = data.fixing ? `fix ${data.round - 1}` : "";
    $("card-code").hidden = false;
    status.textContent = data.fixing ? "Rewritten. Running it again…" : "Running it…";
    return;
  }

  if (name === "tester") {
    $("out-test").textContent = data.output;
    $("card-test").hidden = false;
    $("verdict").textContent = data.passed ? "passed" : "failed";
    $("verdict").className = `verdict ${data.passed ? "pass" : "fail"}`;
    setStage("tester", data.passed ? "done" : "failed");

    if (data.willRetry) {
      setStage("developer", "working");
      status.textContent = `It failed. Sending it back to the developer (attempt ${data.attempts + 1})…`;
    } else {
      setStage("documentation", "working");
      status.textContent = "Writing the documentation…";
    }
    return;
  }

  if (name === "documentation") {
    setStage("documentation", "done");
    $("out-docs").innerHTML = markdown(data.docs);
    $("card-docs").hidden = false;
    return;
  }

  if (name === "retry") {
    status.textContent = `The free tier is busy. Retrying in ${data.seconds} seconds…`;
    return;
  }

  if (name === "error") {
    status.textContent = data.message;
    status.classList.add("error");
    return;
  }

  if (name === "done") {
    const fixes = data.attempts === 0
      ? "first try"
      : `${data.attempts} ${data.attempts === 1 ? "fix" : "fixes"}`;
    const extra = Object.keys(data.files).length - 1;
    status.textContent = data.passed
      ? `Done — ${data.filename} works, ${fixes}.` +
        (extra > 0 ? ` It also created ${extra} other file${extra === 1 ? "" : "s"}.` : "")
      : `Gave up after ${data.attempts} attempts. The last error is above.`;
    return;
  }
}

/* ---------- wiring ---------- */

goButton.addEventListener("click", build);
input.addEventListener("keydown", (e) => { if (e.key === "Enter") build(); });

for (const button of $("examples").querySelectorAll("button")) {
  button.addEventListener("click", () => {
    input.value = button.textContent;
    build();
  });
}
