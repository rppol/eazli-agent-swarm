/* eazli studio — the per-item brief, loaded on demand.
 *
 * Everything the plan knows about one item, including the parts it does not
 * know, plus the two derived rows that need files the page has not fetched:
 * what the winner beat and on which term, and what else the engine already
 * verified would fit that slot.
 *
 * This module is dynamically imported by openBrief() in studio.js and nothing
 * else, so esbuild emits it as its own chunk. That is the whole reason it is a
 * separate file: the three glossaries and the derivation below are ~10 KB that
 * nobody sees until they click a product, and first paint is 68 KB. Same
 * mechanism viewer.js already uses to keep three.js off the critical path — no
 * second loader, no build change.
 *
 * The awkward rows are the point. 31 usable listings publish no colour and the
 * render draws them hatched; if the brief filled that gap in with a plausible
 * word, the picture and the page beside it would disagree and only one of them
 * would be honest.
 */

import { esc, money, measure, humaniseWithin, priceGap, swatch, INLINE_DOT }
  from './text.js';


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

export function briefHtml(i, plan, extras) {
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
    + (d.placed_because
      ? `<br>${esc(d.placed_because)}` : '')
    + (d.positions_tried
      ? ` <em>${d.positions_tried} position(s) tested for it.</em>` : ''));

  row('what it beat', beatHtml(i, plan, extras));
  row('what else fits here', alternativesHtml(i, plan, extras));

  return `<div class="detail" style="margin:12px 14px">${rows.join('')}</div>`;
}

/* ------------------------------------------ what it beat, and on what terms
 *
 * `decision.ranked_above` names the runners-up and their prices. It does not
 * say why each of them lost — but that is derivable exactly, without guessing
 * at anything, because the ranking key is a fixed tuple in app/planner.py:
 *
 *     (-style tags matched, -(rating x min(reviews,200)/200), price term, asin)
 *
 * and every term of it is published per candidate in data/candidates/
 * <category>.json. So the winner is compared with each runner-up term by term,
 * in that order, and the FIRST term that actually separates the two is the one
 * reported. Nothing is inferred from the product: where they tie all the way
 * down to the ASIN tiebreak, that is what it says, and where the candidate list
 * has not been fetched it says that instead of estimating.
 *
 * These two constants are app/planner.py's, mirrored. test_studio.py reads them
 * out of Python and fails this file if the pair ever drift apart, and re-derives
 * every comparison below against the planner's own `_score` on real plans.
 */
const EVIDENCE_FLOOR = { min_rating: 3.5, min_reviews: 5 };
const PLAUSIBILITY_FLAGS = ['implausible_for_category', 'implausible_price',
                            'dimension_conflict', 'incomplete_labelled_dimensions'];

const styleMatches = (c, style) =>
  (c.style || []).filter((t) => (style || []).includes(t)).length;
const evidenceScore = (c) => (c.rating || 0) * Math.min(c.reviews ?? 0, 200) / 200;
const clearsEvidenceFloor = (c) =>
  c.rating != null && c.rating >= EVIDENCE_FLOOR.min_rating
  && (c.reviews ?? 0) >= EVIDENCE_FLOOR.min_reviews
  && !(c.flags || []).some((f) => PLAUSIBILITY_FLAGS.includes(f));

/** The slot's budget target — but only where the plan states it. The planner
 *  writes the figure into `chose_because` on the slots where the target is what
 *  decided; where it is not written down it is not assumed here. */
function targetOf(item) {
  const m = /^budget target for this slot was ([\d.]+) SAR/
    .exec(item.decision?.chose_because ?? '');
  return m ? Number(m[1]) : null;
}

const priceTerm = (c, target) => (target != null && clearsEvidenceFloor(c)
  ? Math.abs((c.price_sar || 1e9) - target)
  : (c.price_sar || 1e9));

const starsOf = (c) => (c.rating != null
  ? `${c.rating}★ from ${c.reviews ?? 0} review(s)` : 'no rating published');

const priceTermWords = (c, target) => (target != null && clearsEvidenceFloor(c)
  ? `${money(Math.abs((c.price_sar || 0) - target))} SAR from the target`
  : `${money(c.price_sar || 0)} SAR outright`);

