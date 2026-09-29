import { session } from "../../../server/session";
import { teamQuestion } from "../../../server/team-questions";
import { teamQuestions } from "../../../lib/team-questions";
import { errorStatus } from "../../../server/unknown";
import { signProof } from "../../../server/proof-token";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const current = await session();
  if (!current) return new Response("Open /sign-in.", { status: 401 });
  const id = new URL(request.url).searchParams.get("id") ?? "";
  const question = teamQuestions.find((item) => item.id === id);
  if (!question)
    return new Response("Unknown question. Open /team.", { status: 404 });
  try {
    // The same reviewed SQL opens in the Workbench, shown before any session runs it.
    const token = signProof({
      level: "source",
      song: "",
      handle: current.handle,
      back: "/team#try",
    });
    return Response.json(
      {
        ...(await teamQuestion(id)),
        workbench: `/workbench?${new URLSearchParams({ intent: "query", sql: question.sql, showcase_proof: token })}`,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    const status = errorStatus(error);
    return new Response("Taking too long. Retry, or copy the SQL.", {
      status: status === 401 || status === 403 ? status : 503,
      headers: { "Cache-Control": "no-store", "Retry-After": "5" },
    });
  }
}
