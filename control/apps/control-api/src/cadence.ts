import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import { AppError } from "./db.js";
export async function cadencePayload(source_key: string, cadence: string) {
  const root = new URL(
    "../../../../functions/src/mdp_functions/sources/",
    import.meta.url,
  );
  const candidates = readdirSync(root, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => new URL(`${e.name}/function.py`, root));
  let found: { path: string; before: string; after: string } | undefined;
  for (const path of candidates) {
    let text: string;
    try {
      text = readFileSync(path, "utf8");
    } catch {
      continue;
    }
    if (
      !text.includes(`source_key="${source_key}"`) &&
      !text.includes(`source_key='${source_key}'`)
    )
      continue;
    const after = text.replace(
      /cadence\s*=\s*(["'])(hourly|daily|weekly)\1/,
      `cadence="${cadence}"`,
    );
    if (after === text)
      throw new AppError(
        "cadence_unchanged",
        "Cadence already matches or its declaration needs a manual edit",
      );
    found = {
      path:
        "functions/src/mdp_functions/sources/" +
        fileURLToPath(path).split("/sources/")[1],
      before: text,
      after,
    };
    break;
  }
  if (!found)
    throw new AppError(
      "source_not_editable",
      "This built-in source needs a manual code change",
    );
  const before = found.before.trimEnd().split("\n");
  const after = found.after.trimEnd().split("\n");
  const diff = `--- a/${found.path}\n+++ b/${found.path}\n@@ -1,${before.length} +1,${after.length} @@\n${before.map((line, i) => (line === after[i] ? " " + line : "-" + line + "\n+" + after[i])).join("\n")}\n`;
  const branch = `cadence/${source_key}-${cadence}`;
  const title = `Change ${source_key} cadence to ${cadence}`;
  const body = `Change the function cadence to ${cadence}. Regenerate sources with mdp sources export and verify cadence selectors before merge.\n\nThis payload changes the declaration; generated dbt artifacts must be reviewed with it.\n\n${diff}`;
  const repo = process.env.GITHUB_REPOSITORY;
  const token = process.env.GITHUB_TOKEN;
  if (!token || !repo) return { branch, title, body, diff, pr_opened: false };
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo))
    throw new AppError(
      "github_unavailable",
      "Invalid repository configuration",
      503,
    );
  async function github(path: string, method = "GET", payload?: unknown) {
    const response = await fetch(
      `https://api.github.com/repos/${repo}/${path}`,
      {
        method,
        headers: {
          authorization: `Bearer ${token}`,
          "content-type": "application/json",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
        signal: AbortSignal.timeout(15000),
      },
    );
    if (!response.ok)
      throw new AppError(
        "github_unavailable",
        "GitHub could not create the cadence proposal",
        503,
      );
    return response.json();
  }
  const base = z
    .object({ default_branch: z.string() })
    .parse(await github("")).default_branch;
  const sha = z
    .object({ object: z.object({ sha: z.string() }) })
    .parse(await github(`git/ref/heads/${encodeURIComponent(base)}`))
    .object.sha;
  const current = z
    .object({ sha: z.string(), content: z.string() })
    .parse(
      await github(`contents/${found.path}?ref=${encodeURIComponent(base)}`),
    );
  if (Buffer.from(current.content, "base64").toString("utf8") !== found.before)
    throw new AppError(
      "github_source_changed",
      "Local declaration differs from the repository; refresh before proposing",
      409,
    );
  await github("git/refs", "POST", { ref: `refs/heads/${branch}`, sha });
  await github(`contents/${found.path}`, "PUT", {
    message: title,
    content: Buffer.from(found.after).toString("base64"),
    sha: current.sha,
    branch,
  });
  const pull = z
    .object({ html_url: z.string() })
    .parse(
      await github("pulls", "POST", {
        title,
        body,
        head: branch,
        base,
        draft: true,
      }),
    );
  return { branch, title, body, diff, pr_opened: true, url: pull.html_url };
}
