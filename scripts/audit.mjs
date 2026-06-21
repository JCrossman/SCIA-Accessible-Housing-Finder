#!/usr/bin/env node
/**
 * Offline accessibility audit of the generated map (axe-core + Playwright).
 *
 * The map normally needs the Google Maps API (a key + network) to render. For a
 * CI-friendly, offline audit (Constitution Art. 7.3 — tests don't call the live
 * service) this script:
 *   1. blocks all external requests,
 *   2. stubs `google.maps` and `markerClusterer` so the page's own initMap()
 *      builds the full UI (filters, year dropdowns, list view) with no network,
 *   3. runs axe-core against the resulting DOM and fails on violations.
 *
 * Usage:  node scripts/audit.mjs            # audits data/accessibility_map.html
 * Exit:   non-zero if any serious/critical violations are found.
 */
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const FILE = resolve('data/accessibility_map.html');
const URL = pathToFileURL(FILE).href;

// Minimal stubs so initMap() runs without the real Google Maps SDK.
const STUBS = `
  const noop = () => {};
  window.markerClusterer = { MarkerClusterer: class { clearMarkers(){} addMarkers(){} } };
  window.google = { maps: {
    Map: function(){ return { addListener: noop, fitBounds: noop }; },
    InfoWindow: function(){ return { setContent: noop, open: noop }; },
    LatLngBounds: function(){ return { extend: noop }; },
    Marker: function(){ this.addListener = noop; },
    Size: function(){}, Point: function(){},
    SymbolPath: { CIRCLE: 0 },
  }};
`;

const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();

// Offline: block every network request; the page is loaded from file://.
await page.route('**/*', (route) =>
  route.request().url().startsWith('file:') ? route.continue() : route.abort());

await page.addInitScript(STUBS);
await page.goto(URL, { waitUntil: 'domcontentloaded' });

// Build the dynamic UI (initMap is normally the Google callback).
await page.evaluate(() => window.initMap && window.initMap());
// Open the collapsible filter + list so axe sees them too.
await page.evaluate(() => {
  const f = document.getElementById('filter-toggle'); if (f) f.click();
  const l = document.getElementById('list-toggle'); if (l) l.click();
});
await page.waitForTimeout(200);

const results = await new AxeBuilder({ page })
  .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
  .analyze();

const serious = results.violations.filter(
  (v) => v.impact === 'serious' || v.impact === 'critical');

for (const v of results.violations) {
  const tag = (v.impact || 'minor').toUpperCase();
  console.log(`\n[${tag}] ${v.id} — ${v.help}`);
  console.log(`  ${v.helpUrl}`);
  for (const n of v.nodes.slice(0, 5)) console.log(`  → ${n.target.join(' ')}`);
}

await browser.close();

if (serious.length) {
  console.error(`\nFAIL: ${serious.length} serious/critical accessibility violation(s).`);
  process.exit(1);
}
console.log(`\nPASS: no serious/critical violations (${results.passes.length} checks passed).`);
