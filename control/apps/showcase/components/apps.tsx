"use client";
import { useEffect, useState } from "react";
import {
  appDescriptions,
  appsPayload,
  platformLine,
  type AppsPayload,
} from "../lib/apps";
import { LocalTime } from "./local-time";
import { Hover } from "./hover";
import { Mark } from "./mark";
import { LinkOut } from "./link-out";
import { linkHref } from "../lib/links";
import { viewerPath } from "../lib/proof-link";
// The data platform's machine counts come measured and dated from the deploy artifact through
// /s/apps, or not at all. The Stack page carries each count with its own date.
export function Apps({ compact = false }: { compact?: boolean }) {
  const [payload, setPayload] = useState<AppsPayload>({
    apps: [],
    platform: null,
    links: [],
  });
  const apps = payload.apps;
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/s/apps", { signal: controller.signal })
      .then(async (response) => {
        if (response.ok) setPayload(appsPayload.parse(await response.json()));
      })
      .catch(() => {});
    return () => controller.abort();
  }, [version]);
  return (
    <div className={`app-links stack-cards ${compact ? "compact-cards" : ""}`}>
      {appDescriptions.map((description) => {
        const app = apps.find((item) => item.name === description.name);
        const preview =
          payload.links.find(
            (link) =>
              link.id ===
              `avatar-open-${description.id === "music-data-platform" ? "console" : description.id}`,
          ) ?? null;
        return (
          <article
            id={description.id}
            key={description.id}
            className="stack-card"
          >
            <div className="app-title">
              <h2>{description.label}</h2>
              <Hover
                label="Service status"
                tap
                card={
                  <p>
                    {app?.checked_at ? (
                      <>
                        {app.state === "live"
                          ? "HTTP check answered"
                          : "No answer"}{" "}
                        · <LocalTime at={app.checked_at} />
                      </>
                    ) : (
                      "Not checked."
                    )}{" "}
                    <button onClick={() => setVersion((n) => n + 1)}>
                      Retry
                    </button>{" "}
                    · <a href={`/stack#${description.id}`}>Service details →</a>
                  </p>
                }
              >
                <button
                  aria-label={`${description.label} status`}
                  className={`status-dot ${app?.state === "live" ? "answered" : "unknown"}`}
                />
              </Hover>
            </div>
            <p>{description.tagline}</p>
            {compact &&
              description.id === "music-data-platform" &&
              payload.platform && (
                <details className="app-counts">
                  <summary>Machine counts</summary>
                  <small className="infra-line">
                    {platformLine(payload.platform)}
                  </small>
                </details>
              )}
            {!compact && (
              <>
                <div className="service-marks">
                  {description.marks.map((mark) => (
                    <div
                      className={mark === "postgresql" ? "postgres-tile" : ""}
                      key={mark}
                    >
                      <Mark name={mark} />
                    </div>
                  ))}
                </div>
                <details open={undefined}>
                  <summary>See more</summary>
                  <p>{description.detail}</p>
                  <a
                    href={description.href}
                    target={
                      description.href.startsWith("https:")
                        ? "_blank"
                        : undefined
                    }
                    rel="noopener noreferrer"
                  >
                    {description.href === "/ops" ? "Open Console" : "Open"} ↗
                  </a>
                </details>
              </>
            )}
            {compact && (
              <LinkOut
                preview={preview}
                check={{
                  state: app?.checked_at
                    ? app.state === "live"
                      ? "answered"
                      : "no_answer"
                    : "not_checked",
                  checked_at: app?.checked_at ?? null,
                }}
                onContinue={(event) => {
                  if (
                    !preview ||
                    !preview.destination ||
                    !("route" in preview.destination)
                  )
                    return;
                  const back =
                    viewerPath(
                      window.location.pathname +
                        window.location.search +
                        window.location.hash,
                    ) ?? "/";
                  const href = linkHref(preview, back);
                  if (href) event.currentTarget.href = href;
                }}
              />
            )}
          </article>
        );
      })}
      {compact ? (
        <a href="/stack">See the whole stack →</a>
      ) : (
        <p className="mark-credit">
          No affiliation with the companies shown.{" "}
          <a href="/sources?credits=1">Credits →</a>
        </p>
      )}
    </div>
  );
}
