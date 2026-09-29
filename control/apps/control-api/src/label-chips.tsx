import { labelsFor, RelationLabel, type ResultLabel } from "@mdp/data-sdk";
import { z } from "zod";

const ChipLabels = RelationLabel.extend({
  tenants: z.array(z.string()).optional(),
  cross_tenant: z.boolean().optional(),
  unresolved: z.boolean().optional(),
  unresolved_inputs: z.array(z.string()).optional(),
});

export function LabelChips({
  labels,
}: {
  labels: z.infer<typeof RelationLabel> | z.infer<typeof ResultLabel>;
}) {
  const parsed = ChipLabels.parse(labels);
  const tenants = parsed.tenants?.length ? parsed.tenants : [parsed.tenant];
  const scope = parsed.tenant === "global" && !parsed.tenants?.length
    ? "global"
    : `tenant: ${tenants.join(", ")}`;
  const unresolved = parsed.unresolved_inputs?.length
    ? parsed.unresolved_inputs
    : ["query inputs"];
  return (
    <div class="label-chips" style="display:flex;gap:8px;flex-wrap:wrap;margin:12px 0">
      {[
        parsed.layer,
        parsed.category,
        scope,
        `learning: ${parsed.learning ? "yes" : "no"}`,
        `resale: ${parsed.resale ? "yes" : "no"}`,
        `licence: ${parsed.licence_status}`,
      ].map((label) => <span class="badge">{label}</span>)}
      {parsed.cross_tenant === true && (
        <strong role="alert" style="color:var(--danger);border:2px solid var(--danger);padding:8px">
          Mixes tenant data: {tenants.join(", ")}. Review the tenant labels before export.
        </strong>
      )}
      {parsed.unresolved === true && unresolved.map((input) => (
        <span class="label-note">
          Labels are unknown for {input}.{" "}
          <a href={`/explorer?q=${encodeURIComponent(input)}`}>Review input labels</a>.
        </span>
      ))}
    </div>
  );
}

export function RelationChips({ schema, name }: { schema: string; name: string }) {
  return <LabelChips labels={labelsFor(schema, name)} />;
}
