"use client";
import { useEffect, useState } from "react";
import { stackStatus, type StackStatus } from "../../lib/stack";
import { stackGroups, plannedStack } from "../../lib/stack-cards";
import type { StackServiceView, StackView } from "../../server/stack";
import { dayLabel } from "../../lib/presentation";
import { Hover } from "../hover";
import { LocalTime } from "../local-time";
import { Mark } from "../mark";
import { Sheet } from "../sheet";
import { LinkOut } from "../link-out";
type Status = StackStatus["services"][number];
type Read =
  | { state: "reading" }
  | { state: "ready"; value: StackStatus }
  | { state: "failed"; at: string };
const sentence = (text: string) => text.replace(/\.$/, "");
export function StackCards({ view }: { view: StackView }) {
  const [read, setRead] = useState<Read>({ state: "reading" });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/s/stack", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Status unavailable");
        setRead({
          state: "ready",
          value: stackStatus.parse(await response.json()),
        });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setRead({ state: "failed", at: new Date().toISOString() });
      });
    return () => controller.abort();
  }, [version]);
  const retry = () => setVersion((n) => n + 1);
  return (
    <div className="stack-groups">
      {stackGroups.map((group) => (
        <section key={group.key} id={group.id} className="stack-group">
          <h2>{group.label}</h2>
          <div className="stack-grid">
            {view.services
              .filter((service) => service.group === group.key)
              .map((service) => (
                <ServiceCard
                  key={service.alias}
                  service={service}
                  read={read}
                  retry={retry}
                  notice={view.notice}
                />
              ))}
          </div>
        </section>
      ))}
      <section id="planned" className="stack-group stack-planned">
        <h2>Planned</h2>
        <ul>
          {plannedStack.map((item) => (
            <li key={item.name}>
              <span>{item.name}</span>
              <span>{item.state}</span>
              <small>{item.evidence}</small>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
function statusOf(read: Read, alias: string): Status | null {
  if (read.state !== "ready") return null;
  return read.value.services.find((item) => item.alias === alias) ?? null;
}
function StatusDot({
  service,
  read,
  retry,
}: {
  service: StackServiceView;
  read: Read;
  retry: () => void;
}) {
  const status = statusOf(read, service.alias);
  const tone =
    status?.state === "answered"
      ? "answered"
      : status?.state === "no_answer"
        ? "no-answer"
        : "unknown";
  return (
    <Hover
      label={`${service.name} status`}
      tap
      interactive
      card={
        <div className="status-card">
          {read.state === "reading" ? (
            <p>Checking.</p>
          ) : read.state === "failed" ? (
            <p>
              Status not checked at <LocalTime at={read.at} />.
            </p>
          ) : !status || status.state === "not_checked" ? (
            <p>
              {status?.probe === "Not checked" || !status
                ? "Not checked. "
                : `${status.probe}: not checked. `}
              {status?.note ?? "No check runs from this app."}
            </p>
          ) : status.state === "answered" ? (
            <p>
              {status.probe} answered{" "}
              {status.checked_at && <LocalTime at={status.checked_at} />}.{" "}
              {status.note}
            </p>
          ) : (
            <p>
              {status.probe}: no answer at{" "}
              {status.checked_at && <LocalTime at={status.checked_at} />}.
              {status.note !== "No answer." && ` ${status.note}`}
            </p>
          )}
          <p>
            <button onClick={retry}>Retry</button> ·{" "}
            <a href={`#${service.id}`}>Service details</a>
          </p>
        </div>
      }
    >
      <button
        aria-label={`${service.name} status`}
        className={`status-dot ${tone}`}
      />
    </Hover>
  );
}
function ServiceCard({
  service,
  read,
  retry,
  notice,
}: {
  service: StackServiceView;
  read: Read;
  retry: () => void;
  notice: string | null;
}) {
  const [more, setMore] = useState(false);
  const shown = service.facts.slice(0, service.count ? 2 : 3);
  // Each item keeps the date it was captured. One date closes the line when they all agree;
  // otherwise every item carries its own, so an older fact never borrows a newer date.
  const items = [
    ...(service.count
      ? [{ text: service.count.text, at: service.count.as_of }]
      : []),
    ...(service.storage
      ? [{ text: service.storage.text, at: service.storage.measured_at }]
      : []),
    ...shown.map((fact) => ({
      text: sentence(fact.text),
      at: fact.captured_at,
    })),
  ];
  const days = [...new Set(items.map((item) => dayLabel(item.at)))];
  return (
    <article id={service.id} className="stack-card service-card">
      <div className="card-kind">
        <StatusDot service={service} read={read} retry={retry} />
        <span>{service.kind}</span>
        {service.region && <span>· {service.region}</span>}
      </div>
      <h3>{service.name}</h3>
      <p className="card-tagline">{service.tagline}</p>
      <p className="facts-line">
        {days.length <= 1
          ? items.map((item) => item.text).join(" · ")
          : items.map((item, index) => (
              <span key={item.text}>
                {index > 0 && " · "}
                {item.text}
                <small> ({dayLabel(item.at)})</small>
              </span>
            ))}
        {days.length === 1 && <small> · as of {days[0]}</small>}
      </p>
      {notice && !service.count?.measured && (
        <p className="card-notice">{notice}</p>
      )}
      {service.marks.length > 0 && (
        <div className="service-marks">
          {service.marks.map((mark) => (
            <div
              className={mark === "postgresql" ? "postgres-tile" : ""}
              key={mark}
            >
              <Mark name={mark} />
            </div>
          ))}
        </div>
      )}
      {service.tie && <p className="tie-in">{service.tie}</p>}
      <div className="card-actions">
        <button onClick={() => setMore(true)}>See more</button>
        <LinkOut preview={service.code} />
        <LinkOut preview={service.open} check={statusOf(read, service.alias)} />
      </div>
      {more && (
        <Sheet
          title={service.name}
          urlKey={`stack-${service.id}`}
          close={() => setMore(false)}
          className="stack-sheet"
        >
          <p>
            {service.kind}
            {service.region ? ` · ${service.region}` : ""}
            {service.count
              ? ` · ${service.count.text} · ${dayLabel(service.count.as_of)}`
              : ""}
          </p>
          {service.facts[0] && (
            <p>
              {sentence(service.facts[0].text)} ·{" "}
              {dayLabel(service.facts[0].captured_at)}
            </p>
          )}
          <details className="service-facts">
            <summary>Service facts</summary>
            <ul className="sheet-facts">
              {service.storage && (
                <li>
                  {service.storage.text} · measured{" "}
                  {dayLabel(service.storage.measured_at)}
                </li>
              )}
              {service.facts.slice(1).map((fact) => (
                <li key={fact.text}>
                  {sentence(fact.text)} · {dayLabel(fact.captured_at)}
                </li>
              ))}
            </ul>
            <ul className="sheet-more">
              {service.more.map((line) => (
                <li key={line}>{line}</li>
              ))}
              {service.tie && <li>{service.tie}</li>}
            </ul>
            {service.marks.length > 0 && (
              <div className="service-marks">
                {service.marks.map((mark) => (
                  <div
                    className={mark === "postgresql" ? "postgres-tile" : ""}
                    key={mark}
                  >
                    <Mark name={mark} />
                  </div>
                ))}
              </div>
            )}
          </details>
          <div className="sheet-links">
            {service.links.map((preview) => (
              <LinkOut key={preview.id} preview={preview} />
            ))}
            <LinkOut preview={service.code} />
            <LinkOut
              preview={service.open}
              check={statusOf(read, service.alias)}
            />
          </div>
        </Sheet>
      )}
    </article>
  );
}
