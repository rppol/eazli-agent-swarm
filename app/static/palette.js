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
