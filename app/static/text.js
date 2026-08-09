/* eazli studio — the shared text primitives.
 *
 * Four escaping-and-formatting helpers and the colour swatch, pulled out of
 * studio.js so that brief.js can use the same ones rather than its own copies.
 *
 * brief.js is loaded lazily — it is ~10 KB of glossary and derivation that
 * nobody sees until they click a product — and a lazy module that imported
 * these from studio.js would be a cycle. A third module both sides import is
 * the same trick palette.js already plays for studio.js and viewer.js, and it
 * keeps there being exactly one `esc`, one `measure` and one swatch in the
 * build. Two copies of `measure` is two ways for a number to stop being a
 * measurement, which is the one thing this page may not get wrong.
 *
 * Everything here is pure: no DOM, no state, no fetch. That is what makes it
 * safe to share and what lets test_studio.py import it on its own.
 */

import { UNKNOWN_COLOUR } from './palette.js';

export const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export function money(n) { return Math.round(n).toLocaleString(); }

/** Reasons come back naming items by the id they were sent with — an ASIN.
 *  "Only 35cm between B0FR3WVLTS and B0H8PQ9KDJ" is precise and unreadable.
 *  Swap the codes for the slot names a person can actually see on screen. */
export function humaniseWithin(text, items) {
  let out = String(text);
  for (const item of items ?? []) {
    if (item.asin) out = out.replaceAll(item.asin, `the ${item.slot_id.replace(/_/g, ' ')}`);
    // Reasons also name items by slot_id, which is only unique inside a room.
    out = out.replaceAll(item.slot_id, item.slot_id.replace(/_/g, ' '));
  }
  return out;
}

/** Put the measurements in a sentence the engine wrote into `<code>`.
 *
 *  A number buried in prose reads as a claim; a number in a monospace box
 *  reads as something you can go and check with a tape measure, which is what
 *  every one of these is. Escapes FIRST — this inserts markup, so it must
 *  never be handed text that has already been marked up. */
export const measure = (text) => esc(text).replace(
  /\d[\d,]*(?:\.\d+)?(?:\s?[x×]\s?\d[\d,]*(?:\.\d+)?)*\s?(?:cm|SAR)\b/g,
  (m) => `<code>${m}</code>`);

/** How two prices in the plan relate. Subtraction of two recorded figures, and
 *  stated as money rather than as a measurement — it is not one, and it must
 *  not end up inside `<code>` pretending to be. */
export function priceGap(runner, item) {
  const gap = Math.round((runner.price_sar || 0) - (item.price_sar || 0));
  if (gap > 0) return `${money(gap)} SAR dearer`;
  if (gap < 0) return `${money(-gap)} SAR cheaper`;
  return 'the same price';
}

/* The dot beside an item, and the one case it has to be honest about.
 *
 * A listing that publishes no colour gets the same flat slate the render and
 * the viewport use, so the three cannot drift apart on what "unknown" looks
 * like. `background-color`, not the `background` shorthand, because the
 * shorthand would wipe the CSS hatch.
 *
 * `size` is for the brief, which shows the same dot inside the modal: the
 * stylesheet sizes `.item .swatch`, and out there it would collapse to
 * nothing. One function either way, so the two dots cannot drift apart. */
export const INLINE_DOT =
  'display:inline-block;width:11px;height:11px;border-radius:3px';

export function swatch(i, size = '') {
  return i.colour_hex
    ? `<span class="swatch" style="background-color:${i.colour_hex};${size}"
             title="colour published by the listing"></span>`
    : `<span class="swatch unknown" style="background-color:${UNKNOWN_COLOUR};${size}"
             title="the listing publishes no colour"></span>`;
}
