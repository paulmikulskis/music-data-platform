import { z } from "zod";
import { validMutation } from "@mdp/showcase-auth";
import { session } from "../../server/session";
import { signedCard } from "../../lib/calls";
import { controlStore } from "../../server/clients";
import { budget } from "../../server/read-budget";
import {
  CallError,
  callsLeft,
  changeCall,
  submitCall,
} from "../../server/call-store";
import { callWeek } from "../../lib/calls";
const mutation = z.discriminatedUnion("action", [
  z.object({ action: z.literal("call"), signed: signedCard }),
  z.object({ action: z.enum(["undo", "hide"]), id: z.uuid() }),
]);
export function GET() {
  return new Response(null, {
    status: 301,
    headers: { Location: "/songs?view=picks" },
  });
}
export async function POST(request: Request) {
  const current = await session();
  if (!current)
    return Response.json(
      { error: "showcase_link_expired", next: "/sign-in" },
      { status: 401 },
    );
  if (
    !validMutation(
      current,
      request.headers.get("origin"),
      request.headers.get("x-csrf-token") ?? "",
    )
  )
    return Response.json(
      { error: "showcase_request_refused", next: "/" },
      { status: 403 },
    );
  const input = mutation.safeParse(await request.json().catch(() => null));
  if (!input.success)
    return Response.json(
      { error: "call_invalid", next: "/" },
      { status: 400 },
    );
  try {
    const data = input.data;
    const call = await budget.run("light", () =>
      data.action === "call"
        ? submitCall(controlStore(), current, data.signed)
        : changeCall(controlStore(), current, data.id, data.action),
    );
    // Every card's confirm sheet shows this count, so a call on one card updates the others.
    const left = await budget
      .run("light", () =>
        callsLeft(controlStore(), current.handle, callWeek()),
      )
      .catch(() => undefined);
    return Response.json(
      { call, left },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    if (error instanceof CallError)
      return Response.json(
        {
          error: error.code,
          next:
            error.code === "draft_closed" || error.code === "draft_not_open"
              ? "/songs?view=friday&board=1"
              : error.code === "call_limit"
                ? "/songs?view=picks"
                : "/",
        },
        { status: 409 },
      );
    return Response.json(
      { error: "call_checking", next: "/songs?view=picks" },
      { status: 503 },
    );
  }
}
