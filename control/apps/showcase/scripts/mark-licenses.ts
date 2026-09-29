import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { marks } from "@mdp/contracts/marks";

export function markLicenses() {
  const header =
    "# Provider marks\n\nMarks identify tools and data providers inside the signed-in app. Public pages use names as text. No affiliation is claimed. Files keep their original colors.\n\n";
  const rows = Object.values(marks).map((mark) =>
    [
      mark.label,
      mark.owner,
      [...new Set(Object.values(mark.files))].join(", ") || "Text only",
      mark.source_url,
      mark.rule,
      mark.changed ?? "Unchanged",
    ]
      .map((cell) => cell.replaceAll("|", "\\|"))
      .join(" | "),
  );
  return (
    header +
    "| Mark | Owner | Files | Source URL | Rule | Changed |\n|---|---|---|---|---|---|\n" +
    rows.map((row) => `| ${row} |`).join("\n") +
    "\n"
  );
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  writeFileSync(
    new URL("../public/brand/third-party/LICENSES.md", import.meta.url),
    markLicenses(),
  );
}
