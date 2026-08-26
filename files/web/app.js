/* Quote Runner — front end. No build step, no framework, no dependencies.
 *
 * HOW THE ACTIVITY CHECKLIST WORKS — read this before changing it.
 *
 * The steps are REAL. There is no hardcoded sequence and nothing on a timer.
 * POST /api/quote runs the agent to completion and returns the list of tool
 * calls that actually executed, in the order they executed, each carrying the
 * arguments the model chose, the costing engine's verbatim reply, and the real
 * elapsed milliseconds measured server-side around that call.
 *
 * What the browser does is reveal those already-finished steps one after
 * another so the panel fills in rather than appearing at once. That is a
 * presentation choice about WHEN the browser draws a step, and it changes
 * nothing about WHETHER the step happened or how long it took — the duration
 * printed beside each row is the real measured one, not the reveal interval.
 * A single POST was chosen over Server-Sent Events deliberately: one code path
 * and one error path, behaving identically on localhost and on Cloud Run.
 *
 * If a run makes three tool calls, three rows appear. If it makes one, one row
 * appears. The UI never invents a step the agent did not take.
 */

const $ = (id) => document.getElementById(id);

const els = {
  enquiry: $("enquiry"),
  generate: $("generate"),
  hint: $("hint"),
  stamp: $("shop-stamp"),
  activity: $("panel-activity"),
  activityMeta: $("activity-meta"),
  activityNote: $("activity-note"),
  steps: $("steps"),
  quote: $("panel-quote"),
  refusal: $("panel-refusal"),
  error: $("panel-error"),
};

/* `turned` and `nodims` are verbatim enquiries from evals/cases.py, so what the
   demo shows is behaviour the eval set already measures.
 *
 * `oversize` is NOT the eval set's mill_manifold_oversize enquiry, and the
 * reason is worth knowing. That case is a 340x120x60mm part whose ground truth
 * puts it on mill_01, where it does not fit -- but it DOES fit fdm_02,
 * router_01 and lathe_01, so the agent is free to plan it onto a machine that
 * takes it, and across three live runs it refused once, quoted once and
 * returned no figure once. The eval judge scores that against the mill; a
 * demo button cannot. This enquiry is 500x400x200mm, which exceeds the
 * envelope of every machine in the shop, so the blocker is a physical fact
 * rather than a routing choice the model happens to make. Same blocker code,
 * same refusal path, no coin flip. Nothing about the agent was changed to
 * achieve it. */
const EXAMPLES = {
  turned: {
    text:
      "We're after 8 brass knobs machined, roughly 45mm diameter and 30mm " +
      "tall, knurled on the outside. C360 brass. There's no particular " +
      "deadline on these — they're for a restoration project and we'd rather " +
      "they were right than fast.",
    attachments: [],
  },
  oversize: {
    text:
      "Hi — we need one aluminium baseplate machined from 6082, overall " +
      "500 x 400 x 200mm. It's a single solid piece, no joins. Three weeks " +
      "is fine. Can you do it?",
    attachments: [],
  },
  nodims: {
    text: "salam, can you print this? need 2 of them. sent the photo. how much",
    attachments: ["IMG_20260814_2231.jpg"],
  },
};

let pendingAttachments = [];

/* -- formatting ---------------------------------------------------------- */

const num = (v, dp = 2) =>
  Number(v ?? 0).toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });

/* Currency comes from rate_card.json via the API. Never hardcoded here. */
const money = (v, currency) => `${currency} ${num(v)}`;

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text ?? "";
}

function show(el) { el.hidden = false; }
function hide(el) { el.hidden = true; }

function resetResults() {
  hide(els.quote);
  hide(els.refusal);
  hide(els.error);
  els.steps.innerHTML = "";
  els.activityNote.textContent = "";
  els.activityMeta.textContent = "";
}

/* -- agent activity ------------------------------------------------------ */

const MARK = { ok: "✓", warn: "✓", blocked: "✗", error: "✗" };

