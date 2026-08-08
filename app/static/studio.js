/* eazli studio — data, reasoning and UI.
 *
 * The browser draws and drags. It never decides. Every arrangement — after a
 * plan, a swap, or a drag — is judged by app/geometry.py: live over HTTP when
 * the service is running, or replayed from verdicts that same engine produced
 * at build time when this is the static Pages build.
 *
 * There is deliberately no geometry in this file. The 3D viewport lives in
 * viewer.js and is imported dynamically, so ~490 KB of three.js does not block
 * the plan and its reasoning from painting.
 */

import { UNKNOWN_COLOUR } from './palette.js';

const $ = (s) => document.querySelector(s);
const state = {
  plan: null, room: null, style: [], assumptions: [],
  tiers: [], tier: null,
  viewer: null, view: 'iso',
};

// The viewport is optional scenery: if three.js fails to load, the plan, the
// verdicts and the reasoning all still work.
const viewerReady = import('./viewer.js')
  .then((mod) => { mod.init($('#canvas-host')); state.viewer = mod; return mod; })
  .catch((err) => {
    $('#canvas-host').innerHTML =
      `<p class="viewport-fallback">3D view unavailable (${err.message}).
       Every plan, verdict and reason below still works.</p>`;
    return null;
  });

const CATEGORY_FOR_ROLE = {
  sofa: 'sofa', coffee_table: 'coffee_table', dining_table: 'dining_table',
  floor_lamp: 'floor_lamp', tv_console: 'tv_unit', bed: 'bed', wardrobe: 'wardrobe',
};


// ---------------------------------------------------------------- rendering the plan

function render(plan) {
  // A flat is several validated rooms; a room is one. Flatten for the panel so
  // everything downstream keeps working on a single list of placed items.
  if (plan.rooms) {
    plan = {
      ...plan,
      room: 'whole flat',
      room_cm: plan.rooms[0].room_cm,
      placed: plan.rooms.flatMap((r) =>
        r.placed.map((i) => ({ ...i, slot_id: `${r.room}: ${i.slot_id}` }))),
      unfilled: plan.rooms.flatMap((r) =>
        r.unfilled.map((u) => ({ ...u, slot_id: `${r.room}: ${u.slot_id}` }))),
      validation: {
        status: plan.rooms.every((r) => r.validation.status === 'pass') ? 'pass'
              : plan.rooms.some((r) => r.validation.status === 'fail') ? 'fail' : 'unverified',
        reasons: plan.rooms.flatMap((r) =>
          r.validation.reasons.map((text) =>
            `${r.room.replace(/_/g, ' ')} — ${humaniseWithin(text, r.placed)}`)),
        notes: plan.rooms.flatMap((r) =>
          (r.validation.notes || []).map((text) =>
            `${r.room.replace(/_/g, ' ')} — ${humaniseWithin(text, r.placed)}`)),
      },
      _flat: plan,
    };
  }
  state.plan = plan;
  state.room = plan.room_cm;
  // Text first, always. The room follows whenever three.js has landed.
  paintVerdict(plan.validation, plan.total_sar, plan.budget_sar);
  paintHow(plan);
  renderAgentLog(plan);
  paintPanel(plan);
  viewerReady.then((v) => { if (v) { v.draw(plan._flat ?? plan); v.setView(state.view); } });
}

