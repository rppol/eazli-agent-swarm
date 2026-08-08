/* The 3D viewport. Imported dynamically, so nothing here blocks first paint.
 *
 * three.js is ~490 KB minified even after tree-shaking — 97% of the page's
 * JavaScript. The plan, the reasoning and the verdict are text, and they are
 * most of what this page is for, so they render immediately and the room
 * arrives a moment later.
 *
 * There is no geometry in this module beyond turning centimetres into metres.
 * Every position it draws was computed and validated by app/geometry.py.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { hex } from './palette.js';

const CM = 0.01;

let scene, camera, renderer, controls, world, host;
let room = null;
const meshes = new Map();

export const colourFor = hex;

export function init(element) {
  host = element;
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0e13);
  scene.fog = new THREE.Fog(0x0b0e13, 22, 60);

  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  host.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
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

  world = new THREE.Group();
  scene.add(world);

  new ResizeObserver(resize).observe(host);
  addEventListener('resize', resize);
  resize();

  (function loop() {
    requestAnimationFrame(loop);
    controls.update();
    renderer.render(scene, camera);
  })();

  // Exposed so the scene can be inspected without a visible window: a hidden
  // tab never fires requestAnimationFrame, so "nothing drawn" and "nothing
  // there" look identical from outside.
  window.__studio = { scene, camera, renderer, world, meshes };
  return true;
}

function resize() {
  const { clientWidth: w, clientHeight: h } = host;
  if (!w || !h) return;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  // updateStyle must stay on: passing `false` left the canvas at its intrinsic
  // 300x150 CSS size while the drawing buffer was correct.
  renderer.setSize(w, h);
}

function clear(group) {
  while (group.children.length) {
    const c = group.children.pop();
    c.traverse?.((o) => { o.geometry?.dispose(); o.material?.dispose?.(); });
  }
}

/** Engine coords (x east, y south, origin NW) -> three.js (x east, z south, centred). */
function toWorld(x, y, w, d, r) {
  return { x: (x + w / 2 - r.width / 2) * CM, z: (y + d / 2 - r.depth / 2) * CM };
}

export function draw(plan) {
  room = plan.room_cm;
  clear(world);
  meshes.clear();

  const W = room.width * CM, D = room.depth * CM, H = room.height * CM;

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(W, D),
    new THREE.MeshStandardMaterial({ color: 0x5c6672, roughness: 0.92 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  world.add(floor);

  // A GridHelper is always square, so on a 3.35 x 5.51m room it hung a metre
  // off both long sides and read as a rendering fault. Metre lines inside the
  // actual floor rectangle instead.
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
    gridGeo, new THREE.LineBasicMaterial({ color: 0x9aa4b0, transparent: true, opacity: 0.35 }));
  grid.position.y = 0.004;
  world.add(grid);

  // The entrance, so the room has an orientation and "needs tipping at the
  // flat entrance" has something to point at. Width comes from the server.
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

  // Unfilled slots are deliberately NOT drawn. The planner never computes a
  // position for something it could not place, so a marker on the floor would
  // assert a location that does not exist.
  for (const item of plan.placed) {
    const { w, d, h } = item.dims_cm;
    const group = furniture(item.role, w * CM, d * CM, h * CM, colourFor(item.role));
    const { x, z } = toWorld(item.x, item.y, w, d, room);
    group.position.set(x, 0, z);
    group.userData.slot = item.slot_id;
    world.add(group);
    meshes.set(item.slot_id, group);
  }
}

// --------------------------------------------------------------- furniture
//
// Every piece is built to occupy EXACTLY the w x d x h the engine validated —
// the detail is carved inside that envelope, never outside it. A sofa that
// looked like a sofa but overhung its verified footprint by a few centimetres
// would be a drawing that disagrees with the verdict, which is the one thing
// this project cannot ship.

function slab(w, d, h, mat) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.castShadow = m.receiveShadow = true;
  return m;
}

function at(mesh, x, y, z) { mesh.position.set(x, y, z); return mesh; }