function stepNode(step) {
  const li = document.createElement("li");
  li.className = "step";

  const head = document.createElement("div");
  head.className = "step-head";

  const mark = document.createElement("span");
  mark.className = `mark ${step.status === "error" ? "err" : ""}`;
  mark.textContent = step.status === "error" ? MARK.error : MARK.ok;

  const name = document.createElement("span");
  name.className = `step-name ${step.status === "error" ? "err" : ""}`;
  name.textContent = step.name;

  const ms = document.createElement("span");
  ms.className = "step-ms";
  /* Both figures are measured server-side. `started_ms` is when the call fired
     relative to the start of the run, `duration_ms` is how long the tool
     itself took. They differ by orders of magnitude on purpose: the costing
     engine is pure Python and returns in single-digit milliseconds, so almost
     all of a run's wall clock is Vertex round-trip time. Printing only the
     duration would make a 2ms engine call look like the whole step. */
  ms.textContent = [
    step.started_ms == null ? null : `+${num(step.started_ms / 1000, 1)}s`,
    step.duration_ms == null ? null : `${step.duration_ms}ms`,
  ].filter(Boolean).join(" · ");

  head.append(mark, name, ms);
  li.append(head);

  if (step.detail) {
    const detail = document.createElement("div");
    detail.className = "step-detail";
    detail.textContent = step.detail;
    li.append(detail);
  }

  if (step.checks && step.checks.length) {
    const ul = document.createElement("ul");
    ul.className = "checks";
    for (const check of step.checks) {
      const item = document.createElement("li");
      item.className = `check ${check.status}`;

      const label = document.createElement("div");
      label.className = "check-label";
      label.textContent = `${check.label}  ${MARK[check.status] || ""}`;
      item.append(label);

      for (const line of check.lines || []) {
        if (!line) continue;
        const div = document.createElement("div");
        div.className = "check-line";
        div.textContent = line;
        item.append(div);
      }
      ul.append(item);
    }
    li.append(ul);
  }
  return li;
}

function revealSteps(steps, onDone) {
  let i = 0;
  const next = () => {
    if (i >= steps.length) { onDone(); return; }
    els.steps.append(stepNode(steps[i]));
    i += 1;
    setTimeout(next, 260);
  };
  next();
}

/* -- quotation ----------------------------------------------------------- */

function fact(dl, label, value) {
  if (value === null || value === undefined || value === "") return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.append(dt, dd);
}

function costRow(table, label, amount, currency, opts = {}) {
  const tr = table.insertRow();
  tr.className = [opts.rule ? "rule" : "", opts.strong ? "strong" : "", opts.sub ? "sub" : ""]
    .filter(Boolean).join(" ");
  const td = tr.insertCell();
  td.textContent = label;
  const td2 = tr.insertCell();
  td2.className = "amount";
  td2.textContent = money(amount, currency);

  if (opts.detail) {
    const detailRow = table.insertRow();
    const cell = detailRow.insertCell();
    cell.className = "detail";
    cell.colSpan = 2;
    cell.textContent = opts.detail;
  }
}

function renderQuote(data) {
  const q = data.quote;
  const c = q.currency || data.currency;

  setText("quote-ref", data.quote_ref);
  setText("quote-request", data.request);

  const dl = $("quote-facts");
  dl.innerHTML = "";
  fact(dl, "Quantity", q.quantity);
  fact(dl, "Material", q.material);
  fact(dl, "Process", q.process);
  fact(dl, "Production", `${num(q.production_hours, 1)} hours`);
  fact(dl, "Lead time", plural(q.lead_days, "day"));
  fact(dl, "Client band", q.client_band);

  setText("total-figure", money(q.price, c));

  /* Provenance line — the engine's floor is what bounded the agent's figure. */
  const pct = q.above_floor_pct;
  const delta =
    Math.abs(pct) < 0.05
      ? "quoted at floor"
      : `quoted +${num(pct, 1)}% above floor`;
  setText("provenance", `Engine floor: ${money(q.price_floor, c)} · ${delta}`);

  const table = $("cost-lines");
  table.innerHTML = "";
  /* The engine's own itemised lines, verbatim. It does not emit a five-way
     material/machine/setup/finishing/margin split — setup is inside the
     machine line, finishing is one labour line — so nothing is re-bucketed. */
  for (const line of q.cost_lines || []) {
    costRow(table, line.label, line.amount, c, { detail: line.detail });
  }
  costRow(table, "Total cost", q.cost_totals.total_cost, c, { rule: true });
  costRow(
    table,
    `Margin at ${num(q.margin_floor * 100, 0)}% floor on revenue`,
    q.margin_amount,
    c,
    { sub: true }
  );
  costRow(table, "Engine price floor", q.price_floor, c, { rule: true });
  costRow(table, "Quoted price", q.price, c, { strong: true });

  if (q.min_job_value_applied) {
    const tr = table.insertRow();
    const cell = tr.insertCell();
    cell.className = "detail";
    cell.colSpan = 2;
    cell.textContent =
      "Minimum job value binds here, not the margin — the admin costs more than the making.";
  }

  show(els.quote);
}

/* -- refusal ------------------------------------------------------------- */

