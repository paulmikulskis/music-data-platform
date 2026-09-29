import { redirect } from "next/navigation";
import { sessionForToken } from "../../server/session";
import { errorHint } from "@mdp/contracts";
import { cookies } from "next/headers";
import {
  expired,
  timedOut,
  ended,
  noLink,
  SESSION_COOKIE,
  PRE_COOKIE,
  verifyLink,
  person,
} from "@mdp/showcase-auth";
import { ClearLink } from "../../components/clear-link";
import { SignInForm } from "../../components/sign-in-form";
import { Wordmark } from "../../components/wordmark";
export const dynamic = "force-dynamic";
export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const token = typeof params.t === "string" ? params.t : "";
  const link = token ? verifyLink(token) : null;
  const valid = link && person(link.handle);
  const jar = await cookies();
  const previousSession = jar.get(SESSION_COOKIE)?.value;
  if (
    !token &&
    !params.reason &&
    previousSession &&
    (await sessionForToken(previousSession))
  )
    redirect("/");
  const reason =
    params.reason === "key-refused"
      ? "key-refused"
      : params.reason === "expired" || (token && !valid)
        ? "expired"
        : valid || params.reason === "timeout"
          ? "timeout"
          : params.reason === "ended" || previousSession
            ? "ended"
            : null;
  const refused = errorHint("showcase_key_refused");
  const message =
    params.reason === "key-refused"
      ? `${refused.summary} ${refused.next_step}`
      : reason === "expired"
        ? expired
        : reason === "timeout"
          ? timedOut
          : reason === "ended"
            ? ended
            : noLink;
  const pre = jar.get(PRE_COOKIE)?.value ?? "";
  return (
    <main className="sign-in">
      <ClearLink reason={reason} />
      <header>
        <Wordmark />
        <span className="eyebrow">Data platform</span>
      </header>
      <section className="welcome">
        <span className="eyebrow">Music Data Platform, in view</span>
        <h1>
          See what
          <br />
          moved.
        </h1>
        <p className="intro">
          Activity, evidence, and a closer look at what Music Data Platform holds.
        </p>
        {valid ? (
          <SignInForm token={token} pre={pre} />
        ) : (
          <div className="notice">
            <p>{message}</p>
          </div>
        )}
      </section>
      <footer>Private access · Music Data Platform</footer>
    </main>
  );
}
