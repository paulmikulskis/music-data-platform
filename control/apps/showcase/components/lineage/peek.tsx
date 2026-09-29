"use client";
import { LinkOut } from "../link-out";
import { linkHref } from "../../lib/links";
import { readSheet } from "./read";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { z } from "zod";
import { peekView, type TraceView } from "../../lib/trace";
import { here } from "../../lib/proof-link";
import { LocalTime } from "../local-time";
const response = peekView.extend({ workbench: z.string().nullable() });
const labels: Record<string, string> = {
  title: "Playlist",
  platform: "Platform",
  followers: "Followers",
  observed_at: "Read",
  title_text: "Song",
  artist_text: "Artist",
  position: "Place",
  chart_date: "Date",
  handle: "Public account",
  snapshot_at: "Read",
};
export function Peek({
  node,
  close,
}: {
  node: TraceView["nodes"][number];
  close: () => void;
}) {
  const router = useRouter();
  const preview = (id: string) =>
    node.links?.find((link) => link.id === id) ?? null;
  const [data, setData] = useState<z.infer<typeof response> | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!node.preview || !node.relation) return;
    const controller = new AbortController();
    readSheet(
      `/s/peek?${new URLSearchParams({ relation: node.relation, filters: JSON.stringify(node.filters), expected: node.expected ?? "", from: here() ?? "/sources" })}`,
      controller.signal,
    )
      .then(async (result) => {
        if (result.status === 401 || result.status === 403) {
          setData(null);
          router.replace("/sign-in");
          return;
        }
        if (!result.ok) throw new Error("Read unavailable");
        setData(response.parse(await result.json()));
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [node, attempt, router]);
  return (
    <aside className="trace-peek" aria-label="Table preview">
      <div className="trace-peek-head">
        <h2>{node.label}</h2>
        <button onClick={close} aria-label="Close preview">
          ×
        </button>
      </div>
      {!node.preview || data?.state === "unavailable" ? (
        <p>
          No preview is available. <a href="/sources">See source details →</a>
        </p>
      ) : failed ? (
        <p>
          Peek is taking too long.{" "}
          <button
            onClick={() => {
              setFailed(false);
              setAttempt(attempt + 1);
            }}
          >
            Retry
          </button>
          , or <a href="/sources">read source details →</a>
        </p>
      ) : !data ? (
        <p>
          Reading. <button onClick={close}>Back to path</button>
        </p>
      ) : data.state === "changed" ? (
        <p>
          This reading changed. <a href="/sources">See source details →</a>
        </p>
      ) : (
        <>
          <details name="trace-peek" open>
            <summary>Results</summary>
            <p>
              {data.rows.length}
              {data.total !== null
                ? ` of ${BigInt(data.total).toLocaleString("en-US")}`
                : data.rows.length === 1
                  ? " result"
                  : " results"}
            </p>
            {data.captured_at && (
              <p>
                Stored at <LocalTime at={data.captured_at} relativeDay />
              </p>
            )}
            {data.cache_state !== "live" && data.saved_at && (
              <p className="trace-cached">
                Cached · as of <LocalTime at={data.saved_at} />
              </p>
            )}
            <div className="trace-table">
              <table>
                <thead>
                  <tr>
                    {data.columns.map((column) => (
                      <th
                        key={column}
                        title={labels[column] ?? column.replaceAll("_", " ")}
                      >
                        {labels[column] ?? column.replaceAll("_", " ")}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row, i) => (
                    <tr key={i}>
                      {data.columns.map((column) => (
                        <td key={column}>
                          {row[column] === null ? (
                            "Unknown"
                          ) : column === "chart_date" &&
                            typeof row[column] === "string" ? (
                            new Date(row[column]).toLocaleDateString("en-US", {
                              timeZone: "UTC",
                              dateStyle: "medium",
                            })
                          ) : (column === "observed_at" ||
                              column === "snapshot_at") &&
                            typeof row[column] === "string" ? (
                            <LocalTime at={row[column]} relativeDay />
                          ) : (
                            String(row[column])
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>Not cleared for training/resale.</p>
          </details>
          {data.workbench && (
            <details name="trace-peek" className="peek-workbench">
              <summary>Open in Workbench</summary>
              <LinkOut
                preview={preview("viewer-open-workbench")}
                signedHref={data.workbench}
              />
            </details>
          )}
          {data.sql && (
            <details name="trace-peek">
              <summary>Query for later</summary>
              <textarea
                aria-label="Query for later"
                data-proper-name
                readOnly
                value={data.sql}
              />
              <p>Select and copy. Ask the data team for access.</p>
            </details>
          )}
        </>
      )}
      {!node.relation && <a href="/stack#readers">See it on Stack →</a>}
      {preview("viewer-open-explorer") && (
        <details name="trace-peek" className="peek-explorer">
          <summary>Open in Console</summary>
          <LinkOut
            preview={preview("viewer-open-explorer")}
            onContinue={(event) => {
              const explorer = preview("viewer-open-explorer");
              const href = explorer
                ? linkHref(explorer, here() ?? "/sources")
                : null;
              if (href) event.currentTarget.href = href;
            }}
          />
        </details>
      )}
      <LinkOut
        preview={preview("viewer-node-code")}
        disclosureGroup="trace-peek"
      />
    </aside>
  );
}