function renderRefusal(data) {
  const r = data.refusal;
  setText("refusal-request", data.request);
  setText("refusal-head", r.headline || "Unable to quote");

  const ul = $("refusal-reasons");
  ul.innerHTML = "";
  /* Blocker messages are engine output, written to be pasted into a client
     email unedited. They are not model prose and are not rewritten here. */
  for (const reason of r.reasons || []) {
    const li = document.createElement("li");
    li.textContent = reason;
    ul.append(li);
  }
  if (!(r.reasons || []).length && r.reasoning) {
    const li = document.createElement("li");
    li.textContent = r.reasoning;
    ul.append(li);
  }
  $("refusal-reasons-block").hidden = !ul.children.length;

  setText("refusal-action", r.recommended_action || "");
  $("refusal-action-block").hidden = !r.recommended_action;

  const notes = [];
  if (!r.engine_consulted) {
    notes.push("No costed result was recorded for this enquiry.");
  }
  if (r.agent_quoted_against_blockers) {
    notes.push(
      "The agent named a figure against a job the engine marked undeliverable. " +
      "The figure was discarded."
    );
  }
  setText("refusal-footnote", notes.join(" "));

  show(els.refusal);
}

/* -- error --------------------------------------------------------------- */

function renderError(err) {
  setText("error-head", err.title || "Something went wrong");
  setText("error-message", err.message || "");
  setText(
    "error-footnote",
    err.kind === "priced_without_tool"
      ? "price_job() takes no price argument. A figure that never passed through " +
        "the costing engine is not a quote and is not shown."
      : ""
  );
  show(els.error);
}

/* -- run ----------------------------------------------------------------- */

function busy(on) {
  els.generate.disabled = on;
  els.generate.textContent = on ? "Working..." : "Generate Quote";
  els.hint.textContent = on ? "Agent running — this takes a few seconds." : "";
}

async function generate() {
  const text = els.enquiry.value.trim();
  if (!text) {
    els.hint.textContent = "Describe the part first.";
    els.enquiry.focus();
    return;
  }

  resetResults();
  show(els.activity);
  busy(true);

  let data;
  try {
    const res = await fetch("/api/quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: text, attachments: pendingAttachments }),
    });
    data = await res.json();
  } catch (e) {
    busy(false);
    renderError({
      title: "Could not reach the server",
      message: `${e}. The request never completed.`,
    });
    return;
  }

  busy(false);

  /* Every /api/quote response carries meta too, including the error paths, so
     a cold start whose /api/meta fetch lost the race still ends up correct. */
  applyMeta(data.meta);

  const steps = data.steps || [];
  els.activityMeta.textContent = [
    plural(steps.length, "tool call"),
    data.elapsed_ms != null ? `${num(data.elapsed_ms / 1000, 1)}s` : null,
  ].filter(Boolean).join(" · ");

  if (!steps.length) {
    els.activityNote.textContent = "No tool calls were recorded for this run.";
  }

  revealSteps(steps, () => {
    if (data.outcome === "quote") renderQuote(data);
    else if (data.outcome === "refusal") renderRefusal(data);
    else renderError(data.error || { title: "Unknown result", message: "" });

    const panel = data.outcome === "quote" ? els.quote
      : data.outcome === "refusal" ? els.refusal : els.error;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

/* -- wiring -------------------------------------------------------------- */

els.generate.addEventListener("click", generate);

els.enquiry.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") generate();
});

els.enquiry.addEventListener("input", () => { pendingAttachments = []; });

for (const button of document.querySelectorAll(".example")) {
  button.addEventListener("click", () => {
    const example = EXAMPLES[button.dataset.example];
    els.enquiry.value = example.text;
    /* Filenames only, exactly as the eval case carries them. Not file upload. */
    pendingAttachments = example.attachments.slice();
    els.enquiry.focus();
  });
}

/* Download Quote is window.print() against the print stylesheet. No server-side
   PDF. The breakdown is expanded first and restored afterwards: a closed
   <details> has its content hidden by the browser's own UA styles, which a
   print rule on the child element cannot reliably override, so the printed
   quote would silently lose its cost breakdown. */
$("download").addEventListener("click", () => {
  const breakdown = document.querySelector(".breakdown");
  const wasOpen = breakdown.open;
  breakdown.open = true;
  window.print();
  breakdown.open = wasOpen;
});

/* Shop identity from /api/meta, and again on every quote response.
 *
 * Not from /healthz. That endpoint is the Cloud Run health check and is left
 * alone; deploy.sh also records that it is intercepted at the Google edge on
 * *.run.app and never reaches the container, which would leave this header
 * blank on the deployed service. /api/meta is under a prefix this app owns, so
 * it sidesteps that question instead of depending on the answer.
 *
 * Currency and rate card version come from rate_card.json either way — the
 * header is never a hardcoded string. */
function applyMeta(meta) {
  if (!meta) return;
  els.stamp.textContent =
    `${meta.currency} · rate card ${meta.rate_card_version}\n` +
    `${meta.model} · ${meta.revision}`;
  setText(
    "colophon-text",
    `Prices produced by a deterministic costing engine · rate card ${meta.rate_card_version}.`
  );
}

fetch("/api/meta")
  .then((r) => (r.ok ? r.json() : null))
  .then(applyMeta)
  .catch(() => {});
