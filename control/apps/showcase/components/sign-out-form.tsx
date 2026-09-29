"use client";
import { useState, type FormEvent } from "react";
export function SignOutForm({ csrf }: { csrf: string }) {
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const response = await fetch("/sign-out", {
        method: "POST",
        body: new URLSearchParams({ csrf }),
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Sign-out unavailable");
      window.location.assign(response.url);
    } catch {
      setError("Can’t sign out. Press Sign out to try again.");
    }
  }
  return (
    <form action="/sign-out" method="post" onSubmit={submit}>
      <input type="hidden" name="csrf" value={csrf} />
      <button className="quiet">Sign out</button>
      <span role="status">{error}</span>
    </form>
  );
}
