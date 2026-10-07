const $ = (id) => document.getElementById(id);
const money = (n) => new Intl.NumberFormat("en-US", {style:"currency", currency:"USD", maximumFractionDigits:2}).format(n);
const pct = (n) => `${Number(n).toFixed(2)}%`;
const safe = (value) => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function renderChart(points, baseline) {
  const svg = $("equity-chart");
  const width=900, height=280, left=70, right=24, top=20, bottom=38;
  const values = points.map(p=>Number(p.equity)).concat([baseline]);
  let lo=Math.min(...values), hi=Math.max(...values); const pad=Math.max((hi-lo)*.25,25); lo-=pad; hi+=pad;
  const x=i=>left+(points.length===1?0:(i/(points.length-1))*(width-left-right));
  const y=v=>top+(hi-v)/(hi-lo)*(height-top-bottom);
  const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(' ');
  const area=`${path} L${x(points.length-1)},${height-bottom} L${x(0)},${height-bottom} Z`;
  const ticks=[0,.25,.5,.75,1].map(t=>lo+(hi-lo)*t);
  svg.innerHTML=`<defs><linearGradient id="area-gradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#6ee7b7" stop-opacity=".24"/><stop offset="1" stop-color="#6ee7b7" stop-opacity="0"/></linearGradient></defs>`+
    ticks.map(v=>`<line class="chart-grid" x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}"/><text class="chart-label" x="${left-10}" y="${y(v)+4}" text-anchor="end">${money(v)}</text>`).join('')+
    `<line class="baseline-line" x1="${left}" x2="${width-right}" y1="${y(baseline)}" y2="${y(baseline)}"/><path class="chart-area" d="${area}"/><path class="chart-line" d="${path}"/>`+
    points.map((p,i)=>`<circle class="chart-point" cx="${x(i)}" cy="${y(p.equity)}" r="5"><title>${safe(p.at)}: ${money(p.equity)}</title></circle>`).join('')+
    `<text class="chart-label" x="${left}" y="${height-10}">${safe(points[0].at)}</text><text class="chart-label" x="${width-right}" y="${height-10}" text-anchor="end">${safe(points.at(-1).at)}</text>`;
}

function render(data){
  const account=data.account, trial=data.trial, risk=data.risk, cycle=data.latest_cycle;
  const change=account.equity-trial.starting_equity, changePct=change/trial.starting_equity*100;
  $("week-number").textContent=trial.completed_weeks; $("trial-dial").style.setProperty("--progress",`${trial.completed_weeks/trial.protocol_weeks*360}deg`); $("trial-status").textContent=trial.status.replaceAll('_',' ');
  $("equity").textContent=money(account.equity); $("equity-change").textContent=`${change>=0?'+':''}${money(change)} · ${changePct>=0?'+':''}${pct(changePct)}`; $("equity-change").className=change>=0?'positive':'negative';
  $("cash").textContent=money(account.cash); $("cash-share").textContent=`${pct(account.cash/account.equity*100)} of equity`;
  const invested=Object.values(account.positions).reduce((a,b)=>a+Number(b),0); $("exposure").textContent=pct(invested/account.equity*100);
  $("risk-state").textContent=risk.halted?'HALTED':'Healthy'; $("risk-detail").textContent=`${pct(risk.current_drawdown_pct)} drawdown · ${pct(risk.max_drawdown_pct)} halt`;
  $("open-orders").textContent=account.open_orders.length?`${account.open_orders.length} open orders`:'No open orders';
  const entries=Object.entries(account.positions), cashPct=account.cash/account.equity*100;
  $("allocation-bar").innerHTML=entries.map(([s,v])=>`<span class="allocation-${s.toLowerCase()}" style="width:${v/account.equity*100}%">${safe(s)}</span>`).join('')+`<span class="allocation-cash" style="width:${cashPct}%">Cash</span>`;
  $("positions").innerHTML=entries.map(([s,v])=>`<div class="position-row"><span>${safe(s)}</span><strong>${money(v)} · ${pct(v/account.equity*100)}</strong></div>`).join('')+`<div class="position-row"><span>Cash</span><strong>${money(account.cash)} · ${pct(cashPct)}</strong></div>`;
  $("signal-name").textContent=`${cycle.signal} controller`; $("cycle-date").textContent=`Signal ${cycle.signal_as_of}`;
  $("decision-trace").innerHTML=cycle.sleeves.map((s,i)=>`<div class="decision-step"><b>${i+1}</b><span>${safe(s.name)}</span><strong>${safe(s.target)}</strong></div>`).join('');
  $("order-result").textContent=`${cycle.confirmed_fills} filled · ${cycle.rejected_orders} rejected`;
  $("last-update").textContent=`Verified ${new Date(data.generated_at).toLocaleString()}`;
  const checks=[['Paper endpoint',data.safety.paper_only,'No live-money endpoint'],['Account binding',data.safety.account_binding,'Private state matches'],['Risk state',data.safety.risk_integrity,'Durable and readable'],['Drawdown breaker',!risk.halted,'No halt active'],['Order reconciliation',data.safety.orders_reconciled,'Final broker statuses checked'],['Data freshness',data.safety.data_fresh,'Latest decision data accepted']];
  $("safety-grid").innerHTML=checks.map(([n,ok,d])=>`<div class="safety-check ${ok?'':'bad'}"><header><i></i><strong>${safe(n)}</strong></header><p>${ok?'OK · ': 'Action required · '}${safe(d)}</p></div>`).join('');
  $("cycles").textContent=trial.completed_weeks; $("fills").textContent=trial.total_confirmed_fills; $("incidents").textContent=trial.safety_incidents;
  if(data.alert){$("alert").hidden=false; $("alert").textContent=data.alert}
  renderChart(data.equity_history,trial.starting_equity);
}

fetch('data/status.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json()}).then(render).catch(err=>{const a=$("alert");a.hidden=false;a.textContent=`Dashboard data unavailable: ${err.message}`});
