// Copy the design tokens out of @codesweep-ai/ui into the page's own tokens.css.
//
// The path is resolved through the package's export map rather than reached for
// under node_modules, so the package decides where its tokens live and a move on
// its side is not a break on ours. The result is a build artifact: it is
// gitignored, and `npm install` is what updates it.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SPECIFIER = "@codesweep-ai/ui/tokens";
const OUT = resolve(process.argv[2] ?? "tokens.css");

let from;
try {
  from = fileURLToPath(import.meta.resolve(SPECIFIER));
} catch (cause) {
  console.error(`copy-tokens: cannot resolve ${SPECIFIER}. Run 'npm install' first.`);
  process.exit(1);
}

mkdirSync(dirname(OUT), { recursive: true });
copyFileSync(from, OUT);

const { version } = (await import("@codesweep-ai/ui/package.json", { with: { type: "json" } })).default;
console.log(`tokens: ${OUT} from @codesweep-ai/ui ${version}`);
