import densityPolicy from "../../lib/card-words.json" with { type: "json" };
export { densityPolicy };
// Run inside a rendered phone page. Count only text the viewport actually displays.
// Playwright serializes each function alone, so each one carries its own helpers.
// Viewer words never appear on a viewer screen, sheet or hover card.
export function density(policy) {
  const banned = new RegExp("\\b(" + policy.banned.join("|") + ")\\b", "gi");
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return (
      style.visibility !== "hidden" &&
      style.display !== "none" &&
      Number(style.opacity) !== 0 &&
      box.width > 0 &&
      box.height > 0 &&
      box.top < innerHeight &&
      box.bottom > 0 &&
      box.left < innerWidth &&
      box.right > 0 &&
      !element.closest(
        '[aria-hidden="true"],dialog:not([open]),details:not([open])>:not(summary)',
      )
    );
  };
  const words = [];
  const detailWords = [];
  const named = [];
  const walker = document.createTreeWalker(
    document.querySelector("[data-viewer-screen]"),
    NodeFilter.SHOW_TEXT,
  );
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement || !visible(node.parentElement)) continue;
    if (["SCRIPT", "STYLE"].includes(node.parentElement.tagName)) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    if (rect.top >= innerHeight || rect.bottom <= 0) continue;
    const found =
      node.textContent.match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? [];
    words.push(...found);
    // Reviewed function explanations have their own sentence and card checks in stage-marks.
    // Keep their totals in evidence while measuring the surrounding short UI separately.
    if (
      node.parentElement.closest(
        ".function-name,.function-card,[data-function-description],[data-stage-sentence]",
      )
    )
      detailWords.push(...found);
    if (
      !node.parentElement.closest("[data-proper-name],[data-stage-sentence]")
    ) {
      // The required revision frame names this page's build. Only that exact frame
      // phrase is exempt; it still counts toward every visible word budget.
      const plain = node.parentElement.closest("[data-link-frame]")
        ? node.textContent.replace(
            policy.code_frame,
            "Code at this page's version",
          )
        : node.textContent;
      named.push(
        ...(plain.match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? []),
      );
    }
  }
  const text = words.join(" ");
  const checked = named.join(" ");
  // Mirrors docs/copy.md's Headlines rules and ops/ci/lint_copy.py's static check: a headline
  // that personifies the product, performs a metaphor instead of stating a fact, or opens on a
  // rhetorical "the" should never reach a rendered screen, even a computed one the static lint can't see.
  const performativeHeadline = [
    /^(the|The)\s+\S/,
    /^origin (hears|sees|knows|feels|reads|watches|tracks|senses|understands|listens)\b/i,
    /leaves an? \w+\b|becomes? \w+\b|keeps listening/i,
  ];
  const headlineTexts = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")]
    .filter(visible)
    .map((e) => e.textContent.trim());
  const headlines = [...document.querySelectorAll("h1")]
    .filter(visible)
    .map((e) => e.textContent.trim().split(/\s+/).length);
  const headlinePatterns = headlineTexts.filter(
    (t) =>
      !["the music is out of reach.", "the songs are out of reach."].includes(
        t.toLowerCase(),
      ) && performativeHeadline.some((re) => re.test(t)),
  );
  const cards = [...document.querySelectorAll("[data-card]")]
    .filter(visible)
    .map((e) => e.querySelectorAll("[data-primary]").length);
  const tables = [...document.querySelectorAll("table")].filter(visible).length;
  const monospace = [
    ...document.querySelectorAll("[data-viewer-screen] *"),
  ].filter(
    (e) =>
      visible(e) &&
      e.childNodes.length &&
      /Geist|monospace/i.test(getComputedStyle(e).fontFamily),
  ).length;
  return {
    words: words.length,
    detailWords: detailWords.length,
    detailNumbers: detailWords.filter((w) => /\d/.test(w)).length,
    numbers: words.filter((w) => /\d/.test(w)).length,
    banned:
      checked.replace(/\bSee (?:the )?rows\b/g, "See results").match(banned) ??
      [],
    jargonWarnings: [
      ...(checked.match(
        new RegExp("\\b(" + policy.jargon.join("|") + ")\\b", "gi"),
      ) ?? []),
    ],
    clockViolations: [...document.querySelectorAll("time[data-local-time]")]
      .filter(visible)
      .filter((element) => {
        const zone = new Intl.DateTimeFormat(undefined, {
          timeZoneName: "short",
        })
          .formatToParts(new Date(element.dateTime))
          .find((part) => part.type === "timeZoneName")?.value;
        return !zone || !element.textContent.includes(zone);
      })
      .map((element) => element.textContent),
    headlines,
    headlinePatterns,
    cards,
    tables,
    monospace,
    text,
  };
}
// The newest open hover card, reveal or sheet: its visible words, numbers and banned words.
// Popovers hold at most 30 words and 4 numbers; sheets at most 80 words.
export function overlay({ kind, policy, selector = "" }) {
  const banned = new RegExp("\\b(" + policy.banned.join("|") + ")\\b", "gi");
  const visible = (element) => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return (
      style.visibility !== "hidden" &&
      style.display !== "none" &&
      Number(style.opacity) !== 0 &&
      box.width > 0 &&
      box.height > 0 &&
      !element.closest(
        '[aria-hidden="true"],dialog:not([open]),details:not([open])>:not(summary)',
      )
    );
  };
  const roots = [
    ...document.querySelectorAll(
      selector || (kind === "sheet" ? "dialog[open]" : "[data-popover]"),
    ),
  ].filter(visible);
  const root = roots.at(-1);
  if (!root)
    return {
      found: false,
      words: 0,
      detailWords: 0,
      detailNumbers: 0,
      numbers: 0,
      banned: [],
      text: "",
    };
  const words = [];
  const detailWords = [];
  const named = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement || !visible(node.parentElement)) continue;
    if (["SCRIPT", "STYLE", "TITLE"].includes(node.parentElement.tagName))
      continue;
    const found =
      node.textContent.match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? [];
    words.push(...found);
    // Reviewed function explanations have their own sentence and card checks in stage-marks.
    // Keep their totals in evidence while measuring the surrounding short UI separately.
    if (
      node.parentElement.closest(
        ".function-name,.function-card,[data-function-description],[data-stage-sentence]",
      )
    )
      detailWords.push(...found);
    if (
      !node.parentElement.closest("[data-proper-name],[data-stage-sentence]")
    ) {
      // The required revision frame names this page's build. Only that exact frame
      // phrase is exempt; it still counts toward every visible word budget.
      const plain = node.parentElement.closest("[data-link-frame]")
        ? node.textContent.replace(
            policy.code_frame,
            "Code at this page's version",
          )
        : node.textContent;
      named.push(
        ...(plain.match(/[\p{L}\p{N}]+(?:[’':.,-][\p{L}\p{N}]+)*/gu) ?? []),
      );
    }
  }
  const text = words.join(" ");
  return {
    found: true,
    words: words.length,
    detailWords: detailWords.length,
    detailNumbers: detailWords.filter((w) => /\d/.test(w)).length,
    numbers: words.filter((w) => /\d/.test(w)).length,
    banned:
      named
        .join(" ")
        .replace(/\bSee (?:the )?rows\b/g, "See results")
        .match(banned) ?? [],
    jargonWarnings:
      named
        .join(" ")
        .match(new RegExp("\\b(" + policy.jargon.join("|") + ")\\b", "gi")) ??
      [],
    text,
  };
}
