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
// esc/money/measure/humaniseWithin/priceGap/swatch live in text.js so that
// brief.js — which studio.js loads lazily — can share the exact same ones
// rather than carry a second copy into its chunk.
import { esc, money, measure, humaniseWithin, priceGap, swatch } from './text.js';

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

/** A flat is several validated rooms; a room is one. Flatten for the panel so
 *  everything downstream — the agent log included — keeps working on a single
 *  list of placed items, with the original kept on `_flat` for the parts that
 *  have to speak about the rooms separately.
 *
 *  Named and exported rather than inlined into `render` so the tests can put a
 *  whole-flat plan through the same transform the page does, instead of
 *  reimplementing it beside it and drifting. */
function flatten(plan) {
  if (!plan.rooms) return plan;
  return {
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

function render(plan) {
  plan = flatten(plan);
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

function humanise(text) {
  return humaniseWithin(text, state.plan?.placed ?? []);
}

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

/* ---------------------------------------------------------------- what was
 * distinctive about THIS plan
 *
 * Two hundred configurations produce two hundred genuinely different plans:
 * some drop slots, some are refused by the lift, some are corridors and some
 * are halls, some spend a third of the budget and some spend all of it. Copy
 * that opens the same way over all of them is a template with the numbers
 * substituted, and it reads as one — which undercuts the claim that four
 * agents looked at this specific room.
 *
 * So each turn below picks its own lead from the plan. The helpers here are
 * the shared part of that: they find WHICH fact is the notable one. They never
 * invent the fact, and nothing here computes a measurement — every number that
 * reaches the reader is one the JSON already states, which is what lets
 * test_studio.py re-find all of them in the file they were printed from.
 */

/** Slots the recipe defines that this budget never put into play.
 *
 *  A flat records them per room, so they are gathered the way the panel
 *  gathers everything else: one list, room-qualified. */
function lockedSlots(plan) {
  const rooms = plan._flat?.rooms;
  if (rooms) {
    return rooms.flatMap((r) => (r.unspent_budget?.slots_locked_by_tier || [])
      .map((s) => ({ ...s, slot_id: `${r.room}: ${s.slot_id}` })));
  }
  return plan.unspent_budget?.slots_locked_by_tier || [];
}

/** The cheapest amount that would unlock any of them — a figure the plan
 *  states per slot, not one derived from the tier list. */
const nextUnlock = (locked) =>
  locked.reduce((lo, s) => Math.min(lo, s.unlocks_at_sar), Infinity);

const listWords = (xs) => xs.length > 1
  ? `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`
  : (xs[0] ?? '');

/** The same list, but a whole-flat plan can hold eighteen slots and naming all
 *  of them mid-sentence buries the sentence. The full set is never hidden — it
 *  is the panel below, and for the auditor one gotcha per line underneath. */
const listSome = (xs, cap = 4) => (xs.length > cap
  ? `${xs.slice(0, cap).join(', ')} and ${xs.length - cap} more`
  : listWords(xs));

/** Which of the two numbers in `room_cm` is the larger, and by enough to
 *  matter. Nothing is measured: this only decides which sentence gets written
 *  about a room the survey already dimensioned. */
function shapeOf(r) {
  const deep = r.depth > r.width;
  const long = deep ? r.depth : r.width;
  const short = deep ? r.width : r.depth;
  const ratio = long / short;
  return {
    deep, long, short,
    kind: ratio >= 1.5 ? 'elongated' : ratio >= 1.2 ? 'oblong' : 'squarish',
  };
}

/** The slot the planner had to work hardest for, and the one it got first
 *  time. `positions_tried` is recorded per slot and swings from 1 to over two
 *  hundred across these plans, so it is the honest answer to "was this room
 *  tight" — and it is an answer that differs per configuration. */
function effortOf(placed) {
  const tried = (i) => i.decision?.positions_tried ?? 0;
  const hardest = placed.reduce((a, b) => (tried(b) > tried(a) ? b : a), placed[0]);
  return {
    hardest, worst: hardest ? tried(hardest) : 0,
    firstTry: placed.filter((i) => tried(i) === 1).length,
    total: placed.reduce((n, i) => n + tried(i), 0),
  };
}

/* Zeina frames the brief and routes it. She never picks a product: eazli's own
 * page says she "doesn't give you a catalog or a quote; she gives you a map".
 * So this turn is only what was asked for and where it was sent — but WHICH of
 * those facts leads depends on the brief. A brief that came back short is the
 * thing to say first; a flat sharing one budget across four rooms is a
 * different opening again from a room whose tier locked half the recipe. */
function zeinaTurn(plan, ctx) {
  const flat = plan._flat;
  const dropped = plan.unfilled.length;
  const slots = plan.placed.length + dropped;
  const locked = lockedSlots(plan);
  const tier = ctx.tier ? esc(ctx.tier.label) : null;
  const budget = `${money(plan.budget_sar)} SAR`;
  const where = flat
    ? esc(flat.label)
    : `the ${slotWords(plan.room)} in ${esc(plan.unit)}`;
  const styled = (ctx.style || []).length ? esc(ctx.style.join(' + ')) : null;
  const u = unspentOf(plan);

  // Worst news first. Below that, whichever fact about this configuration is
  // the one the next configuration would not have produced.
  let opener;
  if (dropped) {
    const names = listSome(plan.unfilled.map((s) => slotWords(s.slot_id)));
    opener = `${dropped} of the ${slots} slots in ${where} came back empty — ${names} —
      so I could not meet this brief in full, and that is the first thing I owe you.
      ${measure(plan.unfilled[0].reason)} That is the ceiling you set talking,
      not the room: it is a real ${budget}, and it stopped here.`;
  } else if (flat) {
    const shares = flat.rooms.map((r) =>
      `${slotWords(r.room)} ${money(r.area_share_sar ?? r.budget_sar)} SAR`);
    opener = `${esc(flat.label)} is not a room, it is ${flat.rooms.length} of them and
      ${slots} slots sharing one ${budget}. I split the money by floor area before
      anything was drawn — ${listWords(shares)} — so the big room cannot quietly eat
      the small ones, and each was then laid out and sourced against its own share.`;
  } else if (locked.length) {
    const at = nextUnlock(locked);
    const names = listSome([...new Set(locked.map((s) => slotWords(s.slot_id)))]);
    opener = `${tier ? `The ${tier} tier` : `A ${budget} brief`} is what shaped this one:
      it puts ${slots} slot${slots === 1 ? '' : 's'} into play for
      ${where} and holds ${locked.length} back — ${names} — which this room does not
      get to consider below ${money(at)} SAR. What you are looking at is the
      essentials, deliberately, not everything the recipe could hold.`;
  } else {
    opener = `Nothing was held back on this one: every slot the recipe defines for
      ${where} went into play, all ${slots} of them${u && u.unspent_sar > 0
        ? `, and ${money(u.unspent_sar)} SAR of the ${budget} still came back
           unspent — the room ran out of things worth buying before the money ran
           out` : `, and it took ${money(plan.total_sar)} SAR of the ${budget} to do it`}.`;
  }

  // The routing is her job whatever the brief was, but what she had to route
  // is not the same thing every time: a stated style is a filter she can hand
  // Noura, and no style stated means there was nothing to hand over.
  const routing = styled
    ? `I don't hand you a catalogue or a quote; I hand you a map. So: the
       ${styled} brief and its ${slots} slot${slots === 1 ? '' : 's'} went to Noura
       to lay out, the ${budget} went to Adam to source against, and what the two
       of them agreed on went to fit-auditor, who is allowed to throw it out.`
    : `I don't hand you a catalogue or a quote; I hand you a map. You stated no
       style, so there was no filter to pass Noura beyond the
       ${slots} slot${slots === 1 ? '' : 's'} themselves and Adam had to rank on
       evidence rather than taste. The ${budget} was his ceiling, and what the two
       of them agreed on went to fit-auditor, who is allowed to throw it out.`;

  return agentTurn('zeina', `${opener} ${routing}`);
}

/** The door as the survey recorded it — width, wall and offset all stated. */
function doorLine(r) {
  const doors = r.doors || [];
  if (!doors.length) return 'The plan locates no door in it.';
  const d = doors[0];
  return `The door is <code>${d.width_cm}&nbsp;cm</code> on the ${esc(d.wall)} wall,
    <code>${d.offset_cm}&nbsp;cm</code> from the corner`
    + (doors.length > 1 ? `, and there are ${doors.length} in all.` : '.');
}

/** The lead sentence about a room, which is the room's own proportions.
 *
 *  A 335x551 corridor and a 610x335 hall are not the same problem and must not
 *  get the same sentence. Which one this is comes from `room_cm`; the piece
 *  named against it is the largest footprint the plan actually placed, printed
 *  at the size the listing states rather than measured against an axis it may
 *  have been rotated onto. */
function shapeSentence(r, placed) {
  const s = shapeOf(r);
  const dims = `<code>${r.width}&times;${r.depth}&nbsp;cm</code>`;
  const n = placed.length;
  const load = `${n} piece${n === 1 ? '' : 's'} to seat in it`;
  const biggest = placed.length
    ? placed.reduce((a, b) =>
        (b.dims_cm.w * b.dims_cm.d > a.dims_cm.w * a.dims_cm.d ? b : a))
    : null;
  const against = biggest
    ? ` <code>${s.short}&nbsp;cm</code> is the tighter of the two, and the largest
        footprint I had to seat inside it is the ${slotWords(biggest.slot_id)} at
        <code>${biggest.dims_cm.w}&times;${biggest.dims_cm.d}&nbsp;cm</code>.`
    : '';

  if (s.kind === 'elongated') {
    return s.deep
      ? `${dims}: this one runs <code>${r.depth}&nbsp;cm</code> back from the door wall
         on a frontage of only <code>${r.width}&nbsp;cm</code>, which is a corridor to lay
         out rather than a room, and I had ${load}.${against}`
      : `${dims}: <code>${r.width}&nbsp;cm</code> across and only
         <code>${r.depth}&nbsp;cm</code> front to back, so this is a hall — width to spare,
         depth to fight over, and ${load}.${against}`;
  }
  if (s.kind === 'oblong') {
    return s.deep
      ? `${dims}, noticeably deeper than it is wide, with ${load} — a long rectangle,
         though not the corridor the living rooms in this building can be.${against}`
      : `${dims}, a wide and shallow rectangle with ${load}: frontage to spread along
         and less room to come forward into.${against}`;
  }
  return `${dims} with ${load} — near enough square that nothing is forced onto one
    axis, so the proportions are not what constrains this one.${against}`;
}

/* Noura owns the layout: the slots, the positions, and the engine's verdict on
 * the arrangement as a whole — including the advisories in validation.notes,
 * which are hers because they are about the room rather than a product.
 *
 * She leads on the room, and the rooms are not alike: a corridor, a hall and a
 * square each get their own sentence, and what happened next is read off
 * `positions_tried`, which is 1 where a piece dropped straight in and over two
 * hundred where the room fought back. */
function nouraTurn(plan) {
  const flat = plan._flat;
  const filled = plan.placed.length + plan.unfilled.length;
  const notes = [...new Set(plan.validation.notes || [])];
  const e = effortOf(plan.placed);

  const shape = flat
    ? `${plan.placed.length} pieces across ${flat.rooms.length} rooms, and no two of
       those rooms the same problem:
       ${listWords(flat.rooms.map((rm) =>
         `${slotWords(rm.room)} at <code>${rm.room_cm.width}&times;${rm.room_cm.depth}&nbsp;cm</code>`))}.
       Each was dimensioned off the surveyed floor plan and laid out on its own —
       nothing was copied from one room into the next.`
    : `${shapeSentence(plan.room_cm, plan.placed)} ${doorLine(plan.room_cm)}`;

  // An advisory is about the room, so it outranks the room's proportions: the
  // reader needs the caveat before the description it qualifies.
  //
  // It still has to say WHICH room, in that same first sentence. Three of the
  // nine configurations in the test spread carry exactly one advisory and it
  // is the same sentence in all three — a rug under the same N-wall door swing
  // — so an opening that led with the advisory alone gave a 335x551 corridor,
  // a 335x305 bedroom and a 427x305 bedroom the identical first line. The
  // dimensions are the plan's own and cost nothing to keep.
  const about = flat
    ? `the layout for ${esc(flat.label)}`
    : `this <code>${plan.room_cm.width}&times;${plan.room_cm.depth}&nbsp;cm</code> layout`;
  const advisoryLead = notes.length
    ? `Before anything else about ${about}: the engine left
       ${notes.length} advisor${notes.length === 1 ? 'y' : 'ies'} on it —
       ${notes.map((n) => measure(flat ? n : humaniseWithin(n, plan.placed))).join(' ')} `
    : '';

  let effort;
  if (!plan.placed.length) {
    effort = 'Nothing could be placed here, so there is no layout to describe.';
  } else if (e.worst <= 1) {
    effort = `It was not a fight: all ${plan.placed.length} pieces held in the very
      first position I tested.`;
  } else {
    const h = e.hardest;
    // Printed as the plan states it, not rounded: a rounded number is one the
    // reader cannot find in the data behind the page.
    const at = `<code>(${h.x}, ${h.y})&nbsp;cm</code> facing ${esc(h.facing)}`;
    const because = h.decision?.placed_because
      ? ` — ${esc(h.decision.placed_because)}` : '';
    const easy = e.firstTry
      ? `${e.firstTry} of the ${plan.placed.length} pieces dropped straight into the
         first position tried`
      : `not one of the ${plan.placed.length} pieces dropped straight in`;
    effort = e.worst >= 50
      ? `The ${slotWords(h.slot_id)} is where the room fought back: ${e.worst} positions
         tested before one held, at ${at}${because}. ${easy}, and ${e.total} positions
         were tried across the room in all.`
      : `It went in fairly straightforwardly. The most awkward piece was the
         ${slotWords(h.slot_id)} at ${e.worst} positions tested, settling at ${at}${because},
         and ${easy}.`;
  }

  const verdict = plan.validation.status === 'pass'
    ? `I filled ${plan.placed.length} of the ${filled} slot${filled === 1 ? '' : 's'} the
       recipe defines, and the engine re-checked the finished arrangement and returned
       <b class="v-pass">pass</b> with nothing listed against it.`
    : `I filled ${plan.placed.length} of the ${filled} slot${filled === 1 ? '' : 's'} the
       recipe defines. The engine returned
       <b class="v-${plan.validation.status}">${plan.validation.status}</b> on the finished
       arrangement; what it said is fit-auditor's to read out.`;

  return agentTurn('noura', `${advisoryLead}${shape} ${effort} ${verdict}`);
}

/** The planner records WHY it took each piece, and the four things it can say
 *  are genuinely different kinds of decision — a style match is routine, a
 *  budget target it deliberately undershot is not. Classifying on the recorded
 *  phrase, never on a guess about the product. */
function reasonKind(why) {
  if (/^budget target/.test(why)) return 'target';
  if (/^cheapest that fits/.test(why)) return 'cheapest';
  if (/^best evidence/.test(why)) return 'evidence';
  if (/^matched /.test(why)) return 'style';
  return 'other';
}

const REASON_RANK = { target: 4, cheapest: 3, evidence: 2, style: 1, other: 0 };
const KIND_WORDS = {
  target: 'came in under a per-slot budget target',
  cheapest: 'had no style tag and no rating to go on, so price decided',
  evidence: 'was ranked on review evidence',
  style: 'matched the style words',
  other: 'was recorded without a reason',
};

/* Adam owns sourcing. Listing every slot in recipe order gave every plan the
 * same paragraph with the nouns changed, so he leads with the slot where the
 * decision was actually interesting — a target he undershot, a pool with
 * nothing to rank on, a fallback to evidence, or failing all of that the
 * biggest single line — and compresses the rest into a tally. */
function adamTurn(plan) {
  if (!plan.placed.length) {
    return agentTurn('adam', `I sourced nothing: no slot in this plan got as far as
      a product I could price.`);
  }
  const scored = plan.placed.map((i) => {
    const d = i.decision || {};
    const why = d.chose_because ?? '';
    return { i, d, why, kind: reasonKind(why) };
  });
  // Deterministic: rarest kind of decision first, then the biggest spend, then
  // the slot name. The same plan always leads on the same slot.
  const order = scored.slice().sort((a, b) =>
    (REASON_RANK[b.kind] - REASON_RANK[a.kind])
    || ((b.i.price_sar || 0) - (a.i.price_sar || 0))
    || (a.i.slot_id < b.i.slot_id ? -1 : 1));
  const top = order[0];
  const { i, d, why, kind } = top;
  const runner = (d.ranked_above || [])[0];
  const pool = `${d.considered} in the pool`;
  const took = `${esc(String(i.title).slice(0, 60))} at ${money(i.price_sar)} SAR`;

  let lead;
  if (kind === 'target') {
    lead = `The ${slotWords(i.slot_id)} is where the budget showed its hand:
      ${measure(why)}. So ${took}, out of ${pool} — the money was there and nothing
      dearer earned it.`;
  } else if (kind === 'cheapest') {
    lead = `The ${slotWords(i.slot_id)} is the one I had nothing to judge on —
      ${esc(why)} — so ${took}, out of ${pool}. That is not a recommendation; it is
      what is left when the listings publish neither a style word nor a rating.`;
  } else if (kind === 'evidence') {
    lead = `The ${slotWords(i.slot_id)} came down to evidence rather than taste —
      ${esc(why)} — so ${took}, out of ${pool}, is the one to look at first.`;
  } else {
    lead = `The biggest single line here is the ${slotWords(i.slot_id)}: ${took},
      ${esc(why || 'no reason was recorded')}, out of ${pool}`
      + (runner
        ? `, taken over ${esc(String(runner.title).slice(0, 40))} at
           ${money(runner.price_sar || 0)} SAR`
        : '') + '.';
  }
  if (kind !== 'style' && runner) {
    lead += ` It was ranked above ${esc(String(runner.title).slice(0, 40))} at
      ${money(runner.price_sar || 0)} SAR.`;
  }

  const rest = order.slice(1);
  const tally = Object.entries(rest.reduce((t, s) => {
    t[s.kind] = (t[s.kind] || 0) + 1; return t;
  }, {})).sort((a, b) => b[1] - a[1]);
  const restLine = rest.length
    ? `The other ${rest.length}, compressed: ${listSome(rest.map((s) =>
        `${slotWords(s.i.slot_id)} ${money(s.i.price_sar)} SAR`), 6)}. Of those,
       ${listWords(tally.map(([k, n]) => `${n} ${KIND_WORDS[k]}`))}.`
    : 'There was no second slot to source.';

  const u = unspentOf(plan);
  const tail = `That is ${money(plan.total_sar)} SAR against
    ${money(plan.budget_sar)} SAR${u && u.unspent_sar > 0
      ? `, leaving ${money(u.unspent_sar)} SAR I could not spend on anything that
         verified` : ''}.`;
  return agentTurn('adam', `${lead}<br>${restLine}<br>${tail}${slotRecordHtml(plan)}`);
}

/* ----------------------------------------------- the record, slot by slot
 *
 * Three questions per placed item, and the answers to all three are already on
 * disk before anyone asks:
 *
 *   why this one won   `decision.chose_because` and `considered`, plus the
 *                      spatial half in `placed_because` / `positions_tried`
 *   what it beat       `decision.ranked_above` — the runners-up and their prices
 *   what else you have `data/swaps/<swaps_ref>.json`, which carries a full
 *                      engine verdict for every alternative in every slot
 *
 * The first two ride on the plan this page has already fetched, so they print
 * here. The third does not, and deliberately stays where it is: a swap table is
 * 48-670 KB and a candidate list a further ~15 KB per category, which for one
 * living room is ~200 KB against a whole first paint of 68 KB. So the record
 * ends at the control that already fetches them, and the attribute-by-attribute
 * comparison and the alternatives with their real verdicts print in the brief
 * behind it — per slot, on demand, which is what that data was split up for.
 */
/* The same row the product brief uses, so the record and the brief behind it
 * share one label gutter instead of defining a second. */
const recLine = (label, body) =>
  `<div class="d-row"><span>${label}</span><div>${body}</div></div>`;

function slotRecordHtml(plan) {
  if (!plan.placed.length) return '';
  const items = plan.placed.map((i) => {
    const d = i.decision || {};
    const lines = [];

    lines.push(recLine('why this one', `${esc(d.chose_because ?? 'no reason recorded')}
      — out of ${d.considered ?? 0} in the pool.`));

    if (d.placed_because || d.positions_tried) {
      lines.push(recLine('where it sits',
        `${esc(d.placed_because ?? 'no position reason recorded')}`
        + (d.positions_tried
          ? ` <em>${d.positions_tried} position(s) tested for it.</em>` : '')));
    }

    // Labelled for what the field is rather than for what it is called. The
    // planner writes the head of the ranked pool into `ranked_above`, and some
    // of those scored above the winner and were thrown out before position was
    // tried — the brief behind the button says which, per entry.
    const above = d.ranked_above || [];
    const thrownOut = new Set((d.rejected || []).map((r) => r.asin));
    lines.push(recLine('up against', above.length
      ? above.map((r) => `${esc(String(r.title).slice(0, 52))}
          <em>${money(r.price_sar || 0)} SAR</em> — ${priceGap(r, i)}`
          + (thrownOut.has(r.asin) ? ', thrown out on a measurement' : '')).join('<br>')
      : 'nothing — it was the only candidate in the pool.'));

    const rejected = d.rejected || [];
    if (rejected.length) {
      lines.push(recLine('ruled out', `${rejected.length} thrown out on a measurement
        first — those are under fit-auditor below.`));
    }

    // Collapsed, and one <details> per slot rather than one around the lot.
    // A premium living room fills eleven of these and running them all open put
    // 1,300 words under Adam that a reader has to scroll past to reach the
    // auditor. The summary carries the slot, the piece and the price, so the
    // closed state is still the whole record at a glance — it is the reasoning
    // that folds away, not the answer.
    // Set tight on purpose. Indentation inside a template literal is not
    // whitespace the minifier can remove — it is string content, and it ships.
    return `<li><details><summary>`
      + `<b>${slotWords(i.slot_id)}</b>`
      + `<span class="sr-title">${esc(String(i.title).slice(0, 72))}</span>`
      + `<span class="sr-price">${money(i.price_sar)} SAR</span>`
      + `</summary>${lines.join('')}`
      + `<button type="button" class="link sr-more" data-slot="${esc(i.slot_id)}">`
      + `what separated them, and what else fits &rsaquo;</button></details></li>`;
  });
  return `<div class="slot-record"><ol>${items.join('')}</ol></div>`;
}

// Every cause the planner counts is already a phrase. Underscores out, and
// nothing else: renaming them here would put a second vocabulary on screen.
const causeWords = (cause) => esc(cause.replace(/_/g, ' '));

/* The auditor is the interesting one, and the only one allowed to be negative.
 * Every line it prints is a rejection, an empty slot or a failed rule that the
 * plan already recorded — including the stage each rejection died at. When the
 * plan holds none of that, it says so rather than manufacturing a worry. */
/** Every delivery refusal the engine writes names its own leg first:
 *  `lift car doors (door 85x210cm): item 150x213x99cm cannot pass...`. Reading
 *  that name off the front of the recorded sentence adds no claim to it — it is
 *  the same string, cut at the bracket the engine put there. */
const legName = (why) => String(why).split(' (')[0].trim();

function auditorTurn(plan) {
  const gotchas = [];
  const seen = new Set();
  const stages = {};             // which stage killed things, and how many
  const blockingLegs = {};       // and, at the delivery stage, which leg
  const deliverySlots = new Set();  // and which slots paid for it
  for (const i of plan.placed) {
    for (const r of i.decision?.rejected || []) {
      const key = `${r.asin}|${r.why}`;
      if (seen.has(key)) continue;   // the same listing loses in several rooms
      seen.add(key);
      stages[r.stage] = (stages[r.stage] || 0) + 1;
      if (r.stage === 'delivery') {
        const leg = legName(r.why);
        blockingLegs[leg] = (blockingLegs[leg] || 0) + 1;
        deliverySlots.add(i.slot_id);
      }
      gotchas.push(`<b>${slotWords(i.slot_id)}</b> · ${esc(String(r.title).slice(0, 44))}
        rejected at the <i>${esc(r.stage)}</i> stage —
        ${measure(humaniseWithin(r.why, plan.placed))}`);
    }
  }
  const empties = [];
  for (const s of plan.unfilled) {
    empties.push(s);
    gotchas.push(`<b>${slotWords(s.slot_id)}</b> · left empty — ${measure(s.reason)}`
      + ((s.rejected || []).length
        ? ` ${s.rejected.length} candidate(s) were ruled out before it gave up.` : ''));
  }
  const failures = plan.validation.reasons || [];
  for (const reason of failures) {
    gotchas.push(`<b>engine verdict</b> · ${measure(
      plan._flat ? reason : humaniseWithin(reason, plan.placed))}`);
  }

  const u = unspentOf(plan);
  const tally = Object.entries(u?.candidates_rejected || {})
    .filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const scope = tally.length
    ? ` Behind the plan: of ${u.candidates_in_scope} listing(s) in scope for these
        categories${u.rooms ? `, counted once per room across ${u.rooms} rooms` : ''},
        ${tally.map(([cause, n]) => `${n} ${causeWords(cause)}`).join(', ')}.`
    : '';

  // Three plans can each have "things worth seeing" and be three completely
  // different stories: an arrangement the engine threw out, a slot nobody could
  // fill, and a piece that fits the room perfectly but cannot get up the lift.
  // Worst first, and the opening says which of them this is — including which
  // leg of the delivery route did the refusing, because "the lift car doors"
  // and "the turn into the flat" are two different things to go and measure.
  const rejects = gotchas.length - empties.length - failures.length;
  const delivery = stages.delivery || 0;
  const elsewhere = rejects - delivery;
  const also = (n, what) => (n ? ` Below that, ${n} ${what}.` : '');
  let opening;
  if (failures.length) {
    opening = `Start with the verdict: the engine returned
      <b class="v-${plan.validation.status}">${esc(plan.validation.status)}</b> on this
      arrangement and listed ${failures.length}
      reason${failures.length === 1 ? '' : 's'} against it. Nothing below matters until
      that does.`
      + also(empties.length, 'slot(s) came back empty')
      + also(rejects, 'listing(s) were ruled out on a measurement');
  } else if (empties.length) {
    opening = `${empties.length} slot${empties.length === 1 ? '' : 's'} in this plan
      ${empties.length === 1 ? 'is' : 'are'} empty — ${listSome(empties.map((s) =>
        slotWords(s.slot_id)))} — so the arrangement holds but the room is unfinished,
      which is a different thing from a room that failed.`
      + also(rejects, 'listing(s) were ruled out with a measurement before that');
  } else if (delivery) {
    // Which legs, in the words the engine wrote them in, commonest first.
    const legs = Object.entries(blockingLegs).sort((a, b) => b[1] - a[1]);
    const named = listWords(legs.slice(0, 2).map(([leg, n]) =>
      `${n} at the ${esc(leg)}`));
    const where = listSome([...deliverySlots].map(slotWords), 3);
    opening = (delivery === 1
      ? `The ${where} is the only slot where this plan lost anything to the building
         rather than to the room: one listing that fits the space was turned back at
         the ${esc(legs[0][0])}.`
      : `${delivery} listings that would have fitted this room were refused on the way
         to it — ${named}${legs.length > 2
           ? `, plus the rest across ${legs.length - 2} other leg(s) of the route` : ''} —
         and they cost the ${where}.`)
      + ` Nothing in the plan breaks a rule inside the room; it is the route that
        removed them, at the <i>delivery</i> stage, and a route you can go and measure.`
      + also(elsewhere, 'other(s) went at an earlier stage');
  } else if (rejects) {
    opening = `${rejects} listing${rejects === 1 ? '' : 's'} in this plan
      ${rejects === 1 ? 'was' : 'were'} ruled out on a measurement before the winner
      stood. Nothing was left empty and the engine found nothing to say against the
      arrangement, so this is the shortlist you did not see.`;
  } else {
    // Even a clean plan is not the same clean plan twice: what the catalogue
    // threw away before this room was ever consulted differs per category set,
    // so that is what leads instead of a stock all-clear.
    const worst = tally[0];
    const lead = worst
      ? `${worst[1]} of the ${u.candidates_in_scope} listings in scope for these
         categories never reached this room at all — ${causeWords(worst[0])} — and that
         was settled in the catalogue, before any of it was my problem. `
      : '';
    return agentTurn('auditor', `${lead}Nothing to object to in the plan itself: no
      candidate was rejected on a measurement, no slot was left empty, and the engine
      returned ${esc(plan.validation.status)} with no reason against it.${scope}`,
      gotchas);
  }
  return agentTurn('auditor', opening + scope, gotchas);
}

/* ---------------------------------------------------------------- the panel
 *
 * This is a decision RECORD, and it says so. The four agents genuinely own
 * these decisions in this system's design — Zeina the brief, Noura the layout,
 * Adam the sourcing, fit-auditor the rejections — so attributing each finding
 * to the agent whose remit it falls under is accurate. Staging it as a
 * conversation would not be: the plan is the output of a deterministic Python
 * planner, and no turn in it ever happened in time. A synthetic thread would
 * fabricate the one thing this project sells, which is that what you are shown
 * actually occurred.
 *
 * The real thing exists and is linked instead of imitated: docs/demo-run.md and
 * the debate section of SHOWCASE.md hold an actual multi-agent run, with real
 * subagents, real MCP tool calls, and a real disagreement the sourcing agent
 * won against the auditor.
 */
function agentLogHtml(plan, ctx) {
  const what = plan._flat ? `these ${plan._flat.rooms.length} rooms` : 'this room';
  return `<p class="muted small"><b>Decision record</b> for ${what}, under the agent
    whose remit each decision falls under. A log, not a transcript: nothing below was
    said by anybody — <code>app/planner.py</code> wrote it and
    <code>app/geometry.py</code> re-checked it. A real multi-agent exchange, with the
    sourcing agent contradicting the auditor and turning out to be right, is in
    <code>docs/demo-run.md</code> and <code>SHOWCASE.md</code>.</p>`
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
  // The record ends at the two questions it deliberately does not answer
  // inline — what each runner-up lost on, and what else fits — because both
  // need files this page has not fetched. The button is where they get fetched.
  host.querySelectorAll('.sr-more').forEach((b) => {
    b.onclick = () => openBrief(b.dataset.slot);
  });
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
/* Lives in brief.js and is fetched the first time a product is opened.
 *
 * It is the three parser glossaries plus the two derived rows — what the
 * winner beat and on which term, and what else the engine verified would fit
 * — and none of it is on screen until someone clicks. Deferring it is the same
 * bargain viewer.js already takes with three.js: the plan, the verdicts and the
 * decision record paint from bytes the page has, and the rest arrives when it
 * is asked for. */

/** The two files the comparison and the alternatives are read out of. Neither
 *  is fetched until a brief is actually opened: between them they are several
 *  times the size of the plan, and the page paints without either. */
async function briefExtras(item) {
  const extras = { style: state.style, candidates: null, swaps: null,
                   swapsMissing: !state.plan?.swaps_ref };
  const ref = state.plan?.swaps_ref;
  const [list, swaps] = await Promise.all([
    item.category
      ? api(`/plan/candidates/${item.category}`).catch(() => null) : null,
    ref && STATIC ? json(`./data/swaps/${ref}.json`).catch(() => null) : null,
  ]);
  if (list?.items) {
    extras.candidates = Object.fromEntries(list.items.map((c) => [c.asin, c]));
  }
  extras.swaps = swaps;
  if (!swaps) extras.swapsMissing = true;
  return extras;
}

/* The brief module itself, fetched once and kept.
 *
 * A few KB over the network the first time a product is opened, and nothing on
 * the paths that paint the page. If the chunk fails to load the modal says so
 * rather than sitting empty — the same rule the viewport follows. */
let briefModule = null;
const loadBrief = () => (briefModule ??= import('./brief.js'));

function openBrief(slotId) {
  const item = state.plan?.placed.find((p) => p.slot_id === slotId);
  if (!item) return;
  const title = slotId.replace(/_/g, ' ');
  $('#picker-title').textContent = title;
  $('#picker-list').innerHTML = '<p class="picker-msg">Loading the brief…</p>';
  $('#picker').hidden = false;
  const stale = () => $('#picker').hidden || $('#picker-title').textContent !== title;

  loadBrief().then(({ briefHtml }) => {
    if (stale()) return;
    // Everything the plan already holds paints now; the two heavier files fill
    // in the last two rows when they land. A brief that waited for them would
    // be a blank modal for as long as a 670 KB fetch takes.
    $('#picker-list').innerHTML = briefHtml(item, state.plan, null);
    return briefExtras(item).then((extras) => {
      if (stale()) return;
      $('#picker-list').innerHTML = briefHtml(item, state.plan, extras);
    });
  }).catch((err) => {
    if (stale()) return;
    $('#picker-list').innerHTML =
      `<p class="picker-msg error">Could not load the brief — ${esc(err.message)}</p>`;
  });
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
 * The decision record and the brief are the two places this page writes prose,
 * and prose is the one thing a source-reading test cannot judge. Both are pure
 * functions of the plan precisely so a test can run them over real exported
 * plans in node and check that every measurement they print is in the JSON
 * they printed it from. Nothing in the page imports these.
 *
 * The brief's own builders are exported from brief.js, which the page loads
 * lazily and the test harness imports directly.
 */
export { agentLogHtml, flatten };
