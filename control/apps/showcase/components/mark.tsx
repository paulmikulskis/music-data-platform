import { markFor } from "@mdp/contracts/flows";
import type { MarkTone } from "@mdp/contracts/marks";

export function Monogram({ name, label }: { name: string; label?: string }) {
  const mark = markFor(name);
  return (
    <span
      className="monogram text-plate"
      aria-label={label ?? mark?.label ?? "Source"}
    >
      {mark?.monogram ?? label ?? mark?.label ?? "Source"}
    </span>
  );
}
export function MarkImage({
  name,
  tone = "dark",
  size = 32,
}: {
  name: string;
  tone?: MarkTone;
  size?: number;
}) {
  const mark = markFor(name, tone);
  return mark?.src ? (
    <img
      className="mark-image"
      data-plate={mark.plate ?? undefined}
      src={mark.src}
      alt={mark.label}
      width={Math.max(21, size)}
      height={Math.max(21, size)}
    />
  ) : (
    <Monogram name={name} />
  );
}
export function Mark({
  name,
  label,
  tone = "dark",
  publicSurface = false,
  monogram = false,
}: {
  name: string;
  label?: string;
  tone?: MarkTone;
  publicSurface?: boolean;
  monogram?: boolean;
}) {
  const mark = markFor(name, tone);
  if (!mark) return <a href="/sources">Source details →</a>;
  return (
    <a
      className="provider-mark"
      href={mark.href}
      target={mark.href.startsWith("https:") ? "_blank" : undefined}
      rel="noopener noreferrer"
      aria-label={`Open ${label ?? mark.label}`}
    >
      {!publicSurface &&
        (monogram ? (
          <Monogram name={name} />
        ) : (
          <MarkImage name={name} tone={tone} />
        ))}
      {(publicSurface ||
        mark.src ||
        mark.monogram !== (label ?? mark.label)) && (
        <span>{label ?? mark.label}</span>
      )}
    </a>
  );
}
