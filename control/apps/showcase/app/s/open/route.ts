import { z } from "zod";
import { session } from "../../../server/session";
import { signProof } from "../../../server/proof-token";
import { consolePath } from "../../../server/console-policy";
import { viewerPath } from "../../../lib/proof-link";

export const dynamic = "force-dynamic";
const roots = z.enum([
  "/ops",
  "/workbench",
  "/explorer",
  "/functions",
  "/runs",
]);
const headers = {
  "Cache-Control": "no-store",
  "Referrer-Policy": "same-origin",
};

export async function GET(request: Request) {
  try {
    const current = await session();
    if (!current)
      return new Response("Open /sign-in.", { status: 401, headers });
    const query = new URL(request.url).searchParams;
    const to = query.get("to") ?? "";
    const [path, search] = to.split("?");
    const root = roots.safeParse(path);
    const back = viewerPath(query.get("back"));
    const params = new URLSearchParams(search);
    if (
      !root.success ||
      !consolePath(root.data) ||
      !back ||
      to.includes("#") ||
      to.split("?").length > 2 ||
      (search !== undefined &&
        (root.data !== "/explorer" ||
          params.getAll("q").length !== 1 ||
          !params.get("q") ||
          [...params.keys()].some((key) => key !== "q"))) ||
      query.getAll("to").length !== 1 ||
      query.getAll("back").length !== 1 ||
      [...query.keys()].some((key) => key !== "to" && key !== "back")
    )
      return new Response("This destination is unavailable. Open /stack.", {
        status: 400,
        headers,
      });
    params.set(
      "showcase_proof",
      signProof({ level: "source", song: "", handle: current.handle, back }),
    );
    return new Response(null, {
      status: 303,
      headers: { ...headers, Location: `${root.data}?${params}` },
    });
  } catch {
    return new Response("The Console is unavailable. Retry from /stack.", {
      status: 503,
      headers,
    });
  }
}
