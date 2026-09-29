"use client";
import { useState, type FormEvent } from "react";
export function SignInForm({ token, pre }: { token: string; pre: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      // Fetch uses same-origin credentials with a no-referrer policy. A native form navigation can send
      // Origin: null under that policy, which the server must continue to refuse.
      const response = await fetch("/sign-in", {
        method: "POST",
        body: new URLSearchParams({ token, pre }),
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Sign-in unavailable");
      window.location.assign(response.url);
    } catch {
      setError("Can’t reach sign-in. Press Sign in to try again.");
      setPending(false);
    }
  }
  return (
    <form action="/sign-in" method="post" onSubmit={submit}>
      <input type="hidden" name="token" value={token} />
      <input type="hidden" name="pre" value={pre} />
      <button className="primary" type="submit" disabled={pending}>
        {pending ? "Signing in" : "Sign in"} <span aria-hidden="true">↗</span>
      </button>
      <p role="status">{error}</p>
      <noscript>
        Sign-in needs JavaScript. Enable it, then open the link again.
      </noscript>
    </form>
  );
}
