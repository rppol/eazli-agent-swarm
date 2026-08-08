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
 * `background` shorthand, because the shorthand would wipe the CSS hatch. */
function swatch(i) {
  return i.colour_hex
    ? `<span class="swatch" style="background-color:${i.colour_hex}"
             title="colour published by the listing"></span>`
    : `<span class="swatch unknown" style="background-color:${UNKNOWN_COLOUR}"
             title="the listing publishes no colour"></span>`;
}

function paintPanel(plan) {
  $('#items').innerHTML = plan.placed.map((i) => `
    <div class="item" data-slot="${i.slot_id}" data-role="${i.role}">
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
  $('#items').querySelectorAll('.item[data-slot]').forEach((el) => {
    el.onmouseenter = () => highlight(el.dataset.slot, true);
    el.onmouseleave = () => highlight(el.dataset.slot, false);
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
