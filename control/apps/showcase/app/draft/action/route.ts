import { z } from "zod";
import { validMutation } from "@mdp/showcase-auth";
import { session } from "../../../server/session";
import { controlStore } from "../../../server/clients";
import { budget } from "../../../server/read-budget";
import { backRule, closeDraft } from "../../../server/draft-store";
import { CallError } from "../../../server/call-store";
import { draftWeek } from "../../../lib/draft";
const mutation = z.discriminatedUnion("action", [
  z
    .object({
      action: z.literal("back"),
      week: draftWeek,
      id: z.string().max(80),
    })
    .strict(),
  z
    .object({ action: z.literal("close"), week: draftWeek, key: z.uuid() })
    .strict(),
]);
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
      { error: "showcase_request_refused", next: "/songs?view=friday" },
      { status: 403 },
    );
  const parsed = mutation.safeParse(await request.json().catch(() => null));
  if (!parsed.success)
    return Response.json(
      { error: "draft_invalid", next: "/songs?view=friday" },
      { status: 400 },
    );
  try {
    const input = parsed.data;
    await budget.run("light", async () => {
      await (input.action === "back"
        ? backRule(controlStore(), current, input.week, input.id)
        : closeDraft(
            controlStore(),
            input.week,
            input.key,
            `api-key:${current.person.api_key_id}`,
          ));
    });
    return Response.json(
      { next: "/songs?view=friday&board=1" },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return Response.json(
      {
        error:
          error instanceof CallError
            ? error.code
            : parsed.data.action === "close"
              ? "draft_close_failed"
              : "draft_invalid",
        next: "/songs?view=friday&board=1",
      },
      { status: 409 },
    );
  }
}
