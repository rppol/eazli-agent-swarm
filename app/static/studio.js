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
const state = { plan: null, room: null, style: [], selected: null, meshes: new Map(), ghosts: [] };

// ---------------------------------------------------------------- 3D scene

const host = $('#canvas-host');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0e13);
scene.fog = new THREE.Fog(0x0b0e13, 14, 34);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
host.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI / 2 - 0.02;   // never go under the floor

scene.add(new THREE.HemisphereLight(0xdfe8f5, 0x1a1f26, 1.5));
const key = new THREE.DirectionalLight(0xffffff, 1.5);
key.position.set(6, 11, 5);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -12, right: 12, top: 12, bottom: -12, far: 40 });
scene.add(key);

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
    new THREE.MeshStandardMaterial({ color: 0x2b323c, roughness: 0.95 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  world.add(floor);

  const grid = new THREE.GridHelper(Math.max(W, D), Math.round(Math.max(W, D)), 0x3a434f, 0x232a33);
  grid.position.y = 0.002;
  world.add(grid);

  // Two walls only: enough to read the space, low enough to see into it.
  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x39424e, roughness: 1, transparent: true, opacity: 0.55, side: THREE.DoubleSide,
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

function addGhost(slot, room) {
  // Unfilled slots are drawn as a footprint on the floor. The empty space is a
  // finding, not an absence — hiding it would make a partial plan look whole.
  const size = 0.9;
  const g = new THREE.Mesh(
    new THREE.PlaneGeometry(size, size),
    new THREE.MeshBasicMaterial({ color: 0xf85149, transparent: true, opacity: 0.14, side: THREE.DoubleSide }),
  );
  g.rotation.x = -Math.PI / 2;
  g.position.set((Math.random() - 0.5) * room.width * CM * 0.5, 0.004, (Math.random() - 0.5) * room.depth * CM * 0.5);
  g.userData.ghost = true;
  world.add(g);
  state.ghosts.push(g);
}

// ---------------------------------------------------------------- views

const VIEWS = {
  iso:  (r) => ({ p: [r.width * CM * 0.95, Math.max(r.width, r.depth) * CM * 0.75, r.depth * CM * 0.95], t: [0, 0.4, 0] }),
  top:  (r) => ({ p: [0, Math.max(r.width, r.depth) * CM * 1.25, 0.001], t: [0, 0, 0] }),
  eye:  (r) => ({ p: [0, 1.6, r.depth * CM * 0.62], t: [0, 1.1, -r.depth * CM * 0.2] }),
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
  state.ghosts = [];
  buildRoom(plan.room_cm);
  plan.placed.forEach((i) => addItem(i, plan.room_cm));
  if ($('#showGhosts').checked) plan.unfilled.forEach((s) => addGhost(s, plan.room_cm));
  paintVerdict(plan.validation, plan.total_sar, plan.budget_sar);
  paintPanel(plan);
}

function paintVerdict(validation, total, budget) {
  const v = $('#verdict');
  v.className = `verdict ${validation.status}`;
  v.textContent = validation.status;
  const m = $('#money');
  m.classList.toggle('over', total > budget);
  m.innerHTML = `<b>${total.toLocaleString()}</b> / ${budget.toLocaleString()} SAR`;
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

function paintPanel(plan) {
  $('#items').innerHTML = plan.placed.map((i) => `
    <div class="item" data-slot="${i.slot_id}" data-role="${i.role}" tabindex="0">
      <span class="swatch" style="background:#${(COLOR[i.role] ?? COLOR.other).toString(16).padStart(6, '0')}"></span>
      <div>
        <div class="slot">${i.slot_id.replace(/_/g, ' ')}</div>
        <div class="name">${esc(i.title)}</div>
        <div class="meta">${i.dims_cm.w}×${i.dims_cm.d}×${i.dims_cm.h} cm · ${i.dims_confidence}</div>
        ${accessBadges(i.access)}
      </div>
      <div class="price">${Math.round(i.price_sar).toLocaleString()}</div>
    </div>`).join('')
    + plan.unfilled.map((s) => `
    <div class="item ghost">
      <span class="swatch" style="background:#3a434f"></span>
      <div>
        <div class="slot">${s.slot_id.replace(/_/g, ' ')}</div>
        <div class="name">nothing fits</div>
        <div class="why">${esc(s.reason)}</div>
      </div>
      <div class="price">—</div>
    </div>`).join('');

  const reasons = plan.validation.reasons || [];
  $('#issues').innerHTML = reasons.length
    ? reasons.map((r) => `<div class="issue">${esc(r)}</div>`).join('')
    : `<div class="issue note">Every clearance, door swing, walkway and delivery
        route checked by <code>app/geometry.py</code>. Click any item to swap it.</div>`;

  document.querySelectorAll('.item[data-slot]').forEach((el) => {
    el.onclick = () => openPicker(el.dataset.slot, el.dataset.role);
    el.onkeydown = (e) => { if (e.key === 'Enter') el.click(); };
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

// ---------------------------------------------------------------- swapping

async function openPicker(slotId, role) {
  const category = CATEGORY_FOR_ROLE[role];
  if (!category) return;
  $('#picker-title').textContent = `Swap ${slotId.replace(/_/g, ' ')}`;
  $('#picker-list').innerHTML = '<div class="cand"><div class="t">loading…</div><div class="p"></div></div>';
  $('#picker').hidden = false;

  const { items } = await api(`/plan/candidates/${category}`);
  const current = state.plan.placed.find((p) => p.slot_id === slotId);

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
const key = (u, r, s) => `${u}|${r}|${(s ?? []).join(',')}`;

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
    const plan = BUNDLE.plans[key(body.unit, body.room, body.style)];
    if (!plan) throw new Error(`This build has no precomputed plan for ${body.room}.`);
    return structuredClone(plan);
  }
  if (path === '/plan/swap') {
    // Look up by full context. A verdict is a property of the arrangement, not
    // of the item on its own.
    const ctx = key(body.unit, body.room, state.style);
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
    $('#issues').innerHTML = `<div class="issue">${esc(err.message)} — is the
      service running? <code>uv run uvicorn app.main:app --port 8000</code></div>`;
  } finally {
    $('#loading').hidden = true;
    $('#run').disabled = false;
  }
}

async function boot() {
  const { units } = await api('/home/units');
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
  $('#showGhosts').onchange = () => state.plan && render(state.plan);
  document.querySelectorAll('#viewtools button').forEach((b) =>
    b.onclick = () => setView(b.dataset.view));
  addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#picker').hidden = true; });

  resize();
  await runPlan();
}

boot();