function paintHow(plan) {
  const considered = plan.placed.reduce((n, i) => n + (i.decision?.considered ?? 0), 0);
  const positions = plan.placed.reduce((n, i) => n + (i.decision?.positions_tried ?? 0), 0);
  const ruledOut = plan.placed.reduce((n, i) => n + (i.decision?.rejected?.length ?? 0), 0);

  $('#how-body').innerHTML = `
    <ol class="pipeline">
      <li><b>Brief</b><span>${plan.room.replace(/_/g, ' ')} in ${plan.unit},
        ${state.tier ? `${esc(state.tier.label)} budget — ` : ''}${money(plan.budget_sar)} SAR,
        ${state.style.join(' + ') || 'no style preference'}</span></li>
      <li><b>Space</b><span>${plan.room_cm.width}&times;${plan.room_cm.depth}&nbsp;cm, read off the
        surveyed floor plan &mdash; not estimated</span></li>
      <li><b>Search</b><span>${considered} catalogue candidate(s) ranked, ${positions}
        position(s) tested, ${ruledOut} ruled out with a measurement</span></li>
      <li><b>Verify</b><span>every surviving arrangement re-checked by
        <code>app/geometry.py</code>; this one returned
        <b class="v-${plan.validation.status}">${plan.validation.status}</b></span></li>
    </ol>
    <p class="how-note">No language model chose any of this. The planner searches
    the space of arrangements the engine already accepts, so a plan that could not
    be verified was never a candidate. Open <b>Why this?</b> on any item for the
    per-slot reasoning.</p>
    ${plan._flat ? `<p class="how-note"><b>Whole flat:</b> ${esc(plan._flat.layout_note)}</p>` : ''}
    ${(state.assumptions || []).length ? `
      <details class="assumed">
        <summary>${state.assumptions.length} things assumed, not measured</summary>
        <ul>${state.assumptions.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>
      </details>` : ''}`;
}

function paintVerdict(validation, total, budget) {
  const v = $('#verdict');
  v.className = `verdict ${validation.status}`;
  v.textContent = validation.status;
  const m = $('#money');
  m.classList.toggle('over', total > budget);
  const rooms = state.plan?._flat?.rooms?.length;
  m.innerHTML = `<b>${money(total)}</b> / ${money(budget)} SAR`
    + (rooms ? ` <span class="across">shared across ${rooms} rooms</span>` : '');

  // A red badge whose explanation sits five product cards further down is not
  // an explanation. Put the first reason beside the verdict and scroll the
  // panel back to it, so a failed swap says why without the user hunting.
  const why = $('#verdict-why');
  const first = (validation.reasons || [])[0];
  why.textContent = first ? (state.plan?._flat ? first : humanise(first)) : '';
  why.hidden = validation.status === 'pass' || !first;
  if (validation.status !== 'pass') $('#panel').scrollTo({ top: 0, behavior: 'smooth' });
}

function accessBadges(access) {
  if (!access) return '';
  const joined = (access.reasons || []).join(' ').toLowerCase();
  const out = [];
  if (access.status === 'unverified') out.push('<span class="badge unv">unverified</span>');
  if (joined.includes('tight')) out.push('<span class="badge tight">tight fit</span>');
  if (joined.includes('on its side') || joined.includes('on end')) out.push('<span class="badge side">needs tipping</span>');
  return out.join(' ');
}

function money(n) { return Math.round(n).toLocaleString(); }

function decisionHtml(i) {
  const d = i.decision;
  if (!d) return '';
  const runnersUp = (d.ranked_above || []).length
    ? `<div class="d-row"><span>ranked below it</span><div>${d.ranked_above
        .map((r) => `${esc(r.title.slice(0, 46))} <em>${money(r.price_sar || 0)} SAR</em>`)
        .join('<br>')}</div></div>` : '';
  const knocked = (d.rejected || []).length
    ? `<div class="d-row"><span>ruled out</span><div>${d.rejected.map((r) =>
        `<b>${esc(r.title.slice(0, 40))}</b> — <i>${r.stage}</i><br>${esc(humanise(r.why).slice(0, 160))}`
      ).join('<hr>')}</div></div>` : '';
  // Six "clears at NxNcm" lines is noise. Lead with the legs that need a human
  // to do something, and keep the rest one click away.
  const legs = i.access?.reasons || [];
  const notable = legs.filter((r) => /side|on end|TIGHT|cannot|does not fit|swing/i.test(r));
  const route = legs.length ? `<div class="d-row"><span>delivery route</span><div>${
      notable.length
        ? notable.map((r) => esc(humanise(r))).join('<br>')
        : `all ${legs.length} legs clear with room to spare`
    }<br><button class="link legs">show all ${legs.length} legs</button>
      <div class="all-legs" hidden>${legs.map((r) => esc(humanise(r))).join('<br>')}</div>
    </div></div>` : '';

  return `<div class="detail" hidden>
    <div class="d-row"><span>why this one</span><div>${esc(d.chose_because)}</div></div>
    <div class="d-row"><span>why here</span><div>${esc(d.placed_because)}</div></div>
    <div class="d-row"><span>searched</span><div>${d.considered} candidate(s) in this
      category, ${d.positions_tried} position(s) tested for the winner</div></div>
    ${runnersUp}${knocked}${route}
  </div>`;
}

/** The panel's dot has to say the same thing the render does.
 *
 * It used to fall back to the role's colour, so an item with no published
 * colour got a confident blue dot beside a confident blue sofa and there was
 * nowhere on the page you could tell that nobody had stated one. Unknown gets
 * the hatched slate instead — the same treatment the piece carries in the
 * viewport, so the two are learnable as one thing. `background-color`, not the
 * `background` shorthand, because the shorthand would wipe the CSS hatch.
 *
 * `size` is for the brief, which shows the same dot inside the modal: the
 * stylesheet sizes `.item .swatch`, and out there it would collapse to
 * nothing. One function either way, so the two dots cannot drift apart. */
const INLINE_DOT = 'display:inline-block;width:11px;height:11px;border-radius:3px';
function swatch(i, size = '') {
  return i.colour_hex
    ? `<span class="swatch" style="background-color:${i.colour_hex};${size}"
             title="colour published by the listing"></span>`
    : `<span class="swatch unknown" style="background-color:${UNKNOWN_COLOUR};${size}"
             title="the listing publishes no colour"></span>`;
}

function paintPanel(plan) {
  $('#items').innerHTML = plan.placed.map((i) => `
    <div class="item" data-slot="${i.slot_id}" data-role="${i.role}"
         tabindex="0" role="button" aria-label="Open the brief for ${esc(i.title)}">
      <div class="item-head">
        ${swatch(i)}
        <div class="item-main">
          <div class="slot">${i.slot_id.replace(/_/g, ' ')}</div>
          <div class="name">${esc(i.title)}</div>
          <div class="meta">${i.dims_cm.w}\u00d7${i.dims_cm.d}\u00d7${i.dims_cm.h} cm \u00b7 ${
            i.material ? esc(i.material) : '<i class="unstated">material not published</i>'}${
            i.colour_hex ? '' : ' \u00b7 <i class="unstated">colour not published</i>'}
            \u00b7 <span title="How the dimensions were obtained from the listing">${i.dims_confidence}</span></div>
          ${accessBadges(i.access)}
        </div>
        <div class="price">${money(i.price_sar)}</div>
      </div>
      <div class="actions">
        <button class="link why" data-slot="${i.slot_id}">Why this?</button>
        <button class="link swap" data-slot="${i.slot_id}" data-role="${i.role}">Swap\u2026</button>
      </div>
      ${decisionHtml(i)}
    </div>`).join('')
    + plan.unfilled.map((s) => `
    <div class="item ghost">
      <div class="item-head">
        <span class="swatch" style="background:#3a434f"></span>
        <div class="item-main">
          <div class="slot">${s.slot_id.replace(/_/g, ' ')}</div>
          <div class="name">nothing fits this slot</div>
          <div class="why">${esc(s.reason)}</div>
        </div>
        <div class="price">\u2014</div>
      </div>
    </div>`).join('');

  const reasons = plan.validation.reasons || [];
  const notes = [...new Set(plan.validation.notes || [])];
  const advisories = notes
    .map((n) => `<div class="issue note">${esc(plan._flat ? n : humanise(n))}</div>`).join('');
  renderFinishing(plan);
  $('#issues').innerHTML = reasons.length
    ? reasons.map((r) => `<div class="issue">${esc(plan._flat ? r : humanise(r))}</div>`).join('')
      + advisories
    : advisories + `<div class="issue note">Every clearance, door swing, walkway,
        reach and delivery route was checked by <code>app/geometry.py</code>.
        Nothing here was decided by a language model.</div>`;

  $('#items').querySelectorAll('.why').forEach((b) => {
    b.onclick = () => {
      const box = b.closest('.item').querySelector('.detail');
      box.hidden = !box.hidden;
      b.textContent = box.hidden ? 'Why this?' : 'Hide reasoning';
    };
  });
  $('#items').querySelectorAll('.legs').forEach((b) => {
    b.onclick = () => {
      const box = b.nextElementSibling;
      box.hidden = !box.hidden;
      b.textContent = box.hidden ? `show all legs` : 'hide legs';
    };
  });
  $('#items').querySelectorAll('.swap').forEach((b) => {
    b.onclick = () => openPicker(b.dataset.slot, b.dataset.role);
  });
  // The row is already three things: a hover-highlight for the 3D view and a
  // home for the "Why this?" and "Swap…" buttons. Opening the brief is a
  // fourth, so it stands aside for anything inside the row that is already
  // clickable rather than swallowing it.
  $('#items').querySelectorAll('.item[data-slot]').forEach((el) => {
    el.onmouseenter = () => highlight(el.dataset.slot, true);
    el.onmouseleave = () => highlight(el.dataset.slot, false);
    el.onclick = (e) => {
      if (e.target.closest('.actions, .detail, a, button')) return;
      openBrief(el.dataset.slot);
    };
    el.onkeydown = (e) => {
      if (e.key === 'Enter' && e.target === el) openBrief(el.dataset.slot);
    };
  });
}

function highlight(slot, on) {
  state.viewer?.highlight(slot, on);
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** Reasons come back naming items by the id they were sent with — an ASIN.
 *  "Only 35cm between B0FR3WVLTS and B0H8PQ9KDJ" is precise and unreadable.
 *  Swap the codes for the slot names a person can actually see on screen. */
function humaniseWithin(text, items) {
  let out = String(text);
  for (const item of items ?? []) {
    if (item.asin) out = out.replaceAll(item.asin, `the ${item.slot_id.replace(/_/g, ' ')}`);
    // Reasons also name items by slot_id, which is only unique inside a room.
    out = out.replaceAll(item.slot_id, item.slot_id.replace(/_/g, ' '));
  }
  return out;
}

function humanise(text) {
  return humaniseWithin(text, state.plan?.placed ?? []);
}

/** Put the measurements in a sentence the engine wrote into `<code>`.
 *
 *  A number buried in prose reads as a claim; a number in a monospace box
 *  reads as something you can go and check with a tape measure, which is what
 *  every one of these is. Escapes FIRST — this inserts markup, so it must
 *  never be handed text that has already been marked up. */
const measure = (text) => esc(text).replace(
  /\d[\d,]*(?:\.\d+)?(?:\s?[x×]\s?\d[\d,]*(?:\.\d+)?)*\s?(?:cm|SAR)\b/g,
  (m) => `<code>${m}</code>`);

// ---------------------------------------------------------------- the agent log
/* Who decided what, in the words of the agent that owns the decision.
 *
 * eazli ships three agents — Zeina, Noura, Adam — and this project adds an
 * adversarial fourth with the authority to reject. The plan JSON already
 * records everything the four of them concluded. Nothing here re-derives any
 * of it: this section is attribution and phrasing, and nothing else.
 *
 * The rule the whole section exists to keep is that no sentence may assert
 * something the plan does not contain. There is no template that fires "a
 * considered choice for this space" over an empty field — where an agent has
 * nothing to report it says one honest line instead. Every builder below is a
 * pure function of the plan, which is what makes that checkable: test_studio.py
 * runs them in node over real exported plans and re-finds every measurement
 * they print in the JSON it was printed from.
 */

const AGENTS = {
  zeina: { name: 'Zeina', role: 'AI Guide', avatar: 'zeina_card_avatar.png', mono: 'Z' },
  noura: { name: 'Noura', role: 'AI Design Agent', avatar: 'noura_card_avatar.png', mono: 'N' },
  adam: { name: 'Adam', role: 'AI Sales Advisor', avatar: 'adam_card_avatar.png', mono: 'A' },
  // Not an eazli persona, so eazli publishes no card for it. The monogram is
  // its face rather than a hotlink that would 404 on every single load.
  auditor: { name: 'fit-auditor', role: 'adversarial review, authority to reject',
             avatar: null, mono: '!' },
};

/* A letter on a disc, as a data: URI. Quote characters are percent-encoded
 * because this string is interpolated into an inline `onerror` attribute, and
 * spaces because a bare space in a URI is not portable. */
const monogram = (ch) => 'data:image/svg+xml,'
  + '%3Csvg%20xmlns=%27http://www.w3.org/2000/svg%27%20viewBox=%270%200%2064%2064%27%3E'
  + '%3Crect%20width=%2764%27%20height=%2764%27%20rx=%2732%27%20fill=%27%23334155%27/%3E'
  + '%3Ctext%20x=%2732%27%20y=%2742%27%20text-anchor=%27middle%27%20font-size=%2728%27'
  + '%20font-family=%27sans-serif%27%20fill=%27%23e2e8f0%27%3E' + ch + '%3C/text%3E%3C/svg%3E';

function agentHead(key) {
  const a = AGENTS[key];
  const fallback = monogram(a.mono);
  // Hotlinked from a CDN this build does not control, below the fold, and not
  // worth a millisecond of first paint. If it 404s the monogram takes over —
  // `onerror = null` first, so a failing fallback cannot loop.
  return `<div class="agent-head">
      <img src="${a.avatar ? `https://cdn.eazli.com/agents/${a.avatar}` : fallback}"
           alt="" width="36" height="36" loading="lazy"
           onerror="this.onerror=null;this.src='${fallback}'">
      <span class="agent-name">${esc(a.name)}</span>
      <span class="agent-role">${esc(a.role)}</span>
    </div>`;
}

function agentTurn(key, says, gotchas = []) {
  return `<div class="agent-turn ${key}">${agentHead(key)}
    <div class="agent-says">${says}</div>
    ${gotchas.map((g) => `<div class="agent-gotcha">${g}</div>`).join('')}
  </div>`;
}

const slotWords = (id) => esc(String(id).replace(/_/g, ' '));

/** The rejection tally, which a whole-flat plan keeps per room.
 *
 *  Summed across rooms it double-counts a listing that was in scope for two of
 *  them, so the flat case says so rather than presenting a tidier number. */
function unspentOf(plan) {
  if (plan.unspent_budget) return { ...plan.unspent_budget, rooms: 0 };
  const rooms = (plan._flat?.rooms ?? []).map((r) => r.unspent_budget).filter(Boolean);
  if (!rooms.length) return null;
  const sum = (k) => rooms.reduce((t, u) => t + (u[k] || 0), 0);
  const rejected = {};
  for (const u of rooms) {
    for (const [cause, n] of Object.entries(u.candidates_rejected || {})) {
      rejected[cause] = (rejected[cause] || 0) + n;
    }
  }
  return {
    budget_sar: plan.budget_sar, spent_sar: sum('spent_sar'),
    unspent_sar: plan.budget_sar - sum('spent_sar'),
    candidates_in_scope: sum('candidates_in_scope'),
    candidates_rejected: rejected, rooms: rooms.length,
  };
}

/* Zeina frames the brief and routes it. She never picks a product: eazli's own
 * page says she "doesn't give you a catalog or a quote; she gives you a map".
 * So this turn is only what was asked for and where it was sent. */
function zeinaTurn(plan, ctx) {
  const flat = plan._flat;
  const asked = flat
    ? `${esc(flat.label)} — every room in it`
    : `the ${slotWords(plan.room)} in ${esc(plan.unit)}`;
  const budget = ctx.tier
    ? `the ${esc(ctx.tier.label)} tier at ${money(plan.budget_sar)} SAR`
      + (ctx.tier.note ? ` (${esc(ctx.tier.note)})` : '')
    : `${money(plan.budget_sar)} SAR`;
  const style = (ctx.style || []).length
    ? `styled ${esc(ctx.style.join(' + '))}`
    : 'with no style preference stated';
  const slots = plan.placed.length + plan.unfilled.length;
  return agentTurn('zeina', `You asked for ${asked}, ${style}, on ${budget}.
    I don't hand you a catalogue or a quote; I hand you a map. So I routed it:
    the ${slots} slot${slots === 1 ? '' : 's'} ${flat ? 'those rooms’ recipes define'
      : 'this room’s recipe defines'} went to
    Noura to lay out, the budget went to Adam to source against, and whatever the
    two of them agreed on went to fit-auditor, who is allowed to throw it out.`);
}

/* Noura owns the layout: the slots, the positions, and the engine's verdict on
 * the arrangement as a whole — including the advisories in validation.notes,
 * which are hers because they are about the room rather than a product. */
function nouraTurn(plan) {
  const flat = plan._flat;
  const r = plan.room_cm;
  const doors = (r.doors || []).length;
  const shape = flat
    ? `${flat.rooms.length} rooms, each measured off the surveyed floor plan and laid
       out on its own.`
    : `The room is <code>${r.width}&times;${r.depth}&nbsp;cm</code> with
       ${doors} door${doors === 1 ? '' : 's'} on the plan.`;
  const filled = plan.placed.length + plan.unfilled.length;
  const tried = plan.placed.reduce((n, i) => n + (i.decision?.positions_tried ?? 0), 0);
  const first = plan.placed[0];
  const opener = first
    // The position is printed as the plan states it, not rounded: a rounded
    // number is a number the reader cannot find in the data behind the page.
    ? `First placement: ${slotWords(first.slot_id)} at
       <code>(${first.x}, ${first.y})&nbsp;cm</code> facing
       ${esc(first.facing)} — ${esc(first.decision?.placed_because ?? '')}.
       ${tried} position${tried === 1 ? '' : 's'} were tested in all.`
    : 'Nothing could be placed here, so there is no layout to describe.';
  const verdict = plan.validation.status === 'pass'
    ? `The engine re-checked the finished arrangement and returned
       <b class="v-pass">pass</b> with nothing listed against it.`
    : `The engine returned <b class="v-${plan.validation.status}">${plan.validation.status}</b>
       on the finished arrangement; what it said is fit-auditor's to read out.`;
  const notes = [...new Set(plan.validation.notes || [])];
  const advisory = notes.length
    ? ` It did leave ${notes.length} advisor${notes.length === 1 ? 'y' : 'ies'}:
        ${notes.map((n) => measure(plan._flat ? n : humaniseWithin(n, plan.placed))).join(' ')}`
    : '';
  return agentTurn('noura', `${shape} I filled
    ${plan.placed.length} of the ${filled} slot${filled === 1 ? '' : 's'} the recipe defines.
    ${opener} ${verdict}${advisory}`);
}

/* Adam owns sourcing, and sourcing is per slot: what the pool was, what beat
 * what, and what the running total had reached by the time he got there. */
function adamTurn(plan) {
  if (!plan.placed.length) {
    return agentTurn('adam', `I sourced nothing: no slot in this plan got as far as
      a product I could price.`);
  }
  let running = 0;
  const lines = plan.placed.map((i) => {
    const d = i.decision || {};
    running += i.price_sar || 0;
    const over = (d.ranked_above || [])[0];
    const others = (d.ranked_above || []).length - 1;
    return `<b>${slotWords(i.slot_id)}</b> — ${d.considered} in the pool, took
      ${esc(String(i.title).slice(0, 44))} at ${money(i.price_sar)} SAR because
      ${esc(d.chose_because ?? 'no reason was recorded')}${over
        ? `, over ${esc(String(over.title).slice(0, 34))} at ${money(over.price_sar || 0)} SAR`
        + (others > 0 ? ` and ${others} other${others === 1 ? '' : 's'}` : '')
        : ''}. Running total ${money(running)} SAR.`;
  });
  const u = unspentOf(plan);
  const tail = `That is ${money(plan.total_sar)} SAR against
    ${money(plan.budget_sar)} SAR${u && u.unspent_sar > 0
      ? `, leaving ${money(u.unspent_sar)} SAR I could not spend on anything that
         verified` : ''}.`;
  return agentTurn('adam', `${lines.join('<br>')}<br>${tail}`);
}

// Every cause the planner counts is already a phrase. Underscores out, and
// nothing else: renaming them here would put a second vocabulary on screen.
const causeWords = (cause) => esc(cause.replace(/_/g, ' '));

/* The auditor is the interesting one, and the only one allowed to be negative.
 * Every line it prints is a rejection, an empty slot or a failed rule that the
 * plan already recorded — including the stage each rejection died at. When the
 * plan holds none of that, it says so rather than manufacturing a worry. */
function auditorTurn(plan) {
  const gotchas = [];
  const seen = new Set();
  for (const i of plan.placed) {
    for (const r of i.decision?.rejected || []) {
      const key = `${r.asin}|${r.why}`;
      if (seen.has(key)) continue;   // the same listing loses in several rooms
      seen.add(key);
      gotchas.push(`<b>${slotWords(i.slot_id)}</b> · ${esc(String(r.title).slice(0, 44))}
        rejected at the <i>${esc(r.stage)}</i> stage —
        ${measure(humaniseWithin(r.why, plan.placed))}`);
    }
  }
  for (const s of plan.unfilled) {
    gotchas.push(`<b>${slotWords(s.slot_id)}</b> · left empty — ${measure(s.reason)}`
      + ((s.rejected || []).length
        ? ` ${s.rejected.length} candidate(s) were ruled out before it gave up.` : ''));
  }
  for (const reason of plan.validation.reasons || []) {
    gotchas.push(`<b>engine verdict</b> · ${measure(
      plan._flat ? reason : humaniseWithin(reason, plan.placed))}`);
  }

  const u = unspentOf(plan);
  const tally = Object.entries(u?.candidates_rejected || {}).filter(([, n]) => n > 0);
  const scope = tally.length
    ? ` Behind the plan: of ${u.candidates_in_scope} listing(s) in scope for these
        categories${u.rooms ? `, counted once per room across ${u.rooms} rooms` : ''},
        ${tally.map(([cause, n]) => `${n} ${causeWords(cause)}`).join(', ')}.`
    : '';
  const opening = gotchas.length
    ? `${gotchas.length} thing${gotchas.length === 1 ? ' in this plan is' : 's in this plan are'}
       worth seeing before you buy. Each is the engine's own measurement, quoted.`
    : `Nothing to object to: no candidate was rejected on a measurement, no slot was
       left empty, and the engine returned ${esc(plan.validation.status)} with no
       reason against it.`;
  return agentTurn('auditor', opening + scope, gotchas);
}

function agentLogHtml(plan, ctx) {
  return `<p class="muted small">Four agents produced this room. Each speaks only for
    the decisions the plan records as its own.</p>`
    + zeinaTurn(plan, ctx) + nouraTurn(plan) + adamTurn(plan) + auditorTurn(plan);
}

function renderAgentLog(plan) {
  let host = $('#agent-log');
  if (!host) {
    // The markup may or may not carry a host; either way there is exactly one.
    host = document.createElement('section');
    host.id = 'agent-log';
    ($('#issues') ?? $('#items')).before(host);
  }
  host.classList.add('agent-log');
  host.innerHTML = agentLogHtml(plan, { style: state.style, tier: state.tier });
}

// ---------------------------------------------------------------- swapping

async function openPicker(slotId, role) {
  const category = CATEGORY_FOR_ROLE[role];
  if (!category) return;
  $('#picker-title').textContent = `Swap ${slotId.replace(/_/g, ' ')}`;
  $('#picker-list').innerHTML = '<p class="picker-msg">Loading alternatives…</p>';
  $('#picker').hidden = false;

  let items, current;
  try {
    ({ items } = await api(`/plan/candidates/${category}`));
    current = state.plan.placed.find((p) => p.slot_id === slotId);
  } catch (err) {
    $('#picker-list').innerHTML =
      `<p class="picker-msg error">Could not load alternatives — ${esc(err.message)}</p>`;
    return;
  }
  if (!items.length) {
    $('#picker-list').innerHTML =
      `<p class="picker-msg">The catalogue has no ${esc(category.replace(/_/g, ' '))} at all.</p>`;
    return;
  }

  $('#picker-list').innerHTML = items.map((it) => `
    <div class="cand ${it.usable ? '' : 'blocked'}" data-asin="${it.asin}">
      <div>
        <div class="t">${esc(it.title)}${it.asin === current?.asin ? ' — <em>current</em>' : ''}</div>
        <div class="d">${it.dims_cm.w ?? '?'}×${it.dims_cm.d ?? '?'}×${it.dims_cm.h ?? '?'} cm ·
          ${it.dims_confidence}${it.rating ? ` · ${it.rating}★ (${it.reviews})` : ''}</div>
        ${it.flags.length ? `<div class="flag">${it.flags.join(', ').replace(/_/g, ' ')}</div>` : ''}
      </div>
      <div class="p">${it.price_sar ? Math.round(it.price_sar).toLocaleString() : '—'}</div>
    </div>`).join('');

  document.querySelectorAll('.cand:not(.blocked)').forEach((el) => {
    el.onclick = () => {
      const pick = items.find((i) => i.asin === el.dataset.asin);
      $('#picker').hidden = true;
      applySwap(slotId, pick);
    };
  });
}

// ---------------------------------------------------------------- the product brief
/* Everything the plan knows about one item, including the parts it does not
 * know. It opens in the swap modal rather than a second overlay of its own:
 * one shell, one close button, one Escape key, and the swap flow untouched.
 *
 * The awkward rows are the point. 31 usable listings publish no colour and the
 * render draws them hatched; if the brief filled that gap in with a plausible
 * word, the picture and the page beside it would disagree and only one of them
 * would be honest.
 */

const DIMS_MEANING = {
  stated: 'the listing labelled its axes, so this is what the seller published',
  parsed: 'read off an unlabelled field — the numbers are usually right, but which '
        + 'one is the depth is a guess',
  conflicted: 'two fields on the listing contradict each other, so the size is not believed',
  missing: 'the listing publishes no dimensions at all',
};

const SOURCE_MEANING = {
  attrs: "from the listing's own structured attribute field",
  title: 'read out of the listing title, not a structured field',
  conflicted: 'two fields on the listing state different values, so neither is used',
  missing: 'not published',
};

/* Raw parser flags in front of a shopper are schema, not information.
 * test_studio.py derives the keys from the capture, so a new flag fails a test
 * rather than reaching a customer as `assumed_depth`. */
const FLAG_MEANING = {
  no_published_dimensions: 'The listing publishes no dimensions, so nothing about how it '
    + 'fits can be claimed.',
  implausible_price: 'The price is far outside the range for this category and no volume '
    + 'of reviews vouches for it.',
  implausible_for_category: 'The published dimensions are outside a plausible range for '
    + 'this kind of furniture — most often a mislabelled axis.',
  colour_conflict: 'Two colour fields on the listing disagree, so no colour is claimed.',
  assumed_depth: 'Only two dimensions were published. The depth is an assumption, marked '
    + 'here rather than buried.',
  category_mismatch: 'The search returned it under this category, but the title parses as '
    + 'something else.',
  dimension_conflict: 'Two dimension fields on the listing contradict each other.',
  unclassified: 'No category pattern matched the title, so it was kept under the category '
    + 'the search returned it from.',
};

function briefHtml(i, plan) {
  const rows = [];
  const row = (label, value) =>
    rows.push(`<div class="d-row"><span>${label}</span><div>${value}</div></div>`);

  row('listing', `<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a>
    <br><em>${esc(i.asin)}</em> on amazon.sa · <b>${money(i.price_sar)} SAR</b>`);

  row('size', `<code>${i.dims_cm.w}&times;${i.dims_cm.d}&times;${i.dims_cm.h}&nbsp;cm</code>
    · <b>${esc(i.dims_confidence)}</b><br>
    ${esc(DIMS_MEANING[i.dims_confidence] ?? 'how this was obtained is not recorded')}`);

  // The swatch is the same one the panel and the viewport use, so the three
  // cannot drift apart on what "unknown" looks like.
  row('colour', (i.colour_hex
      ? `${swatch(i, INLINE_DOT)} <code>${esc(i.colour_hex)}</code>`
      : `${swatch(i, INLINE_DOT)} <b>not published</b>`)
    + `<br>${esc(SOURCE_MEANING[i.colour_source] ?? (i.colour_hex
        ? 'published by the listing' : 'the listing states none'))}`
    + (i.colour_hex ? '' : '. The render draws it in flat slate with a hatch for '
      + 'exactly this reason — nothing here invents one.'));

  row('material', (i.material ? `<b>${esc(i.material)}</b>` : '<b>not published</b>')
    + `<br>${esc(SOURCE_MEANING[i.material_source] ?? (i.material
        ? 'published by the listing' : 'the listing states none'))}`);

  if (i.rating !== undefined || i.reviews !== undefined) {
    row('reviews', i.rating
      ? `${i.rating}★ from ${i.reviews} review${i.reviews === 1 ? '' : 's'}`
      : 'no reviews');
  }

  if (Array.isArray(i.flags)) {
    row('flagged', i.flags.length
      ? i.flags.map((f) => `<i>${esc(f.replace(/_/g, ' '))}</i> — ${esc(FLAG_MEANING[f]
          ?? 'a parser flag with no plain-language note yet')}`).join('<br>')
      : 'nothing. This listing tripped none of the parser’s checks.');
  }

  const a = i.access || {};
  const legs = a.reasons || [];
  // On a failure the only line that matters is the segment that failed; on a
  // pass, the legs that need a human to do something.
  const failed = legs.filter((r) => /cannot|does not fit/i.test(r));
  const notable = legs.filter((r) => /TIGHT|on its side|on end|swing/i.test(r));
  const shown = failed.length ? failed : notable;
  row('delivery', `<b>${esc(a.status ?? 'unverified')}</b>${a.measured_using
      ? ` · measured ${esc(a.measured_using)}` : ''}<br>`
    + (shown.length
      ? shown.map((r) => measure(humaniseWithin(r, plan?.placed))).join('<br>')
      : legs.length ? `all ${legs.length} legs of the route clear.`
      : 'no route was checked, so nothing is claimed.'));

  const d = i.decision || {};
  row('why this one', `${esc(d.chose_because ?? 'no reason recorded')} ·
    ${d.considered ?? 0} candidate(s) considered`
    + ((d.ranked_above || []).length
      ? `<br>ranked above:<br>${d.ranked_above.map((r) =>
          `${esc(String(r.title).slice(0, 50))} <em>${money(r.price_sar || 0)} SAR</em>`)
        .join('<br>')}` : ''));

  return `<div class="detail" style="margin:12px 14px">${rows.join('')}</div>`;
}

function openBrief(slotId) {
  const item = state.plan?.placed.find((p) => p.slot_id === slotId);
  if (!item) return;
  $('#picker-title').textContent = slotId.replace(/_/g, ' ');
  $('#picker-list').innerHTML = briefHtml(item, state.plan);
  $('#picker').hidden = false;
}

async function applySwap(slotId, pick) {
  // Keep the position, change the object, then ask the engine what it thinks.
  // The browser deliberately makes no attempt to guess whether this still fits.
  const placements = state.plan.placed.map((p) => p.slot_id === slotId
    ? { ...p, asin: pick.asin, title: pick.title, url: pick.url,
        price_sar: pick.price_sar ?? 0, dims_cm: pick.dims_cm,
        dims_confidence: pick.dims_confidence, flat_pack: pick.flat_pack }
    : p);

  const res = await api('/plan/swap', {
    unit: $('#unit').value, room: $('#room').value, placements,
  });

  placements.forEach((p) => { p.access = res.access[p.asin] ?? p.access; });
  render({ ...state.plan, placed: placements, total_sar: res.total_sar, validation: res.validation });
}

// ---------------------------------------------------------------- data + wiring

/* Two modes, one implementation.
 *
 * Live: POST to FastAPI, which runs app/geometry.py per request.
 * Static: a bundle of verdicts PRE-computed by that same Python, for hosting
 *         somewhere that cannot run a backend (GitHub Pages).
 *
 * The static mode is a replay of real engine output, not a JavaScript
 * reimplementation of the rules — there is deliberately no geometry in this
 * file. A combination the bundle does not contain is reported as unavailable
 * rather than guessed at, because guessing is the one thing the project exists
 * not to do.
 */
/* Two modes, one call site.
 *
 * Live: POST to FastAPI, which runs app/geometry.py per request.
 * Static: fetch verdicts PRE-computed by that same Python, for hosting
 *         somewhere that cannot run a backend (GitHub Pages).
 *
 * The static mode is a replay of real engine output, not a JavaScript
 * reimplementation of the rules — there is deliberately no geometry in this
 * file. A combination the build does not contain is reported as unavailable
 * rather than guessed at, because guessing is the one thing the project exists
 * not to do.
 *
 * Everything is fetched on demand. The build used to ship one 2.5 MB blob
 * containing all 50 plans and all 1,075 swap verdicts, parsed before the first
 * frame — 90% of it for interactions a visitor may never make.
 */
const STATIC = document.documentElement.dataset.mode === 'static';
const planKey = (u, r, s, t) => `${u}__${r}__${(s ?? []).length ? s.join('-') : 'any'}__${t}`;

const cache = new Map();
async function json(url) {
  if (!cache.has(url)) {
    cache.set(url, fetch(url).then((r) => {
      if (!r.ok) throw new Error(`${url} \u2192 ${r.status}`);
      return r.json();
    }));
  }
  return cache.get(url);
}

async function api(path, body) {
  if (STATIC) return staticApi(path, body);
  const res = await fetch(path, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : undefined);
  if (!res.ok) throw new Error(`${path} \u2192 ${res.status}`);
  return res.json();
}

const NOT_BUILT = {
  validation: {
    status: 'unverified',
    reasons: ['This combination was not precomputed for the static build. Run the '
            + 'studio locally to have the engine judge it live: '
            + 'uv run uvicorn app.main:app --port 8000'],
  },
  access: {},
};

async function staticApi(path, body) {
  if (path === '/home/units') return json('./data/index.json');

  if (path.startsWith('/plan/candidates/')) {
    const cat = path.split('/').pop();
    try {
      return await json(`./data/candidates/${cat}.json`);
    } catch {
      return { category: cat, count: 0, items: [] };
    }
  }

  if (path === '/plan/flat') {
    try {
      return structuredClone(await json(
        `./data/flats/${planKey(body.unit, 'flat', state.style, state.tier?.id)}.json`));
    } catch {
      throw new Error('This build has no precomputed whole-flat plan for that unit.');
    }
  }

  if (path === '/plan/auto') {
    try {
      return structuredClone(await json(
        `./data/plans/${planKey(body.unit, body.room, body.style, state.tier?.id)}.json`));
    } catch {
      throw new Error(`This build has no precomputed plan for ${body.room.replace(/_/g, ' ')}.`);
    }
  }

  if (path === '/plan/swap') {
    const notBuilt = () => ({
      ...structuredClone(NOT_BUILT),
      total_sar: body.placements.reduce((t, p) => t + (p.price_sar || 0), 0),
    });

    /* The plan names its own swap table; the context key cannot.
     *
     * Swap files are content-addressed — named by a hash of what is in them —
     * because 200 context-named files held 28 distinct payloads and 86% of
     * 27.6 MB was byte-identical. So the filename is not derivable here; it is
     * read off the plan, which was fetched before any of this was clickable.
     * A whole-flat plan carries no ref, and no table was precomputed for one.
     */
    const ref = state.plan?.swaps_ref;
    if (!ref) return notBuilt();

    let table;
    try {
      table = await json(`./data/swaps/${ref}.json`);
    } catch {
      return notBuilt();
    }
    const hit = body.placements
      .map((p) => table[`${p.slot_id}|${p.asin}`])
      .find(Boolean);
    return hit ? structuredClone(hit) : notBuilt();
  }

  throw new Error(`no static data for ${path}`);
}

async function runPlan() {
  $('#loading').hidden = false;
  $('#run').disabled = true;
  try {
    const style = $('#style').value ? $('#style').value.split(',') : [];
    state.style = style;
    // The tier carries the amount. The control used to be a number box, which
    // the static build could not honour — there is no engine here to re-plan
    // against a figure nobody exported a plan for.
    state.tier = state.tiers.find((t) => t.id === $('#budget').value) ?? state.tiers[0];
    const room = $('#room').value;
    const body = { unit: $('#unit').value, room,
                   budget_sar: state.tier?.sar, style };
    const plan = room === '__flat__'
      ? await api('/plan/flat', { ...body, room: 'whole flat' })
      : await api('/plan/auto', body);
    render(plan);
  } catch (err) {
    // Leaving the previous verdict on screen would claim the plan shown is
    // still verified. It is not: it is whatever was there before the failure.
    const v = $('#verdict');
    v.className = 'verdict pending';
    v.textContent = 'not run';
    $('#verdict-why').hidden = true;
    $('#issues').innerHTML = `<div class="issue">${esc(err.message)} — is the
      service running? <code>uv run uvicorn app.main:app --port 8000</code><br>
      The layout below is from the previous run and has not been re-checked.</div>`;
  } finally {
    $('#loading').hidden = true;
    $('#run').disabled = false;
  }
}

/* Two more rows for the legend, appended here rather than written into
 * index.html, because they describe what the RENDER does with an attribute the
 * listing left out — and the render is JavaScript. A viewer who has learned
 * these two rows can read an unknown off the picture without opening the panel,
 * which is the whole point of giving it a consistent look.
 */
function explainMissingAttributes() {
  const dl = $('#legend dl');
  if (!dl) return;
  dl.insertAdjacentHTML('beforeend', `
    <dt><span class="swatch unknown" style="background-color:${UNKNOWN_COLOUR}"></span>colour
      not published</dt>
    <dd>The listing states no colour, so none is invented. The piece is drawn in
      this flat slate with a wireframe on its exact verified size and a diagonal
      hatch across one face. The dimensions are still measured; only the colour
      is unknown.</dd>
    <dt>material not published</dt>
    <dd>No stated material, so the surface stays plainly matte. Where a material
      <em>is</em> stated it is drawn — leather glossy, linen and bouclé matte,
      marble bright, metal reflective, glass see-through, and a rug's weave from
      its fibre.</dd>`);
}

async function boot() {
  explainMissingAttributes();
  const home = await api('/home/units');
  const { units } = home;
  state.assumptions = home.assumptions || [];
  const unitSel = $('#unit'), roomSel = $('#room');
  unitSel.innerHTML = units.map((u) => `<option value="${u.id}">${esc(u.label)}</option>`).join('');

  // Both modes serve the tiers from the same Python constant — live from
  // /home/units, static from the index the exporter wrote. Spelling the list
  // out here as well would let the dropdown offer an amount no plan exists for.
  const budgetSel = $('#budget');
  state.tiers = home.budget_tiers ?? [];
  budgetSel.innerHTML = state.tiers.map((t) =>
    `<option value="${t.id}" title="${esc(t.note)}">${esc(t.label)} — ${money(t.sar)} SAR</option>`
  ).join('');
  budgetSel.value = home.default_tier ?? state.tiers[0]?.id;

  const PLANNABLE = new Set(['living_dining', 'bedroom', 'master_bedroom',
                             'master_bedroom_1', 'master_bedroom_2']);
  const fillRooms = () => {
    const unit = units.find((u) => u.id === unitSel.value);
    const rooms = unit.rooms.filter((r) => PLANNABLE.has(r));
    roomSel.innerHTML = '<option value="__flat__">whole flat</option>'
      + rooms.map((r) => `<option value="${r}">${r.replace(/_/g, ' ')}</option>`).join('');
    roomSel.value = rooms.includes('living_dining') ? 'living_dining' : rooms[0];
  };
  unitSel.onchange = () => { fillRooms(); runPlan(); };
  fillRooms();

  $('#run').onclick = runPlan;
  $('#room').onchange = runPlan;
  $('#style').onchange = runPlan;
  $('#budget').onchange = runPlan;
  $('#picker-close').onclick = () => { $('#picker').hidden = true; };
  $('#picker').onclick = (e) => { if (e.target.id === 'picker') $('#picker').hidden = true; };
  document.querySelectorAll('#viewtools button').forEach((b) => {
    b.onclick = () => {
      state.view = b.dataset.view;
      state.viewer?.setView(state.view);
      document.querySelectorAll('#viewtools button').forEach((o) =>
        o.classList.toggle('active', o.dataset.view === state.view));
    };
  });
  addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#picker').hidden = true; });

  await runPlan();
}

boot();


/* ------------------------------------------------------------- finishing
 *
 * The gap between "this room passes every rule" and "this room looks
 * finished". The engine measures what is left over — bare wall, empty floor —
 * and sizes a piece for it from a stated rule.
 *
 * Every one of these says, out loud, that the catalogue cannot supply it. The
 * amazon.sa capture is furniture: no art, no plants, no mirrors. Inventing a
 * product to fill the hole would be the exact failure this project exists to
 * avoid, so the honest output is a measurement plus the search that would find
 * the object. For a shopping product that gap IS the finding.
 */
function renderFinishing(plan) {
  const host = $('#finishing');
  if (!host) return;
  const picks = plan._flat
    ? plan.rooms.flatMap((r) => (r.finishing || [])
        .map((s) => ({ ...s, room: r.room.replace(/_/g, ' ') })))
    : (plan.finishing || []);
  if (!picks.length) { host.innerHTML = ''; return; }

  const missing = picks.filter((s) => !s.in_catalogue).length;
  host.innerHTML = `
    <details id="finish" open>
      <summary>Finishing the room — ${picks.length} suggestion${picks.length > 1 ? 's' : ''}
        <span class="pill warn">${missing} not stocked</span></summary>
      <p class="muted small">Measured from the wall and floor this layout leaves
        empty, sized by a stated design rule, and drawn as a translucent outline
        in the render. Nothing here is sourced — the captured amazon.sa
        assortment has no art, plants or mirrors, so these are specifications to
        shop against, not products.</p>
      ${picks.map(finishRow).join('')}
    </details>`;
}

function finishRow(s) {
  const where = s.wall
    ? `${s.wall} wall, centred ${s.centre_height_cm} cm above the floor`
    : 'standing on open floor';
  const size = s.wall
    ? `${Math.round(s.width_cm)} × ${Math.round(s.height_cm)} cm`
    : `⌀${Math.round(s.width_cm)} × ${Math.round(s.height_cm)} cm tall`;
  const query = encodeURIComponent(s.search_query);
  return `
    <div class="finish-row">
      <div class="finish-head">
        <strong>${esc(s.label)}</strong>
        ${s.room ? `<span class="muted small">${esc(s.room)}</span>` : ''}
        <span class="dims">${size}</span>
      </div>
      <div class="muted small">${esc(where)} — ${esc(s.because)}</div>
      <div class="muted small rule-line">rule: ${esc(s.rule)}</div>
      ${s.in_catalogue
        ? '<div class="small ok-line">the catalogue stocks this category</div>'
        : `<div class="small warn-line">not in the captured catalogue —
             <a target="_blank" rel="noopener"
                href="https://www.amazon.sa/s?k=${query}">search amazon.sa for
                “${esc(s.search_query)}”</a></div>`}
    </div>`;
}

/* Exported only so the tests can run them.
 *
 * The agent log and the brief are the two places this page writes prose, and
 * prose is the one thing a source-reading test cannot judge. Both are pure
 * functions of the plan precisely so a test can run them over real exported
 * plans in node and check that every measurement they print is in the JSON
 * they printed it from. Nothing in the page imports these.
 */
export { agentLogHtml, briefHtml };
