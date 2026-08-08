/* eazli studio.
 *
 * The browser draws and drags. It never decides. Every arrangement — after a
 * plan, a swap, or a drag — is posted to /plan/swap and the verdict comes back
 * from app/geometry.py, the same module the tests and the agents call.
 *
 * That boundary is the whole point of the project, so it is worth stating in
 * the code: there is no geometry in this file beyond turning centimetres into
 * metres for three.js.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const CM = 0.01;                       // engine speaks centimetres, three.js metres
const COLOR = {
  sofa: 0x4c6ef5, coffee_table: 0x12b886, dining_table: 0xe8590c,
  floor_lamp: 0xae3ec9, tv_console: 0x5c7cfa, bed: 0x4c6ef5,
  wardrobe: 0x7950f2, dining_chairs_pair: 0xf59f00, other: 0x8b98a5,
};
const CATEGORY_FOR_ROLE = {
  sofa: 'sofa', coffee_table: 'coffee_table', dining_table: 'dining_table',
  floor_lamp: 'floor_lamp', tv_console: 'tv_unit', bed: 'bed', wardrobe: 'wardrobe',
};

const $ = (s) => document.querySelector(s);
const state = { plan: null, room: null, style: [], assumptions: [], selected: null, meshes: new Map() };

// ---------------------------------------------------------------- 3D scene

const host = $('#canvas-host');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0e13);
scene.fog = new THREE.Fog(0x0b0e13, 22, 60);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
host.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI / 2 - 0.02;   // never go under the floor

scene.add(new THREE.HemisphereLight(0xffffff, 0x4a5361, 2.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.0);
keyLight.position.set(6, 11, 5);
keyLight.castShadow = true;
keyLight.shadow.mapSize.set(2048, 2048);
Object.assign(keyLight.shadow.camera, { left: -12, right: 12, top: 12, bottom: -12, far: 40 });
scene.add(keyLight);

const world = new THREE.Group();
scene.add(world);

function resize() {
  const { clientWidth: w, clientHeight: h } = host;
  if (!w || !h) return;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  // updateStyle must stay on. Passing `false` here left the canvas at its
  // intrinsic 300x150 CSS size — the drawing buffer was right and the element
  // on screen was a postage stamp.
  renderer.setSize(w, h);
}
new ResizeObserver(resize).observe(host);
addEventListener('resize', resize);

(function loop() {
  requestAnimationFrame(loop);
  controls.update();
  renderer.render(scene, camera);
})();

// Exposed so the scene can be inspected without a visible window. A hidden or
// minimised tab never fires requestAnimationFrame, so "nothing is drawn" and
// "nothing is there" look identical from outside — this tells them apart.
window.__studio = { scene, camera, renderer, world, state, render: () => renderer.render(scene, camera) };

// ---------------------------------------------------------------- geometry → mesh

function clear(group) {
  while (group.children.length) {
    const c = group.children.pop();
    c.traverse?.((o) => { o.geometry?.dispose(); o.material?.dispose?.(); });
  }
}

/** Engine coords (x east, y south, origin NW) → three.js (x east, z south, centred). */
function toWorld(x, y, w, d, room) {
  return {
    x: (x + w / 2 - room.width / 2) * CM,
    z: (y + d / 2 - room.depth / 2) * CM,
  };
}

function buildRoom(room) {
  clear(world);
  state.meshes.clear();
  const W = room.width * CM, D = room.depth * CM, H = room.height * CM;

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(W, D),
    new THREE.MeshStandardMaterial({ color: 0x5c6672, roughness: 0.92 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  world.add(floor);

  // A GridHelper is always square, so on a 3.35 x 5.51m room it hung a metre
  // off both long sides and read as a rendering fault. Draw the metre lines
  // inside the actual floor rectangle instead.
  const lines = [];
  for (let x = 1; x < room.width / 100; x++) {
    const px = x * 100 * CM - W / 2;
    lines.push(px, 0, -D / 2, px, 0, D / 2);
  }
  for (let z = 1; z < room.depth / 100; z++) {
    const pz = z * 100 * CM - D / 2;
    lines.push(-W / 2, 0, pz, W / 2, 0, pz);
  }
  const gridGeo = new THREE.BufferGeometry();
  gridGeo.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3));
  const grid = new THREE.LineSegments(
    gridGeo,
    new THREE.LineBasicMaterial({ color: 0x9aa4b0, transparent: true, opacity: 0.35 }),
  );
  grid.position.y = 0.004;
  world.add(grid);

  // The entrance, so the room has an orientation. Without it the viewer cannot
  // tell which end is the door, and "needs tipping at the flat entrance" has
  // nothing to point at.
  for (const door of room.doors ?? []) {
    const leaf = door.width_cm * CM;
    const sweep = new THREE.Mesh(
      new THREE.PlaneGeometry(leaf, leaf),
      new THREE.MeshBasicMaterial({ color: 0xd29922, transparent: true, opacity: 0.16, side: THREE.DoubleSide }),
    );
    sweep.rotation.x = -Math.PI / 2;
    sweep.position.set(door.offset_cm * CM + leaf / 2 - W / 2, 0.006, -D / 2 + leaf / 2);
    world.add(sweep);

    const jamb = new THREE.Mesh(
      new THREE.BoxGeometry(leaf, 0.06, 0.05),
      new THREE.MeshBasicMaterial({ color: 0xd29922 }),
    );
    jamb.position.set(sweep.position.x, 0.03, -D / 2);
    world.add(jamb);
  }

  // Two walls only: enough to read the space, low enough to see into it.
  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x788393, roughness: 1, transparent: true, opacity: 0.4, side: THREE.DoubleSide,
  });
  const north = new THREE.Mesh(new THREE.PlaneGeometry(W, H), wallMat);
  north.position.set(0, H / 2, -D / 2);
  world.add(north);
  const west = new THREE.Mesh(new THREE.PlaneGeometry(D, H), wallMat);
  west.rotation.y = Math.PI / 2;
  west.position.set(-W / 2, H / 2, 0);
  world.add(west);
}

