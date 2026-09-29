import type { MouseEventHandler } from "react";
import { linkHref, type LinkPreview as Preview } from "../lib/links";
import { LinkPreview, type LinkCheck } from "./link-preview";

export function LinkOut({
  preview,
  check,
  onContinue,
  signedHref,
  disclosureGroup,
  showPreview = true,
}: {
  preview: Preview | null;
  signedHref?: string | null;
  disclosureGroup?: string;
  showPreview?: boolean;
  check?: LinkCheck | null;
  onContinue?: MouseEventHandler<HTMLAnchorElement>;
}) {
  if (!preview) return null;
  const href = signedHref ?? linkHref(preview);
  if (!href) return null;
  const code = preview.destination && "repo" in preview.destination;
  const door = preview.destination && "host" in preview.destination;
  const external = href.startsWith("https:");
  const content = (
    <>
      {showPreview && <LinkPreview preview={preview} check={check} />}
      {preview.access && <p className="link-access">{preview.access}</p>}
      <a
        href={href}
        target={external ? "_blank" : undefined}
        rel={external ? "noopener noreferrer" : undefined}
        onClick={onContinue}
      >
        {code
          ? "Open code ↗"
          : door
            ? "Continue to sign-in ↗"
            : `${preview.label} →`}
      </a>
    </>
  );
  return code || door ? (
    <details
      name={disclosureGroup}
      className={`link-out ${code ? "code-link" : "door-link"}`}
      data-link-id={preview.id}
    >
      <summary>{preview.label}</summary>
      {content}
    </details>
  ) : (
    <div className="link-out console-link" data-link-id={preview.id}>
      {content}
    </div>
  );
}
