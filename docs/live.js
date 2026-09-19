"use strict";

const LIVE_URL = location.protocol === "file:"
  ? "https://raw.githubusercontent.com/naibwedi/tsetlin-trader/main/docs/live.json"
  : "live.json";
const INTRADAY_URL = location.protocol === "file:"
  ? "https://raw.githubusercontent.com/naibwedi/tsetlin-trader/main/docs/intraday.json"
  : "intraday.json";
const byId = (id) => document.getElementById(id);

function timeLabel(value) {
  if (!value) return "Time unavailable";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString();
}

function orderLabel(order) {
  const status = String(order.status || "unknown").toLowerCase();
  const action = `${order.symbol || "Unknown symbol"} ${order.side || "order"}`;
  if (status.includes("filled")) return `${action}: filled`;
  if (status.includes("reject") || status.includes("cancel")) return `${action}: ${status}`;
  if (status.includes("pending") || status.includes("accepted") || status.includes("new")) {
    return `${action}: submitted; fill not confirmed`;
  }
  return `${action}: ${status}`;
}

function render(data) {
  const run = data.latest_run;
  const signal = data.latest_signal;
  const gate = data.gate || {};
  const points = data.points || [];
  const last = points.at(-1);
  const prior = points.at(-2);

  byId("live-run").textContent = run ? `${run.status}${run.plan_only ? " · preview" : ""}` : "No runs saved";
  byId("live-run-time").textContent = run ? timeLabel(run.logged_at) : "Waiting for the first run";
  byId("live-decision").textContent = signal ? signal.strategy : "No executed signal yet";
  byId("live-change").textContent = signal
    ? signal.previous_strategy ? `Previous: ${signal.previous_strategy}` : `Price date: ${signal.as_of || "unknown"}`
    : "Previews are not trades";
  byId("live-progress").textContent = `${gate.intervals || 0} / ${gate.minimum_intervals || 52} intervals`;
  byId("live-gate").textContent = gate.status === "collecting_evidence"
    ? `${gate.calendar_days || 0} / ${gate.minimum_calendar_days || 365} calendar days`
    : String(gate.status || "No assessment yet").replaceAll("_", " ");

  const signalPanel = byId("live-signal-panel");
  signalPanel.hidden = !signal;
  if (signal) {
    const weights = Object.entries(signal.target_weights || {})
      .map(([symbol, weight]) => `${symbol} ${(Number(weight) * 100).toFixed(0)}%`).join(", ") || "cash";
    byId("live-signal-date").textContent = `Signal from ${signal.as_of || "unknown date"}; recorded ${timeLabel(signal.logged_at)}. Model target: ${weights}.`;
    byId("live-risk").textContent = `Risk check: ${signal.risk_decision || "unknown"}. ${signal.risk_reason || ""}`;
    byId("live-orders").textContent = signal.orders?.length
      ? signal.orders.map(orderLabel).join(" · ")
      : "No orders were recorded for this decision.";
    const list = byId("live-rules");
    list.replaceChildren();
    for (const line of signal.rule_trace || []) {
      const item = document.createElement("li");
      item.textContent = line;
      list.append(item);
    }
  }

  const comparisonPanel = byId("live-comparison-panel");
  comparisonPanel.hidden = !last;
  if (last) {
    const body = byId("live-portfolios");
    body.replaceChildren();
    for (const [key, label] of [["tm", "Tsetlin Machine"], ["blend", "Plain blend"], ["blend_filtered", "Blend + stress rule"]]) {
      const row = document.createElement("tr");
      const name = document.createElement("td");
      const value = document.createElement("td");
      const change = document.createElement("td");
      name.textContent = label;
      value.className = change.className = "num";
      value.textContent = Number(last[key]).toFixed(4);
      change.textContent = prior ? `${((last[key] / prior[key] - 1) * 100).toFixed(2)}%` : "First record";
      row.append(name, value, change);
      body.append(row);
    }
    byId("live-criteria").textContent = `${gate.criteria || ""} Execution fills still require separate verification. Passing this test calls for review; it never enables real-money trading.`;
  }

  const ageDays = run?.logged_at ? (Date.now() - new Date(run.logged_at).getTime()) / 86400000 : NaN;
  const status = byId("live-status");
  status.classList.toggle("error", Number.isFinite(ageDays) && ageDays > 8);
  status.textContent = Number.isFinite(ageDays) && ageDays > 8
    ? `Latest saved run is over 8 days old. Last checked ${timeLabel(new Date().toISOString())}.`
    : `Showing saved data through ${last?.as_of || "the latest run"}. Last checked ${timeLabel(new Date().toISOString())}.`;
}

async function refreshLive() {
  const button = byId("live-refresh");
  button.disabled = true;
  try {
    const response = await fetch(`${LIVE_URL}?t=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    const status = byId("live-status");
    status.classList.add("error");
    status.textContent = `Could not load the saved trial feed (${error.message}). Refresh or check the repo's docs/live.json file.`;
  } finally {
    button.disabled = false;
  }
}

async function refreshIntraday() {
  const status = byId("intraday-status");
  try {
    const response = await fetch(`${INTRADAY_URL}?t=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const trades = data.trades || [];
    const last = trades.at(-1);
    if (!last) {
      byId("intraday-run").textContent = "Awaiting data";
      status.textContent = data.status === "insufficient_sessions"
        ? `${data.sessions} complete sessions; ${data.required} needed before the first comparison.`
        : "No intraday replay has been saved yet.";
      return;
    }
    byId("intraday-run").textContent = `${trades.length} sessions`;
    byId("intraday-time").textContent = `Last session ${last.day}; rebuilt ${timeLabel(data.generated_at)}`;
    byId("intraday-tm").textContent = Number(data.tm_final_value).toFixed(4);
    byId("intraday-baseline").textContent = Number(data.baseline_final_value).toFixed(4);
    byId("intraday-tm-detail").textContent = "Net virtual value from 1.0000";
    byId("intraday-baseline-detail").textContent = "Same bars and estimated costs";
    byId("intraday-detail").hidden = false;
    byId("intraday-action").textContent = `${last.day}: TM ${last.tm_action}; morning rule ${last.baseline_action}. Hypothetical entry $${last.entry}, exit $${last.exit}.`;
    byId("intraday-cost").textContent = `${data.cost_bps_per_side} basis points per side assumed. IEX bars and fixed fills do not verify executable performance.`;
    const rules = byId("intraday-rules");
    rules.replaceChildren();
    const explanation = last.explanation;
    const lines = typeof explanation === "string" ? [explanation] : [
      `Class votes: ${Object.entries(explanation?.votes || {}).map(([key, vote]) => `${key}: ${vote}`).join(", ")}`,
      ...(explanation?.for || []).map(item => `For: ${item.vote} · ${item.literals.join(" AND ") || "empty clause"}`),
      ...(explanation?.against || []).map(item => `Against: ${item.vote} · ${item.literals.join(" AND ") || "empty clause"}`),
    ];
    for (const line of lines) {
      const item = document.createElement("li");
      item.textContent = line;
      rules.append(item);
    }
    status.classList.remove("error");
    status.textContent = `Retrospective replay through ${last.day}; no intraday broker orders.`;
  } catch (error) {
    status.classList.add("error");
    status.textContent = `Could not load intraday replay (${error.message}).`;
  }
}

byId("live-refresh").addEventListener("click", () => { refreshLive(); refreshIntraday(); });
refreshLive();
refreshIntraday();
setInterval(() => { refreshLive(); refreshIntraday(); }, 60000);