function furniture(role, w, d, h, colour) {
  const g = new THREE.Group();
  const body = new THREE.MeshStandardMaterial({ color: colour, roughness: 0.72, metalness: 0.02 });
  const soft = new THREE.MeshStandardMaterial({
    color: new THREE.Color(colour).offsetHSL(0, -0.04, 0.07), roughness: 0.85 });
  const dark = new THREE.MeshStandardMaterial({
    color: new THREE.Color(colour).offsetHSL(0, 0.02, -0.16), roughness: 0.6 });
  const metal = new THREE.MeshStandardMaterial({ color: 0x2f3742, roughness: 0.45, metalness: 0.5 });

  const legs = (inset, legH, thick, mat) => {
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      g.add(at(slab(thick, thick, legH, mat),
        sx * (w / 2 - inset), legH / 2, sz * (d / 2 - inset)));
    }
  };

  if (role === 'sofa' || role === 'armchair') {
    const armW = Math.min(w * 0.11, 0.16);
    const backD = d * 0.22;
    const seatH = h * 0.42;
    g.add(at(slab(w, d, seatH * 0.6, body), 0, seatH * 0.3 + h * 0.06, 0));         // plinth
    g.add(at(slab(w, backD, h - h * 0.06, body), 0, (h + h * 0.06) / 2, -(d - backD) / 2)); // back
    for (const s of [-1, 1]) {                                                       // arms
      g.add(at(slab(armW, d - backD, h * 0.62, body),
        s * (w - armW) / 2, h * 0.34, backD / 2));
    }
    const seats = Math.max(1, Math.round(w / 0.7));
    const cw = (w - armW * 2) / seats;
    for (let i = 0; i < seats; i++) {                                                // cushions
      g.add(at(slab(cw * 0.92, (d - backD) * 0.88, h * 0.14, soft),
        -w / 2 + armW + cw * (i + 0.5), seatH + h * 0.09, backD / 2));
    }
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {                            // feet
      g.add(at(slab(0.05, 0.05, h * 0.06, metal),
        sx * (w / 2 - 0.06), h * 0.03, sz * (d / 2 - 0.06)));
    }
  } else if (role === 'coffee_table' || role === 'dining_table' || role === 'table') {
    const topH = Math.min(h * 0.12, 0.05);
    g.add(at(slab(w, d, topH, body), 0, h - topH / 2, 0));                           // top
    legs(0.06, h - topH, Math.min(w, d) * 0.07, dark);
    if (role === 'dining_table') {                                                   // apron
      g.add(at(slab(w * 0.92, d * 0.92, h * 0.06, dark), 0, h - topH - h * 0.05, 0));
    }
  } else if (role === 'tv_console') {
    g.add(at(slab(w, d, h * 0.86, body), 0, h * 0.43 + h * 0.14, 0));                // carcass
    g.add(at(slab(w * 0.98, d * 0.02, h * 0.02, dark), 0, h * 0.62, d / 2));         // door line
    for (const s of [-1, 1]) {
      g.add(at(slab(w * 0.14, d * 0.03, h * 0.03, metal), s * w * 0.22, h * 0.62, d / 2)); // handles
    }
    legs(0.05, h * 0.14, 0.04, metal);
  } else if (role === 'floor_lamp') {
    const shadeH = h * 0.3, poleH = h - shadeH;
    g.add(at(new THREE.Mesh(new THREE.CylinderGeometry(Math.min(w, d) * 0.42, Math.min(w, d) * 0.46, h * 0.02, 24), dark),
      0, h * 0.01, 0));                                                              // base
    g.add(at(new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, poleH, 12), metal),
      0, poleH / 2, 0));                                                             // pole
    const shade = new THREE.Mesh(
      new THREE.CylinderGeometry(Math.min(w, d) * 0.44, Math.min(w, d) * 0.62, shadeH, 28, 1, true),
      new THREE.MeshStandardMaterial({ color: colour, roughness: 0.9, side: THREE.DoubleSide,
                                       emissive: 0xffe6a8, emissiveIntensity: 0.5 }));
    g.add(at(shade, 0, poleH + shadeH / 2, 0));
  } else if (role === 'bed') {
    g.add(at(slab(w, d, h * 0.34, dark), 0, h * 0.17, 0));                           // base
    g.add(at(slab(w * 0.97, d * 0.94, h * 0.3, soft), 0, h * 0.49, d * 0.015));       // mattress
    g.add(at(slab(w, d * 0.06, h, body), 0, h / 2, -(d - d * 0.06) / 2));            // headboard
    for (const s of [-1, 1]) {                                                        // pillows
      g.add(at(slab(w * 0.42, d * 0.16, h * 0.1, soft),
        s * w * 0.24, h * 0.69, -d * 0.34));
    }
  } else if (role === 'wardrobe' || role === 'bookshelf') {
    g.add(at(slab(w, d, h, body), 0, h / 2, 0));
    g.add(at(slab(w * 0.012, d * 0.02, h * 0.94, dark), 0, h / 2, d / 2));           // door split
    for (const s of [-1, 1]) {
      g.add(at(slab(0.02, d * 0.04, h * 0.1, metal), s * w * 0.06, h * 0.52, d / 2)); // handles
    }
  } else {
    g.add(at(slab(w, d, h, body), 0, h / 2, 0));
  }

  g.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  return g;
}

/** Distance at which a room of this size fills the frame, given the current
 *  field of view and aspect. Fixed multipliers cropped furniture off the edge
 *  as soon as the room was not the shape they were tuned for. */
function fitDistance(r, margin = 1.25) {
  const W = r.width * CM, D = r.depth * CM, H = r.height * CM;
  const radius = Math.hypot(W, D, H) / 2;
  const vFov = (camera.fov * Math.PI) / 180;
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
  return (radius / Math.sin(Math.min(vFov, hFov) / 2)) * margin;
}

/** Place the camera along a unit direction at exactly the fitting distance.
 *  Scaling a non-unit vector by that distance put it ~20% too close. */
function along(dir, distance) {
  const len = Math.hypot(...dir);
  return dir.map((c) => (c / len) * distance);
}

const VIEWS = {
  iso: (r) => ({ p: along([0.85, 0.78, 0.85], fitDistance(r)), t: [0, 0.35, 0] }),
  top: (r) => ({ p: [0, fitDistance(r, 1.1), 0.001], t: [0, 0, 0] }),
  eye: (r) => ({ p: [0, 1.55, r.depth * CM * 0.52], t: [0, 1.15, -r.depth * CM * 0.3] }),
};

export function setView(name) {
  if (!room) return;
  const { p, t } = VIEWS[name](room);
  camera.position.set(...p);
  controls.target.set(...t);
  controls.update();
}

export function highlight(slot, on) {
  const g = meshes.get(slot);
  if (!g) return;
  g.traverse((o) => {
    if (!o.isMesh || !o.material?.emissive) return;
    o.userData.baseEmissive ??= o.material.emissive.clone();
    o.material.emissive = on ? new THREE.Color(0x2f5590) : o.userData.baseEmissive;
  });
}
