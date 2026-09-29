"use client";
import { useState } from "react";
export function CopySQL({
  sql,
  kind = "sql",
}: {
  sql: string;
  kind?: "sql" | "command" | "request";
}) {
  const [message, setMessage] = useState("");
  return (
    <>
      <button
        className="quiet"
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(sql);
            setMessage(
              kind === "request"
                ? "Copied. Send it to the data team."
                : kind === "command"
                  ? "Copied. Run it in a terminal at the repository root."
                  : "Copied. Paste into your SQL editor.",
            );
          } catch {
            setMessage(
              kind === "request"
                ? "Copy is unavailable. Select the request below."
                : kind === "command"
                  ? "Copy is unavailable. Select the command below."
                  : "Copy is unavailable. Select the SQL below.",
            );
          }
        }}
      >
        {kind === "request"
          ? "Copy request"
          : kind === "command"
            ? "Copy command"
            : "Copy SQL"}
      </button>
      <span role="status">{message}</span>
    </>
  );
}
