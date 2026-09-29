import { healthSummaryDto } from "@mdp/contracts";
import { budget } from "../../../server/read-budget";

export const dynamic = "force-dynamic";

export async function GET() {
  const headers = { "Cache-Control": "no-store" };
  try {
    const result = await budget.run("light", async () => {
      const response = await fetch(new URL("/health/status", process.env.MDP_CONTROL_API_URL), {
        signal: AbortSignal.timeout(5000),
        redirect: "error",
        cache: "no-store",
      });
      // Parsing also strips any fields accidentally added upstream.
      const summary = healthSummaryDto.parse(await response.json());
      return { summary, ok: response.ok && summary.ok };
    });
    return Response.json(result.summary, { status: result.ok ? 200 : 503, headers });
  } catch {
    return Response.json({ ok: false, next_step: "Open /ops and check the control API connection." }, {
      status: 503,
      headers,
    });
  }
}
