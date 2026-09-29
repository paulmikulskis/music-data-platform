import { redirect } from "next/navigation";
import "server-only";
import { errorStatus } from "./unknown";
import { requireSession } from "./session";
import { sources } from "./platform";
import { heartbeat } from "./heartbeat";
export async function room() {
  const current = await requireSession();
  await sources(current.person).catch(unavailable);
  await heartbeat.refresh(current.person);
  return current;
}

export function unavailable(error: unknown): null {
  const status = errorStatus(error);
  if (status === 401 || status === 403) redirect("/sign-in?reason=key-refused");
  return null;
}