function addItem(item, room) {
  const { w, d, h } = item.dims_cm;
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(w * CM, h * CM, d * CM),
    new THREE.MeshStandardMaterial({
      color: COLOR[item.role] ?? COLOR.other, roughness: 0.6, metalness: 0.05,
    }),
  );
  const { x, z } = toWorld(item.x, item.y, w, d, room);
  mesh.position.set(x, (h * CM) / 2, z);
  mesh.castShadow = mesh.receiveShadow = true;
  mesh.userData.slot = item.slot_id;

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(mesh.geometry),
    new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.22 }),
  );
  mesh.add(edges);

  world.add(mesh);
  state.meshes.set(item.slot_id, mesh);
}

// Unfilled slots are deliberately NOT drawn in the room.
//
// They were, as translucent footprints — at Math.random() positions, because
// the planner never computed a position for something it could not place.
// A marker on the floor asserts a location, and inventing one to represent
// "we found nothing" is the same class of lie as guessing a dimension. The
// panel lists every unfilled slot with the measurement that ruled its
// candidates out, which is the honest place for it.

// ---------------------------------------------------------------- views

/** Distance at which a room of this size fills the frame, given the current
 *  field of view and aspect. Fixed multipliers cropped furniture off the edge
 *  as soon as the room was not the shape they were tuned for. */
function fitDistance(room, margin = 1.25) {
  const W = room.width * CM, D = room.depth * CM, H = room.height * CM;
  const radius = Math.hypot(W, D, H) / 2;
  const vFov = (camera.fov * Math.PI) / 180;
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
  return (radius / Math.sin(Math.min(vFov, hFov) / 2)) * margin;
}

/** Place the camera along a unit direction at exactly the fitting distance.
 *  Scaling a non-unit vector by that distance put it ~20% too close and
 *  cropped the floor off two edges. */
function along(dir, distance) {
  const len = Math.hypot(...dir);
  return dir.map((c) => (c / len) * distance);
}

const VIEWS = {
  iso: (r) => ({ p: along([0.85, 0.78, 0.85], fitDistance(r)), t: [0, 0.35, 0] }),
  top: (r) => ({ p: [0, fitDistance(r, 1.1), 0.001], t: [0, 0, 0] }),
  eye: (r) => ({ p: [0, 1.55, r.depth * CM * 0.52], t: [0, 1.15, -r.depth * CM * 0.3] }),
};

function setView(name) {
  if (!state.room) return;
  const { p, t } = VIEWS[name](state.room);
  camera.position.set(...p);
  controls.target.set(...t);
  controls.update();
  document.querySelectorAll('#viewtools button').forEach((b) =>
    b.classList.toggle('active', b.dataset.view === name));
}

// ---------------------------------------------------------------- rendering the plan

function render(plan) {
  state.plan = plan;
  state.room = plan.room_cm;
  buildRoom(plan.room_cm);
  plan.placed.forEach((i) => addItem(i, plan.room_cm));
  paintVerdict(plan.validation, plan.total_sar, plan.budget_sar);
  paintHow(plan);
  paintPanel(plan);
}

