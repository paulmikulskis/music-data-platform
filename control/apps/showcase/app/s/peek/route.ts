import { session } from "../../../server/session";
import { peek } from "../../../server/peek";
import { signProof } from "../../../server/proof-token";
import { viewerPath } from "../../../lib/proof-link";
import { z } from "zod";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  try {
    const current = await session();
    if (!current) return new Response("Open /sign-in.", { status: 401 });
    const query = new URL(request.url).searchParams;
    const filters = z
      .record(z.string(), z.union([z.string(), z.number(), z.boolean()]))
      .parse(JSON.parse(query.get("filters") ?? "{}"));
    const result = await peek(
      { relation: query.get("relation"), filters },
      query.get("expected") || undefined,
    );
    const token = signProof({
      level: "source",
      song: "",
      handle: current.handle,
      back: viewerPath(query.get("from")) ?? "/sources",
    });
    return Response.json(
      {
        ...result,
        workbench: result.sql
          ? `/workbench?${new URLSearchParams({ intent: "query", sql: result.sql, showcase_proof: token })}`
          : null,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return Response.json(
      { message: "Peek is taking too long. Retry, or read source details." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
