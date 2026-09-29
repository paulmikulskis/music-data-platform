import type { LinkPreview as Preview } from "../lib/links";
import { LocalTime } from "./local-time";

export type LinkCheck = {
  state: "answered" | "no_answer" | "not_checked";
  checked_at: string | null;
};

function Picture({ preview }: { preview: Preview }) {
  if (preview.kind === "folder") return <path d="M16 31V18h20l7 7h21v30H16Z" />;
  if (preview.kind === "file" || preview.kind === "doc")
    return (
      <>
        <path d="M23 16h25l12 12v37H23Z M48 16v13h12" />
        <path
          d={
            preview.kind === "doc"
              ? "M30 39h22 M30 47h22 M30 55h14"
              : "m36 39-6 6 6 6 m10-12 6 6-6 6"
          }
        />
      </>
    );
  if (preview.kind === "door")
    return <path d="M25 34v-9a13 13 0 0 1 26 0v9 M20 34h36v30H20Z M38 46v8" />;
  const destination = preview.destination;
  const route =
    destination && "route" in destination
      ? destination.route.split("?")[0]
      : "";
  return (
    <>
      <rect x="15" y="17" width="49" height="61" rx="3" />
      <path d="M15 29h49" />
      {route === "/ops" ? (
        <>
          <circle cx="27" cy="42" r="4" />
          <path d="M38 42h18 M23 56h33 M23 66h20" />
        </>
      ) : route?.startsWith("/functions") ? (
        <>
          <circle cx="24" cy="40" r="2" />
          <circle cx="24" cy="53" r="2" />
          <circle cx="24" cy="66" r="2" />
          <path d="M32 40h23 M32 53h17 M32 66h23" />
        </>
      ) : route === "/runs" ? (
        <>
          <path d="M23 40h32 M23 52h24 M23 64h17" />
          <path d="m48 62 3 3 7-8" />
        </>
      ) : route === "/workbench" ? (
        <>
          <path d="m23 38 4 4-4 4 M33 46h14 M15 54h49 M23 63h33 M23 70h20" />
        </>
      ) : (
        <path d="M23 39h33v30H23Z M23 49h33 M23 59h33 M34 39v30 M45 39v30" />
      )}
    </>
  );
}

export function LinkPreview({
  preview,
  check,
}: {
  preview: Preview;
  check?: LinkCheck | null;
}) {
  if (!preview.destination) return null;
  if (!preview.available || preview.kind === "none")
    return preview.frame.length ? (
      <p className="link-fallback">{preview.frame.join(" · ")}</p>
    ) : null;
  const count = (text: string) =>
    preview.kind === "folder" && /^\+\d+ more$/.test(text);
  const lines = [
    ...preview.body
      .filter((text) => !count(text))
      .map((text) => ({ text, proper: false })),
    ...preview.properNames.map((text) => ({ text, proper: true })),
    ...preview.body.filter(count).map((text) => ({ text, proper: false })),
  ];
  let lineNumber = 0;
  const runtime = preview.kind === "door" || preview.kind === "page";
  return (
    <figure className="link-preview" data-preview-kind={preview.kind}>
      <svg
        viewBox="0 0 320 152"
        role="img"
        aria-label={`${preview.label} preview`}
      >
        <g className="preview-drawing" aria-hidden="true">
          <Picture preview={preview} />
        </g>
        <g data-preview-body>
          {lines.map((line, index) => {
            const chunks = line.text.match(/.{1,28}(?:\s|$)|.{1,28}/g) ?? [
              line.text,
            ];
            const y = 23 + lineNumber * 18;
            lineNumber += chunks.length;
            return (
              <text
                key={`${index}:${line.text}`}
                x="80"
                y={y}
                data-proper-name={line.proper ? true : undefined}
                className={
                  preview.kind === "doc" && line.proper
                    ? "preview-target"
                    : undefined
                }
                fontSize="14"
              >
                {chunks.map((chunk, part) => (
                  <tspan key={part} x="80" dy={part === 0 ? 0 : 18}>
                    {chunk}
                  </tspan>
                ))}
              </text>
            );
          })}
        </g>
      </svg>
      <figcaption data-link-frame>
        {preview.frame
          .filter((_, index) => !runtime || index === 0)
          .map((line) => (
            <span key={line}>{line}</span>
          ))}
        {runtime && (
          <span data-live-check>
            <i
              aria-hidden="true"
              className={`preview-dot ${check?.state === "answered" ? "answered" : ""}`}
            />
            {check?.checked_at && check.state !== "not_checked" ? (
              <>
                {check.state === "answered" ? "Checked" : "No answer at"}{" "}
                <LocalTime at={check.checked_at} timeOnly />
              </>
            ) : (
              "Not checked"
            )}
          </span>
        )}
      </figcaption>
    </figure>
  );
}
