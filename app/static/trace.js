/* eazli agent trace — a viewer for the `eazli.agent-trace/v1` shape.
 *
 * The rule this file is written under: it knows the *schema* and nothing about
 * any particular run. No span id, no agent name, no tool name, no number from
 * a recording appears below — `tests/test_trace.py` asserts that, because the
 * whole claim of the schema is that a live coordinator emitting the same
 * shape can be swapped in tomorrow without this file changing. A second run
 * file in docs/agent-runs/ renders through exactly this code.
 *
 * Two consequences worth stating plainly:
 *
 *  - Nothing is invented. Where a field is absent the row says so — a span
 *    with no `duration_ms` gets a dashed envelope, not a guessed bar, and the
 *    `not_yet_run` block is drawn as unrun rather than quietly omitted.
 *  - The recordings carry `duration_ms` but no wall-clock timestamps, so the
 *    horizontal axis is *derived*: siblings are laid end to end in file order.
 *    That is stated on the page rather than left to be assumed. If a future
 *    run file carries `start_ms`, it is used instead.
 */

const DATA = './data/agent-runs/';

/* Deterministic, order-based, and deliberately not derived from the agent's
   name: a hash would mean renaming an agent recolours it for no reason. */
const AGENT_COLOURS = ['#484ef4', '#7a3ff0', '#0e7038', '#8a5a00', '#2b7f9e', '#c1121f', '#5a5f8c'];

// ---------------------------------------------------------------- utilities

function el(tag, props, kids) {
  const n = document.createElement(tag);
  for (const k in (props || {})) {
    if (k === 'class') n.className = props[k];
    else if (k === 'text') n.textContent = props[k];
    else if (k === 'html') n.innerHTML = props[k];
    else if (props[k] !== null && props[k] !== undefined) n.setAttribute(k, props[k]);
  }
  for (const c of (kids || [])) if (c) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  return n;
}

function fmtMs(ms) {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return Math.round(ms) + ' ms';
  const s = ms / 1000;
  if (s < 60) return s.toFixed(1) + ' s';
  const m = Math.floor(s / 60);
  return m + 'm ' + Math.round(s - m * 60) + 's';
}

function fmtTokens(n) {
  if (n === null || n === undefined) return '—';
  if (n < 1000) return String(n);
  return (n / 1000).toFixed(1) + 'k';
}

const fmtInt = (n) => (n === null || n === undefined ? '—' : n.toLocaleString('en-US'));

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/* The transport line states how tool calls resolved, e.g. "... resolved
   through some__namespace__* against ...". Pulling the prefix out of that
   sentence is how the tool table can show the wire name without this file
   knowing which server was on the other end. */
function toolNamespace(transport) {
  const m = /([a-z0-9_]+__[a-z0-9-]+__)\*/i.exec(transport || '');
  return m ? m[1] : '';
}

// ------------------------------------------------------------ value rendering

/* Renders whatever `inputs` / `outputs` happen to contain. The schema fixes
   the span envelope, not the payload, so this has to cope with any JSON. */
function renderValue(v) {
  if (v === null || v === undefined) return el('span', { class: 'scalar mono', text: 'null' });
  if (Array.isArray(v)) {
    if (!v.length) return el('span', { class: 'scalar mono', text: '[]' });
    return el('ul', { class: 'vlist' }, v.map((x) => el('li', {}, [renderValue(x)])));
  }
  if (isObj(v)) {
    const dl = el('dl', { class: 'kv' });
    for (const k of Object.keys(v)) {
      dl.appendChild(el('dt', { text: k }));
      dl.appendChild(el('dd', {}, [renderValue(v[k])]));
    }
    return dl;
  }
  if (typeof v === 'string') return el('span', { class: 'scalar', text: v });
  return el('span', { class: 'scalar mono', text: String(v) });
}

// ------------------------------------------------------------------- layout

/* Build the parent/child tree and give every span a start offset.

   `duration_ms` is what the recordings carry. A span without one (a pure
   dispatch chain, say) gets no bar of its own — it gets the envelope of its
   children, drawn dashed, so the row still shows the span's extent without
   claiming a measurement that was never taken. */
