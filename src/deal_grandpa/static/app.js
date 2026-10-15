const result = document.querySelector('#result');
const query = document.querySelector('#query');
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function card(item) {
  const gates = item.gates.map(g => `<span class="gate ${esc(g.status)}" title="${esc(g.detail)}">${esc(g.gate)} ${esc(g.status)}</span>`).join('');
  const numbers = item.numbers.map(n => `<li><code>${esc(n.name)}=${esc(n.value)}</code><br><small>source_ts ${esc(n.source_ts)} · fetched_at ${esc(n.fetched_at)} · rules ${esc(n.rules_hash)} · run ${esc(n.run_id)}</small></li>`).join('');
  return `<article class="offer"><h3>${esc(item.merchant || 'Merchant withheld')}</h3><p>${esc(item.variant || 'Variant withheld')}</p><div class="cost">${esc(item.effective_cost_display || 'unknown total')}</div><div class="gates">${gates}</div><p>${esc(item.explanation)}</p><details><summary>Evidence Card · ${esc(item.offer_id)}</summary><p>Item ${esc(item.price_display || 'unknown')} + shipping ${esc(item.shipping_display || 'unknown')} + fees ${esc(item.fees_display || 'unknown')} = effective cost ${esc(item.effective_cost_display || 'unknown')}. Verified discount metadata: ${esc(item.verified_discount_minor ?? 'none')}. Cost status: ${esc(item.fees_status)}. Trust score: ${esc(item.trust_score ?? 'none')}.</p><ol>${numbers || '<li>No verified number because this offer is not price-complete.</li>'}</ol><ul>${item.gates.map(g => `<li class="reason"><code>${esc(g.gate)} ${esc(g.status)} ${esc(g.reason_code)}</code> — ${esc(g.detail)}</li>`).join('')}</ul></details></article>`;
}

function render(data) {
  result.innerHTML = `<div class="result-head"><div><span class="outcome ${esc(data.outcome)}">${esc(data.outcome)}</span><p>${esc(data.reasons.join(' '))}</p></div><p class="muted">run ${esc(data.run_id)} · rules ${esc(data.rules_hash)}</p></div><div class="two-col"><div><h2>Ranked exact-variant offers</h2><div class="cards">${data.ranked.length ? data.ranked.map(card).join('') : '<div class="empty">No verified recommendation.</div>'}</div></div><aside class="panel"><h2>Source health · FIXTURE</h2>${data.source_health.map(s => `<p><strong>${esc(s.source_id)}</strong><br>breaker ${esc(s.breaker_state)} · dead-letter ${esc(s.dead_letter_count)} · reliability ${esc(s.reliability)}/100</p>`).join('')}<h2>Excluded</h2><p>${data.excluded.length} offer(s) stayed out. Expand a card to see every G1–G7 reason.</p></aside></div><section><h2>Excluded panel</h2><div class="cards">${data.excluded.slice(0,12).map(card).join('') || '<div class="empty">Nothing excluded.</div>'}</div></section>`;
}

async function check() {
  result.innerHTML = '<div class="empty">Grandpa is checking the FIXTURE evidence…</div>';
  const response = await fetch('/api/runs', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({query:query.value})});
  const data = await response.json();
  if (!response.ok) { result.innerHTML = `<div class="empty">Needs review: ${esc(data.detail || 'request failed')}</div>`; return; }
  render(data);
}
async function fault(mode) {
  await fetch('/api/fault-injection', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({mode})});
  document.querySelector('#mode').textContent = `FIXTURE mode · fault ${mode} · network calls: none`;
}
document.querySelector('#check').addEventListener('click', check);
document.querySelector('#break').addEventListener('click', () => fault('timeout_once'));
document.querySelector('#clear').addEventListener('click', () => fault('clear'));
