// Build check (DR-0001 §3.3 condition 2; issues #89, #87, #172, #173): the chart, the About page, the mimic, the what-if and the management views are separate chunks,
// not part of the entry bundle. Run after `npm run build`.
import { readdirSync } from "node:fs";

const files = readdirSync(new URL("../dist/assets/", import.meta.url));
let failed = 0;
for (const name of ["ControlChart", "About", "Mimic", "WhatIfView", "EvaluationView"]) {
  const hit = files.find((f) => f.startsWith(`${name}-`) && f.endsWith(".js"));
  console.log(hit ? `ok    chunk ${hit}` : `FAIL  no separate ${name} chunk in dist/assets`);
  if (!hit) failed++;
}
process.exit(failed ? 1 : 0);