function buildTree(spans) {
  const byId = new Map(spans.map((s) => [s.id, { span: s, kids: [] }]));
  const roots = [];
  for (const s of spans) {
    const node = byId.get(s.id);
    const parent = s.parent_id ? byId.get(s.parent_id) : null;
    if (parent) parent.kids.push(node); else roots.push(node);
  }
  return { byId, roots };
}

function assign(node, cursor, depth, out) {
  node.depth = depth;
  node.start = (typeof node.span.start_ms === 'number') ? node.span.start_ms : cursor;
  let c = node.start;
  for (const k of node.kids) c = assign(k, c, depth + 1, out);
  const own = node.span.duration_ms;
  node.measured = typeof own === 'number';
  node.dur = node.measured ? own : (c - node.start);
  node.end = node.start + node.dur;
  out.push(node);
  // A recorded duration is the truth about that span, but a child can still
  // outlast its parent's own measurement — an agent that dispatches and
  // returns before its worker finishes. The cursor a sibling starts from has
  // to clear both, or the next bar is drawn overlapping the previous subtree.
  return Math.max(node.end, c);
}

function flatten(roots) {
  const out = [];
  let cursor = 0;
  for (const r of roots) cursor = assign(r, cursor, 0, out);
  // Depth-first, in file order — the reading order of a waterfall.
  const ordered = [];
  const walk = (n) => { ordered.push(n); n.kids.forEach(walk); };
  roots.forEach(walk);
  // The axis has to span the furthest point any span reaches, not just the
  // last root's end, or a deep child's bar runs off the end of the track.
  const total = out.reduce((a, n) => Math.max(a, n.end), 0);
  return { ordered, total: Math.max(total, cursor) };
}

// ------------------------------------------------------------------ topology

/* The orchestration diagram. Solid arrows are dispatches from the bus. Dashed
   arrows are `input_from` edges — one agent's literal output becoming the
   next one's input, which in this pattern travels *through* the bus rather
   than agent to agent. The caption under the diagram says why. */
