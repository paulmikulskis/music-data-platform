import { people } from "@mdp/showcase-auth";
import { errorHint } from "@mdp/contracts";

export const dynamic = "force-dynamic";
export function GET() {
  try {
    people();
    return Response.json({ status: "ok" });
  } catch {
    return Response.json(
      {
        status: "error",
        error_class: "showcase_people_invalid",
        ...errorHint("showcase_people_invalid"),
      },
      { status: 503 },
    );
  }
}
