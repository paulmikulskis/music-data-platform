import { listTag, overDays } from "../lib/music-facts";
// A compact card keeps its window in the detail opened by its fact.
export function CardMeta({
  list,
  days,
  basis,
  showWindow = true,
}: {
  list: string;
  days: number | null;
  basis: string | null;
  showWindow?: boolean;
}) {
  const tag = listTag(list, basis);
  return (
    <p className="card-meta">
      {tag && (
        <span className={`list-tag ${tag === "new list" ? "new" : tag}`}>
          {tag}
        </span>
      )}
      {tag === "new list" && (
        <>
          {" "}
          <span className="list-tag">age unknown</span>
        </>
      )}
      {showWindow && (
        <span className="card-window">
          {tag && " · "}
          {overDays(days)}
        </span>
      )}
    </p>
  );
}
