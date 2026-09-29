import postgres from "postgres";
import qr from "qrcode-terminal";
import type { ContractRouterClient } from "@orpc/contract";
import type { contract } from "@mdp/contracts";
import { revoke, required } from "@mdp/showcase-auth";
export async function showcase(
  client: ContractRouterClient<typeof contract>,
  args: string[],
) {
  const usage =
    "Run pnpm --dir control mdp showcase link --person <handle> [--ttl 24h], or showcase revoke --person <handle>.";
  const option = (name: string) => {
    const i = args.indexOf(name);
    return i < 0 ? undefined : args[i + 1];
  };
  const handle = option("--person");
  if (!handle || !["link", "revoke"].includes(args[0] ?? ""))
    throw new Error(usage);
  const ttl = /^(\d+)(m|h|d)$/.exec(option("--ttl") ?? "24h");
  if (!ttl) throw new Error(usage);
  if (args[0] === "link") {
    const { link_url } = await client.showcase.link({
      person: handle,
      ttl_seconds:
        Number(ttl[1]) * ({ m: 60, h: 3600, d: 86400 }[ttl[2]!] ?? 0),
    });
    console.log(link_url);
    qr.generate(link_url, { small: true });
    console.log("Open the link or scan the code, then select Sign in.");
    return;
  }
  const db = postgres(required("MDP_CONTROL_RT_URL"), {
    max: 1,
    onnotice: () => {},
  });
  try {
    if (args[0] === "revoke") {
      await revoke(db, handle);
      console.log(
        `Access revoked. To restore access, run pnpm --dir control mdp showcase link --person ${handle}.`,
      );
    }
  } finally {
    await db.end();
  }
}
