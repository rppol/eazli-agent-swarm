/* One palette, imported by both the panel and the viewport.
 *
 * It lived in two places — a hex map in studio.js for the swatches and a
 * number map in viewer.js for the materials — which meant the legend dot and
 * the object it labelled could drift apart. This module is a few hundred bytes
 * and is bundled into the entry, so the panel can colour its swatches without
 * waiting for three.js.
 *
 * Chosen to read as materials rather than as a chart: upholstery blue, warm
 * walnut, oak, brass. The console had been a near-identical blue to the sofa,
 * so from across the room the plan looked like it contained two sofas.
 */

/* Role colours.
 *
 * Deliberately NOT a fallback for a product that publishes no colour any more —
 * see UNKNOWN_COLOUR below for why that was a lie. They remain the one shared
 * definition of "what colour is a sofa, generically", which the panel and the
 * viewport both used to spell out separately.
 */
export const PALETTE = {
  sofa: '#5a6ed0',            // upholstery blue
  armchair: '#6f7fd8',
  coffee_table: '#a9744f',    // warm walnut — was a green that read as plastic
  dining_table: '#8d5a38',    // darker stained wood
  tv_console: '#6b7480',      // oak-grey, deliberately far from the sofa blue
  bookshelf: '#7d7466',
  floor_lamp: '#c9a227',      // brass
  bed: '#5a6ed0',
  wardrobe: '#8a7358',
  dining_chairs_pair: '#b08243',
  other: '#8b98a5',
};

/** Same colour as an integer, for three.js materials. */
export const hex = (role) =>
  parseInt((PALETTE[role] ?? PALETTE.other).slice(1), 16);

/* Suggested, not sourced. Matches --warn in studio.css so the amber outline in
   the render and the amber panel entry that explains it are the same colour. */
export const SUGGESTION = '#d29922';
export const suggestionHex = parseInt(SUGGESTION.slice(1), 16);

/* Colour not published.
 *
 * 31 of the 191 usable items in the capture state no colour at all, and they
 * used to be drawn in PALETTE[role]. That is two failures at once: a sofa with
 * no stated colour rendered identically to one that genuinely is upholstery
 * blue, and the picture asserted a colour nobody published. Deriving a hue from
 * the ASIN instead would have been the same lie with an extra step.
 *
 * So "unknown" gets a look of its own rather than a borrowed one: a flat,
 * desaturated slate that appears nowhere else in this file and matches none of
 * the catalogue's colour words (the nearest published greys are #98a0aa and
 * #3a3f47), carrying a wireframe of the exact validated envelope and a diagonal
 * hatch across one face. Undecided, not confident. The dimensions are still
 * exact, so it stays a truthful drawing of a box of known size whose colour
 * nobody stated.
 */
export const UNKNOWN_COLOUR = '#4e545c';
export const unknownHex = parseInt(UNKNOWN_COLOUR.slice(1), 16);

/* The hatch and envelope lines drawn over an unknown-colour piece, and the
   same stripe the panel swatch uses, so the render and the legend agree. */
export const UNKNOWN_MARK = '#c3cad4';
export const unknownMarkHex = parseInt(UNKNOWN_MARK.slice(1), 16);