function paintHow(plan) {
  const considered = plan.placed.reduce((n, i) => n + (i.decision?.considered ?? 0), 0);
  const positions = plan.placed.reduce((n, i) => n + (i.decision?.positions_tried ?? 0), 0);
  const ruledOut = plan.placed.reduce((n, i) => n + (i.decision?.rejected?.length ?? 0), 0);

  $('#how-body').innerHTML = `
    <ol class="pipeline">
      <li><b>Brief</b><span>${plan.room.replace(/_/g, ' ')} in ${plan.unit},
        ${money(plan.budget_sar)} SAR, ${state.style.join(' + ') || 'no style preference'}</span></li>
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
  m.innerHTML = `<b>${money(total)}</b> / ${money(budget)} SAR`;

  // A red badge whose explanation sits five product cards further down is not
  // an explanation. Put the first reason beside the verdict and scroll the
  // panel back to it, so a failed swap says why without the user hunting.
  const why = $('#verdict-why');
  const first = (validation.reasons || [])[0];
  why.textContent = first ? humanise(first) : '';
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

function paintPanel(plan) {
  $('#items').innerHTML = plan.placed.map((i) => `
    <div class="item" data-slot="${i.slot_id}" data-role="${i.role}">
      <div class="item-head">
        <span class="swatch" style="background:#${(COLOR[i.role] ?? COLOR.other).toString(16).padStart(6, '0')}"></span>
        <div class="item-main">
          <div class="slot">${i.slot_id.replace(/_/g, ' ')}</div>
          <div class="name">${esc(i.title)}</div>
          <div class="meta">${i.dims_cm.w}\u00d7${i.dims_cm.d}\u00d7${i.dims_cm.h} cm
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
  $('#issues').innerHTML = reasons.length
    ? reasons.map((r) => `<div class="issue">${esc(humanise(r))}</div>`).join('')
    : `<div class="issue note">Every clearance, door swing, walkway, reach and
        delivery route was checked by <code>app/geometry.py</code>. Nothing here
        was decided by a language model.</div>`;

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
  const m = state.meshes.get(slot);
  if (m) m.material.emissive = new THREE.Color(on ? 0x2b4a7a : 0x000000);
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** Reasons come back naming items by the id they were sent with — an ASIN.
 *  "Only 35cm between B0FR3WVLTS and B0H8PQ9KDJ" is precise and unreadable.
 *  Swap the codes for the slot names a person can actually see on screen. */
function humanise(text) {
  let out = String(text);
  for (const item of state.plan?.placed ?? []) {
    if (!item.asin) continue;
    out = out.replaceAll(item.asin, `the ${item.slot_id.replace(/_/g, ' ')}`);
  }
  return out;
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
const BUNDLE = window.__STATIC_BUNDLE ?? null;
const STATIC = !!BUNDLE;
const planKey = (u, r, s) => `${u}|${r}|${(s ?? []).join(',')}`;

async function api(path, body) {
  if (STATIC) return staticApi(path, body);
  const res = await fetch(path, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : undefined);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

function staticApi(path, body) {
  if (path === '/home/units') return BUNDLE.units;
  if (path.startsWith('/plan/candidates/')) {
    const cat = path.split('/').pop();
    return BUNDLE.candidates[cat] ?? { category: cat, count: 0, items: [] };
  }
  if (path === '/plan/auto') {
    const plan = BUNDLE.plans[planKey(body.unit, body.room, body.style)];
    if (!plan) throw new Error(`This build has no precomputed plan for ${body.room}.`);
    return structuredClone(plan);
  }
  if (path === '/plan/swap') {
    // Look up by full context. A verdict is a property of the arrangement, not
    // of the item on its own.
    const ctx = planKey(body.unit, body.room, state.style);
    const swapped = body.placements.find((p) => BUNDLE.swaps[`${ctx}|${p.slot_id}|${p.asin}`]);
    const hit = swapped && BUNDLE.swaps[`${ctx}|${swapped.slot_id}|${swapped.asin}`];
    if (!hit) {
      return {
        validation: {
          status: 'unverified',
          reasons: ['This exact combination was not precomputed for the static build. '
                  + 'Run the studio locally to have the engine judge it live: '
                  + 'uv run uvicorn app.main:app --port 8000'],
        },
        access: {},
        total_sar: body.placements.reduce((t, p) => t + (p.price_sar || 0), 0),
      };
    }
    return structuredClone(hit);
  }
  throw new Error(`no static data for ${path}`);
}

async function runPlan() {
  $('#loading').hidden = false;
  $('#run').disabled = true;
  try {
    const style = $('#style').value ? $('#style').value.split(',') : [];
    state.style = style;
    const plan = await api('/plan/auto', {
      unit: $('#unit').value, room: $('#room').value,
      budget_sar: Number($('#budget').value), style,
    });
    render(plan);
    setView('iso');
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

async function boot() {
  const home = await api('/home/units');
  const { units } = home;
  state.assumptions = home.assumptions || [];
  const unitSel = $('#unit'), roomSel = $('#room');
  unitSel.innerHTML = units.map((u) => `<option value="${u.id}">${esc(u.label)}</option>`).join('');

  const PLANNABLE = new Set(['living_dining', 'bedroom', 'master_bedroom',
                             'master_bedroom_1', 'master_bedroom_2']);
  const fillRooms = () => {
    const unit = units.find((u) => u.id === unitSel.value);
    const rooms = unit.rooms.filter((r) => PLANNABLE.has(r));
    roomSel.innerHTML = rooms.map((r) =>
      `<option value="${r}">${r.replace(/_/g, ' ')}</option>`).join('');
  };
  unitSel.onchange = () => { fillRooms(); runPlan(); };
  fillRooms();

  $('#run').onclick = runPlan;
  $('#room').onchange = runPlan;
  $('#style').onchange = runPlan;
  $('#budget').onchange = runPlan;
  $('#picker-close').onclick = () => { $('#picker').hidden = true; };
  $('#picker').onclick = (e) => { if (e.target.id === 'picker') $('#picker').hidden = true; };
  document.querySelectorAll('#viewtools button').forEach((b) =>
    b.onclick = () => setView(b.dataset.view));
  addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#picker').hidden = true; });

  resize();
  await runPlan();
}

boot();