/** The first term of the ranking key on which these two differ. */
function separatedBy(win, run, style, target) {
  const ms = styleMatches(win, style);
  const mr = styleMatches(run, style);
  if (ms !== mr) {
    const asked = esc((style || []).join(' + '));
    return mr === 0
      ? `<b>style.</b> It carries none of the ${asked} tags the brief asked for;
         this one carries ${ms}.`
      : `<b>style.</b> It matched ${mr} of the ${asked} tags to this one's ${ms}.`;
  }
  const ew = evidenceScore(win);
  const er = evidenceScore(run);
  if (ew !== er) {
    return `<b>review evidence.</b> ${esc(starsOf(run))} against
      ${esc(starsOf(win))}${ms ? `, with both matching ${ms} style tag(s)`
        : ', with neither matching a style tag'}.`;
  }
  if (priceTerm(win, target) !== priceTerm(run, target)) {
    // Where the plan states the target, the term IS distance from it and can
    // be printed as such. Where it does not, the mechanism behind the third
    // term is not knowable from the plan — the target is worked out from a
    // first costing pass that is not exported — so what prints instead is only
    // what is checkable: the two are level above, and this one is cheaper.
    return target != null
      ? `<b>price.</b> ${priceTermWords(run, target)} against
         ${priceTermWords(win, target)} — the slot's budget target was
         ${money(target)} SAR and the closer piece takes it.`
      : `<b>price.</b> Level on style and on review evidence, and this one is the
         cheaper: ${money(win.price_sar || 0)} SAR against
         ${money(run.price_sar || 0)} SAR.`;
  }
  return `<b>nothing.</b> Same style match, same review evidence, same price term —
    the tiebreak is alphabetical on the ASIN, and ${esc(win.asin)} sorts before
    ${esc(run.asin)}.`;
}

/* READ THIS BEFORE TRUSTING THE FIELD NAME.
 *
 * `decision.ranked_above` does NOT mean "candidates the winner was ranked
 * above". It is the head of the ranked pool. app/planner.py writes
 *
 *     ranked_above=[... for p in pool[:4] if p.asin != product.asin]
 *
 * — the top four of the sorted pool with the winner removed — and the winner
 * is not always pool[0]. Measured over this build: 322 of the 3,375 entries
 * score ABOVE the piece that was actually placed. Every single one of those
 * 322 is also in `decision.rejected`, thrown out at the delivery or placement
 * stage before a position was ever tried for it; the planner walks the pool in
 * order and the first candidate that survives delivery AND finds a legal
 * position wins, so anything better that failed either check stays in the list
 * with no mark on it.
 *
 * The name invites exactly one mistake, and it is the mistake this project
 * exists not to make: printing "beaten by" over a product that was never
 * beaten. A 3,647 SAR loft bed that does not fit through the lift doors did
 * not lose to a 161 SAR dresser on price or on taste — it never got as far as
 * being compared.
 *
 * So every entry is checked against `decision.rejected` first. If the plan
 * records it as thrown out, the removal is the answer and the engine's own
 * measurement is quoted. Only when it is not thrown out is the ranking key
 * asked which term separated the two.
 *
 * test_a_candidate_that_outranked_the_winner_is_not_called_beaten holds this
 * down, and re-counts the 322 off the exported build rather than trusting this
 * comment's arithmetic.
 */
function beatHtml(i, plan, extras) {
  const above = i.decision?.ranked_above || [];
  if (!above.length) {
    return 'nothing. No other candidate in the pool was ranked below this one.';
  }
  const thrownOut = {};
  for (const r of i.decision?.rejected || []) thrownOut[r.asin] = r;
  const cands = extras?.candidates;

  const rows = above.map((r) => {
    const head = `<a href="https://www.amazon.sa/-/en/dp/${esc(r.asin)}" target="_blank"
      rel="noopener">${esc(String(r.title).slice(0, 60))}</a>
      <em>${money(r.price_sar || 0)} SAR</em> · ${priceGap(r, i)}`;
    const out = thrownOut[r.asin];
    if (out) {
      return `${head}<br><b>not beaten — removed.</b> It never reached a position to be
        judged in: thrown out at the <i>${esc(out.stage)}</i> stage.
        ${measure(humaniseWithin(out.why, plan?.placed))}`;
    }
    if (!cands) return `${head}<br><span class="muted">loading its attributes…</span>`;
    const win = cands[i.asin];
    const run = cands[r.asin];
    if (!win || !run) {
      return `${head}<br>the catalogue list for this category does not carry
        ${!run ? 'its' : "the winner's"} attributes in this build, so what separated
        them is not shown here.`;
    }
    return `${head}<br>${separatedBy(win, run, extras.style, targetOf(i))}`;
  });
  return rows.join('<hr class="d-sep">');
}

