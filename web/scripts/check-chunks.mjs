// Build check (DR-0001 §3.3 condition 2; issue #89): the chart and the About page are separate chunks,
// not part of the entry bundle. Run after `npm run build`.
import { readdirSync } from "node:fs";

const files = readdirSync(new URL("../dist/assets/", import.meta.url));
let failed = 0;
for (const name of ["ControlChart", "About"]) {
  const hit = files.find((f) => f.startsWith(`${name}-`) && f.endsWith(".js"));
  console.log(hit ? `ok    chunk ${hit}` : `FAIL  no separate ${name} chunk in dist/assets`);
  if (!hit) failed++;
}
process.exit(failed ? 1 : 0);
