import { z } from "zod";
import { validMutation } from "@mdp/showcase-auth";
import { session } from "../../../server/session";
import { budget } from "../../../server/read-budget";
import { controlStore } from "../../../server/clients";
import { recordCallsView } from "../../../server/call-store";
export async function POST(request: Request) {
  const current = await session();
  if (!current) return new Response("Open /sign-in.", { status: 401 });
  if (
    !validMutation(
      current,
      request.headers.get("origin"),
      request.headers.get("x-csrf-token") ?? "",
    )
  )
    return new Response("Open /songs?view=picks and try again.", { status: 403 });
  const input = z
    .object({ week: z.iso.date() })
    .safeParse(await request.json().catch(() => null));
  if (!input.success)
    return new Response("Open /songs?view=picks and choose a week.", { status: 400 });
  await budget.run("light", () =>
    recordCallsView(controlStore(), current, input.data.week),
  );
  return new Response(null, { status: 204 });
}