/* ---------------------------------------------------- and what else you have
 *
 * Not an estimate and not a list of things that look similar: every alternative
 * below was dropped into this exact slot at this exact position and put through
 * app/geometry.py at build time, and what prints is that engine's verdict. The
 * table is fetched only when this brief opens, because it runs 48-670 KB.
 */
function alternativesHtml(i, plan, extras) {
  if (extras === undefined) return 'open this brief in the page to load them.';
  const table = extras?.swaps;
  if (!table) {
    return extras?.swapsMissing
      ? `no alternatives were precomputed for this plan — whole-flat plans carry no
         swap table in this build, so nothing about a substitution can be claimed.`
      : 'loading the precomputed alternatives…';
  }
  const prefix = `${i.slot_id}|`;
  const keys = Object.keys(table)
    .filter((k) => k.startsWith(prefix) && k !== prefix + i.asin).sort();
  if (!keys.length) {
    return 'the build precomputed no alternative for this slot.';
  }
  const cands = extras.candidates || {};
  const alts = keys.map((k) => {
    const asin = k.slice(prefix.length);
    const e = table[k] || {};
    const access = e.access?.[asin] || {};
    return {
      asin,
      c: cands[asin],
      holds: e.validation?.status === 'pass',
      delivers: access.status === 'pass',
      // The first thing the engine said against it, in its own words.
      why: (e.validation?.reasons || [])[0]
        || (access.reasons || []).find((r) => /cannot|does not fit/i.test(r)) || '',
      total: e.total_sar,
    };
  });
  const both = alts.filter((a) => a.holds && a.delivers);
  const undeliverable = alts.filter((a) => !a.delivers);
  // The swap table is built over the whole category, which includes listings
  // that publish no price. "0 SAR" would be a claim about one of them.
  const name = (a) => (a.c
    ? `${esc(String(a.c.title).slice(0, 52))} <em>${a.c.price_sar != null
        ? `${money(a.c.price_sar)} SAR` : 'no price published'}</em>`
    : `<em>${esc(a.asin)}</em>`);

  const head = `${keys.length} other listing(s) were dropped into this slot at build
    time and re-checked by the engine. ${both.length} of them keep the arrangement valid
    <i>and</i> clear the delivery route; ${alts.length - both.length} do
    not${undeliverable.length
      ? `, ${undeliverable.length} of those because the building turns them back` : ''}.`;

  const works = both.length
    ? `<br><b>would work:</b><br>${both.slice(0, 4).map((a) => name(a)
        + (a.total ? ` · plan total ${money(a.total)} SAR` : '')).join('<br>')}`
      + (both.length > 4 ? `<br>and ${both.length - 4} more.` : '')
    : '<br><b>would work:</b> none. This slot has one answer in this catalogue.';

  const blocked = alts.filter((a) => !a.holds || !a.delivers).slice(0, 3);
  const fails = blocked.length
    ? `<br><b>would not:</b><br>${blocked.map((a) => `${name(a)} — ${a.why
        // The engine names every item by ASIN, including the one being tried:
        // "B07Y8ZXYNP and the bedside table overlap" is precise and unreadable.
        // The placed items already resolve to their slot names; the candidate
        // is given the one name it has here, which is "the candidate".
        ? measure(humaniseWithin(a.why,
            [...(plan?.placed || []), { asin: a.asin, slot_id: 'candidate' }]))
        : (a.delivers ? 'the engine rejected the arrangement' : 'refused on the route')}`)
      .join('<br>')}`
    : '';

  return `${head}${works}${fails}<br><span class="muted">Swap… opens the same list to
    pick from.</span>`;
}

/* Exported for test_studio.py, which re-derives every comparison this module
 * makes against app.planner._score on real exported plans, and fails the build
 * if the two constants below ever drift from the planner's own. Nothing in the
 * page imports these. */
export { separatedBy, targetOf, beatHtml, alternativesHtml,
         EVIDENCE_FLOOR, PLAUSIBILITY_FLAGS };