function topology(run, tree, colourOf) {
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (t, a, kids) => {
    const n = document.createElementNS(NS, t);
    for (const k in a) if (a[k] !== null && a[k] !== undefined) n.setAttribute(k, a[k]);
    for (const c of (kids || [])) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    return n;
  };

  const roots = tree.roots;
  const workers = [];
  const walk = (n) => { if (n.depth > 0) workers.push({ name: n.span.agent, id: n.span.id, unrun: false }); n.kids.forEach(walk); };
  roots.forEach(walk);
  for (const name of Object.keys(run.not_yet_run || {})) workers.push({ name, id: null, unrun: true });

  const PITCH = 46, TOP = 22, BOXW = 210, BOXH = 30, BUSX = 14, BUSW = 150, GAP = 300;
  const H = Math.max(TOP * 2 + workers.length * PITCH, 130);
  const W = GAP + BOXW + 110;
  const svg = mk('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img' });
  svg.appendChild(mk('defs', {}, [
    mk('marker', { id: 'ah', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto' },
      [mk('path', { d: 'M0,0 L8,4 L0,8 z', fill: '#5a5f8c' })]),
    mk('marker', { id: 'ah2', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto' },
      [mk('path', { d: 'M0,0 L8,4 L0,8 z', fill: '#484ef4' })]),
  ]));

  const busY = H / 2;
  const rowY = (i) => TOP + i * PITCH + BOXH / 2;
  const posOf = new Map();
  workers.forEach((w, i) => { if (w.id) posOf.set(w.id, rowY(i)); });

  // dispatch edges
  workers.forEach((w, i) => {
    const y = rowY(i);
    svg.appendChild(mk('path', {
      d: `M${BUSX + BUSW},${busY} C${BUSX + BUSW + 60},${busY} ${GAP - 60},${y} ${GAP - 6},${y}`,
      fill: 'none', stroke: w.unrun ? '#d9dbf7' : '#5a5f8c',
      'stroke-width': 1.2, 'stroke-dasharray': w.unrun ? '3 3' : null,
      'marker-end': w.unrun ? null : 'url(#ah)',
    }));
  });

  // A2A edges, bowed out to the right so they read as a separate class of arrow
  const a2a = [];
  const collect = (n) => { if (n.span.input_from) a2a.push(n.span); n.kids.forEach(collect); };
  roots.forEach(collect);
  a2a.forEach((s, k) => {
    const y1 = posOf.get(s.input_from), y2 = posOf.get(s.id);
    if (y1 === undefined || y2 === undefined) return;
    const x = GAP + BOXW + 22 + k * 14;
    svg.appendChild(mk('path', {
      d: `M${GAP + BOXW},${y1} C${x + 26},${y1} ${x + 26},${y2} ${GAP + BOXW + 2},${y2}`,
      fill: 'none', stroke: '#484ef4', 'stroke-width': 1.4, 'stroke-dasharray': '4 3',
      'marker-end': 'url(#ah2)',
    }));
    svg.appendChild(mk('text', {
      x: x + 16, y: (y1 + y2) / 2, fill: '#484ef4', 'font-size': 9,
      'letter-spacing': '.06em', 'text-anchor': 'middle',
    }, ['A2A']));
  });

  // the bus
  const rootNames = roots.map((r) => r.span.agent).join(' / ');
  svg.appendChild(mk('rect', {
    x: BUSX, y: busY - 21, width: BUSW, height: 42, rx: 8,
    fill: '#12164c', stroke: '#12164c',
  }));
  svg.appendChild(mk('text', { x: BUSX + BUSW / 2, y: busY - 4, 'text-anchor': 'middle', fill: '#ffffff', 'font-size': 11.5, 'font-family': 'ui-monospace, monospace' }, [rootNames]));
  svg.appendChild(mk('text', { x: BUSX + BUSW / 2, y: busY + 11, 'text-anchor': 'middle', fill: '#b5b8fe', 'font-size': 9.5, 'letter-spacing': '.07em' }, ['THE BUS']));

  // the workers
  workers.forEach((w, i) => {
    const y = rowY(i);
    svg.appendChild(mk('rect', {
      x: GAP, y: y - BOXH / 2, width: BOXW, height: BOXH, rx: 7,
      fill: w.unrun ? '#f1f2fd' : '#ffffff',
      stroke: w.unrun ? '#d9dbf7' : colourOf(w.name), 'stroke-width': w.unrun ? 1 : 1.4,
      'stroke-dasharray': w.unrun ? '4 3' : null,
    }));
    svg.appendChild(mk('text', {
      x: GAP + 12, y: y + 4, fill: w.unrun ? '#5a5f8c' : '#12164c',
      'font-size': 11.5, 'font-family': 'ui-monospace, monospace',
    }, [w.name]));
    if (w.unrun) {
      svg.appendChild(mk('text', {
        x: GAP + BOXW - 10, y: y + 4, 'text-anchor': 'end', fill: '#5a5f8c', 'font-size': 9, 'letter-spacing': '.06em',
      }, ['NOT YET RUN']));
    }
  });

  return svg;
}

// ----------------------------------------------------------------- rendering

function statStrip(run, nodes, total) {
  const spans = run.spans || [];
  const tokens = spans.reduce((a, s) => a + (s.tokens || 0), 0);
  const tools = spans.reduce((a, s) => a + ((s.tool_calls || []).length), 0);
  const steps = spans.reduce((a, s) => a + ((s.react_trace || []).length), 0);
  const revised = spans.reduce((a, s) => a + (s.react_trace || []).filter((r) => r.revised).length, 0);
  const handoffs = spans.filter((s) => s.input_from).length;
  const unrun = Object.keys(run.not_yet_run || {}).length;

  const cells = [
    [String(spans.length), 'spans'],
    [fmtMs(total), 'wall time'],
    [fmtInt(tokens), 'tokens'],
    [String(tools), 'MCP tool calls'],
    [String(steps), 'ReAct steps'],
    [String(revised), 'self-revisions', revised > 0],
    [String(handoffs), 'A2A handoffs'],
    [String(unrun), 'not yet run'],
  ];
  return el('div', { class: 'stats' }, cells.map(([b, s, flag]) =>
    el('div', { class: 'stat' + (flag ? ' flag' : '') }, [el('b', { text: b }), el('span', { text: s })])));
}

function toolTable(calls, ns) {
  const rows = calls.map((c) => el('tr', {}, [
    el('td', {}, [
      ns ? el('span', { class: 'tool-ns', text: ns }) : null,
      el('span', { class: 'tool-nm', text: c.tool }),
      c.args ? el('div', { class: 'args', text: JSON.stringify(c.args) }) : null,
    ]),
    el('td', {}, [renderValue(c.result)]),
  ]));
  return el('table', { class: 'tools' }, [
    el('thead', {}, [el('tr', {}, [el('th', { text: 'tool call' }), el('th', { text: 'result' })])]),
    el('tbody', {}, rows),
  ]);
}

function reactList(steps) {
  const li = steps.map((s) => {
    const head = el('div', { class: 'step-head' }, [
      s.step !== undefined ? el('span', { class: 'step-no', text: 'step ' + s.step }) : null,
      s.revised ? el('span', { class: 'revised-flag', text: 'revised — corrected itself' }) : null,
    ]);
    const dl = el('dl', { class: 'react-k' });
    const put = (label, val, cls) => {
      if (val === undefined || val === null) return;
      dl.appendChild(el('dt', { text: label }));
      dl.appendChild(el('dd', { class: cls || '', text: typeof val === 'string' ? val : JSON.stringify(val) }));
    };
    put('thought', s.thought);
    put('action', s.action, 'action');
    put('observation', s.observation, 'obs');
    put('reflection', s.reflection);
    // Anything a future run adds to a step still shows up, unstyled but present.
    for (const k of Object.keys(s)) {
      if (['step', 'revised', 'thought', 'action', 'observation', 'reflection'].includes(k)) continue;
      put(k, s[k]);
    }
    return el('li', { class: s.revised ? 'revised' : '' }, [head, dl]);
  });
  return el('ol', { class: 'react' }, li);
}

function detailFor(node, byId, ns) {
  const s = node.span;
  const d = el('div', { class: 'detail', hidden: '' });

  if (s.summary) {
    d.appendChild(el('div', { class: 'callout' }, [
      el('span', { class: 'k', text: 'summary' }), el('p', { text: s.summary }),
    ]));
  }

  if (s.input_from) {
    const src = byId.get(s.input_from);
    const h = el('div', { class: 'handoff' }, [
      el('div', { class: 'arrow', text: s.input_from + '  →  ' + s.id }),
      s.input_note ? el('div', { class: 'note', text: s.input_note }) : null,
    ]);
    if (src) {
      const pay = el('div', { class: 'payload' }, [
        el('div', { class: 'note', text: 'what ' + src.span.agent + ' returned, and what this span received:' }),
      ]);
      pay.appendChild(renderValue(src.span.outputs));
      h.appendChild(pay);
    } else {
      h.appendChild(el('div', { class: 'note', text: 'the source span is not in this file' }));
    }
    d.appendChild(el('h3', { text: 'A2A handoff' }));
    d.appendChild(h);
  }

  if (s.inputs !== undefined) {
    d.appendChild(el('h3', { text: 'inputs' }));
    d.appendChild(renderValue(s.inputs));
  }

  const calls = s.tool_calls || [];
  if (calls.length) {
    d.appendChild(el('h3', { text: 'tool calls — ' + calls.length + ' over ' + (ns ? 'MCP' : 'the recorded transport') }));
    d.appendChild(toolTable(calls, ns));
  }

  const steps = s.react_trace || [];
  if (steps.length) {
    const rev = steps.filter((x) => x.revised).length;
    d.appendChild(el('h3', {
      text: 'ReAct trace — ' + steps.length + ' step' + (steps.length === 1 ? '' : 's') +
            ' recorded' + (rev ? ', ' + rev + ' of them a revision' : ''),
    }));
    d.appendChild(reactList(steps));
  }

  if (s.outputs !== undefined) {
    d.appendChild(el('h3', { text: 'outputs' }));
    d.appendChild(renderValue(s.outputs));
  }

  if (s.honesty_note) {
    d.appendChild(el('h3', { text: 'honesty note' }));
    d.appendChild(el('div', { class: 'callout honest' }, [el('p', { text: s.honesty_note })]));
  }

  // Any span field the schema grows later, so a new key is visible rather than
  // silently dropped on the floor.
  const KNOWN = ['id', 'parent_id', 'agent', 'role', 'kind', 'summary', 'duration_ms',
    'tokens', 'input_from', 'input_note', 'tool_calls', 'react_trace', 'inputs',
    'outputs', 'honesty_note', 'start_ms'];
  const extra = Object.keys(s).filter((k) => !KNOWN.includes(k));
  if (extra.length) {
    d.appendChild(el('h3', { text: 'other recorded fields' }));
    const dl = el('dl', { class: 'kv' });
    for (const k of extra) { dl.appendChild(el('dt', { text: k })); dl.appendChild(el('dd', {}, [renderValue(s[k])])); }
    d.appendChild(dl);
  }
  return d;
}

function waterfall(run, tree, ordered, total, colourOf, ns) {
  const wf = el('div', { class: 'wf' });
  wf.appendChild(el('div', { class: 'wf-head' }, [
    el('div', { text: 'span' }),
    el('div', { text: total > 0 ? 'timeline — ' + fmtMs(total) + ' end to end' : 'timeline' }),
    el('div', { class: 'r', text: 'duration' }),
    el('div', { class: 'r', text: 'tokens' }),
  ]));
  const rows = el('div', { id: 'rows' });
  const edges = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  edges.setAttribute('id', 'edges');
  rows.appendChild(edges);

  const barOf = new Map();
  const rowOf = new Map();

  for (const n of ordered) {
    const s = n.span;
    const steps = s.react_trace || [];
    const rev = steps.filter((x) => x.revised).length;
    const badges = el('div', { class: 'badges' }, [
      s.kind ? el('span', { class: 'badge', text: s.kind }) : null,
      (s.tool_calls || []).length ? el('span', { class: 'badge', text: (s.tool_calls || []).length + ' tools' }) : null,
      steps.length ? el('span', { class: 'badge', text: steps.length + ' react' }) : null,
      rev ? el('span', { class: 'badge rev', text: rev + ' revised' }) : null,
      s.input_from ? el('span', { class: 'badge a2a', text: 'a2a in' }) : null,
    ]);

    const who = el('div', { class: 'who', style: 'padding-left:' + (n.depth * 16) + 'px' }, [
      el('span', { class: 'caret', text: '▶' }),
      el('span', { class: 'dot', style: 'background:' + colourOf(s.agent) }),
      el('span', { class: 'nm', text: s.agent }),
      s.role ? el('span', { class: 'rl', text: s.role }) : null,
    ]);

    const pct = (v) => (total > 0 ? (v / total) * 100 : 0);
    const bar = el('div', {
      class: 'bar' + (n.measured ? '' : (n.dur > 0 ? ' envelope' : ' none')),
      style: 'left:' + pct(n.start).toFixed(3) + '%;width:' + Math.max(pct(n.dur), 0.4).toFixed(3) + '%;' +
             (n.measured ? 'background:' + colourOf(s.agent) + ';' : ''),
      title: n.measured ? fmtMs(n.dur) : 'no duration recorded for this span — shown as the envelope of its children',
    });
    const track = el('div', { class: 'track' }, [bar]);

    const row = el('div', {
      class: 'row', role: 'button', tabindex: '0', 'aria-expanded': 'false',
      'data-span': s.id,
    }, [
      el('div', {}, [who, badges]),
      track,
      el('div', { class: 'num' + (n.measured ? '' : ' dim'), text: n.measured ? fmtMs(n.dur) : '—' }),
      el('div', { class: 'num' + (s.tokens ? '' : ' dim'), text: fmtTokens(s.tokens) }),
    ]);
    const detail = detailFor(n, tree.byId, ns);
    const toggle = () => {
      const open = row.getAttribute('aria-expanded') === 'true';
      row.setAttribute('aria-expanded', open ? 'false' : 'true');
      detail.hidden = open;
      drawEdges();
    };
    row.addEventListener('click', toggle);
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    rows.appendChild(row);
    rows.appendChild(detail);
    barOf.set(s.id, bar);
    rowOf.set(s.id, row);
  }

  // `not_yet_run` gets rows too. Greyed, labelled, and hatched where a bar
  // would be — an agent that did not run must not be able to look like one
  // that ran quickly.
  const unrun = run.not_yet_run || {};
  for (const name of Object.keys(unrun)) {
    rows.appendChild(el('div', { class: 'row unrun' }, [
      el('div', {}, [
        el('div', { class: 'who', style: 'padding-left:16px' }, [
          el('span', { class: 'caret' }),
          el('span', { class: 'dot' }),
          el('span', { class: 'nm', text: name }),
          el('span', { class: 'rl', text: unrun[name] }),
        ]),
        el('div', { class: 'badges' }, [el('span', { class: 'badge unrun', text: 'not yet run' })]),
      ]),
      el('div', { class: 'track' }, [el('span', { class: 'no-bar', text: 'no run — nothing to plot' })]),
      el('div', { class: 'num dim', text: '—' }),
      el('div', { class: 'num dim', text: '—' }),
    ]));
  }

  wf.appendChild(rows);

  /* The A2A edge, drawn over the waterfall: from the end of the source span's
     bar to the start of the target's. This is the picture the whole schema
     exists to make — one agent's output literally becoming the next's input. */
  function drawEdges() {
    while (edges.firstChild) edges.removeChild(edges.firstChild);
    const base = rows.getBoundingClientRect();
    edges.setAttribute('width', base.width);
    edges.setAttribute('height', rows.scrollHeight);
    edges.setAttribute('viewBox', `0 0 ${base.width} ${rows.scrollHeight}`);
    const NS = 'http://www.w3.org/2000/svg';
    const defs = document.createElementNS(NS, 'defs');
    const marker = document.createElementNS(NS, 'marker');
    for (const [k, v] of [['id', 'wfa'], ['viewBox', '0 0 8 8'], ['refX', 7], ['refY', 4],
      ['markerWidth', 6], ['markerHeight', 6], ['orient', 'auto']]) marker.setAttribute(k, v);
    const head = document.createElementNS(NS, 'path');
    head.setAttribute('d', 'M0,0 L8,4 L0,8 z');
    head.setAttribute('fill', '#484ef4');
    marker.appendChild(head);
    defs.appendChild(marker);
    edges.appendChild(defs);

    for (const n of ordered) {
      const from = n.span.input_from;
      if (!from) continue;
      const a = barOf.get(from), b = barOf.get(n.span.id);
      if (!a || !b) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const x1 = ra.right - base.left, y1 = ra.top - base.top + ra.height / 2;
      const x2 = rb.left - base.left, y2 = rb.top - base.top + rb.height / 2;
      // Consecutive spans sit end to end, so x1 and x2 are usually within a
      // pixel or two of each other and a flat bezier would collapse to
      // nothing. Bowing the control points out by a fixed amount keeps the
      // hook visible however tightly the two bars abut.
      const dip = Math.max(26, Math.abs(x2 - x1) / 2);
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', `M${x1},${y1} C${x1 + dip},${y1} ${x2 - dip},${y2} ${x2 - 3},${y2}`);
      p.setAttribute('fill', 'none');
      p.setAttribute('stroke', '#484ef4');
      p.setAttribute('stroke-width', '1.6');
      p.setAttribute('stroke-dasharray', '5 3');
      p.setAttribute('marker-end', 'url(#wfa)');
      edges.appendChild(p);
      const o = document.createElementNS(NS, 'circle');
      o.setAttribute('cx', x1); o.setAttribute('cy', y1); o.setAttribute('r', '3');
      o.setAttribute('fill', '#484ef4');
      edges.appendChild(o);
      const t = document.createElementNS(NS, 'text');
      t.setAttribute('x', Math.max(x1, x2) + dip + 4);
      t.setAttribute('y', (y1 + y2) / 2 + 3);
      t.setAttribute('fill', '#484ef4');
      t.setAttribute('font-size', '9.5');
      t.setAttribute('letter-spacing', '.06em');
      t.textContent = 'A2A — input_from';
      edges.appendChild(t);
    }
  }
  requestAnimationFrame(drawEdges);
  window.addEventListener('resize', drawEdges);
  return wf;
}

function header(run, ns) {
  const box = el('div', { class: 'card' });

  if (run.provenance_kind && run.provenance_kind !== 'recorded') {
    box.appendChild(el('div', { class: 'synthetic-banner' }, [
      el('b', { text: 'Not a recording. ' }),
      'This file declares provenance_kind: "' + run.provenance_kind + '". It exists so the ' +
      'viewer can be shown to be data-driven, and nothing in it is evidence of anything an agent did.',
    ]));
  }

  const meta = el('div', { class: 'meta' });
  const put = (k, v) => { if (v) meta.appendChild(el('div', {}, [el('dt', { text: k }), el('dd', { text: v })])); };
  put('schema', run.schema);
  put('run id', run.run_id);
  put('recorded', run.recorded);
  if (ns) put('tool namespace', ns + '*');
  box.appendChild(meta);

  const callout = (k, text, cls) => {
    if (!text) return;
    box.appendChild(el('div', { class: 'callout' + (cls ? ' ' + cls : '') }, [
      el('span', { class: 'k', text: k }), el('p', { text }),
    ]));
  };
  callout('provenance', run.provenance);
  callout('orchestration pattern', run.pattern, 'warn');
  callout('transport', run.transport);
  callout('schema note', run.schema_note);

  if (run.request) {
    box.appendChild(el('div', { class: 'request' }, [
      el('span', { class: 'k', text: 'the customer request this run started from' }),
      el('span', { text: '“' + run.request + '”' }),
    ]));
  }
  return box;
}

// --------------------------------------------------------------------- boot

function colourAssigner() {
  const seen = new Map();
  return (name) => {
    if (!seen.has(name)) seen.set(name, AGENT_COLOURS[seen.size % AGENT_COLOURS.length]);
    return seen.get(name);
  };
}

function render(run) {
  const app = document.getElementById('app');
  app.textContent = '';
  const spans = run.spans || [];
  const tree = buildTree(spans);
  const { ordered, total } = flatten(tree.roots);
  const colourOf = colourAssigner();
  const ns = toolNamespace(run.transport);

  const sect = (title, node) => {
    const s = el('section', {}, [el('h2', { text: title }), node]);
    app.appendChild(s);
  };

  sect('This run', header(run, ns));
  app.appendChild(el('section', {}, [statStrip(run, ordered, total)]));

  const topoCard = el('div', { class: 'card topo' }, [topology(run, tree, colourOf)]);
  topoCard.appendChild(el('p', { class: 'unrun-note', style: 'margin:10px 0 0', text: run.pattern || '' }));
  sect('Orchestration topology', topoCard);

  const wfWrap = el('div', {}, [
    el('p', {
      class: 'unrun-note',
      text: spans.some((s) => typeof s.start_ms === 'number')
        ? 'Bars are positioned from the recorded start offsets.'
        : 'This file records durations but no wall-clock start times, so bars are laid ' +
          'out sequentially in file order: widths are measured, horizontal positions are derived. ' +
          'A span with no duration of its own is drawn as a dashed envelope of its children. ' +
          'Click any row for its inputs, outputs, tool calls and ReAct steps.',
    }),
    waterfall(run, tree, ordered, total, colourOf, ns),
  ]);
  sect('Waterfall', wfWrap);
}

function empty(msg) {
  document.getElementById('app').replaceChildren(el('section', {}, [
    el('div', { class: 'empty' }, [msg]),
  ]));
}

async function boot() {
  const picker = document.getElementById('run');
  let manifest;
  try {
    const r = await fetch(DATA + 'index.json');
    if (!r.ok) throw new Error(r.status);
    manifest = await r.json();
  } catch (e) {
    picker.disabled = true;
    empty(el('div', {}, [
      el('p', { text: 'No recorded runs are being served from this page.' }),
      el('p', { html: 'The run files live in <code>docs/agent-runs/</code> and are published by ' +
        '<code>PYTHONPATH=. uv run python tools/export_static.py</code>, which copies them next to ' +
        'this page and writes the index it reads. Nothing is faked in their absence.' }),
    ]));
    return;
  }

  const runs = manifest.runs || [];
  if (!runs.length) { empty(el('p', { text: 'The run index is empty.' })); return; }

  for (const r of runs) {
    const label = (r.run_id || r.file) + (r.recorded ? '  ·  ' + r.recorded : '') +
      (r.provenance_kind && r.provenance_kind !== 'recorded' ? '  ·  ' + r.provenance_kind : '');
    picker.appendChild(el('option', { value: r.file, text: label }));
  }

  const wanted = new URLSearchParams(location.search).get('run');
  const start = runs.find((r) => r.file === wanted || r.run_id === wanted) || runs[0];
  picker.value = start.file;

  const load = async (file) => {
    try {
      const r = await fetch(DATA + file);
      if (!r.ok) throw new Error(r.status);
      render(await r.json());
    } catch (e) {
      empty(el('p', { text: 'Could not load ' + file + ': ' + e.message }));
    }
  };
  picker.addEventListener('change', () => {
    const u = new URL(location.href);
    u.searchParams.set('run', picker.value);
    history.replaceState(null, '', u);
    load(picker.value);
  });
  await load(start.file);
}

boot();
