import type postgres from "postgres";

type Read = <T>(query: PromiseLike<T>) => Promise<T>;
const code = (error: unknown) =>
  typeof error === "object" && error !== null && "code" in error ? error.code : undefined;

// PostgreSQL ends reader_wh transactions at six seconds. This timer only bounds the
// caller's wait; sql.begin owns rollback and connection replacement, including late errors.
export async function readTransaction<T>(
  warehouse: postgres.Sql,
  work: (tx: postgres.TransactionSql, read: Read) => Promise<T>,
): Promise<T> {
  const started = performance.now();
  let expired = false;
  let queryError: unknown;
  let began = false;
  const timeout = Object.assign(new Error("Read deadline exceeded; narrow the range"), { code: "57014" });
  let timer: ReturnType<typeof setTimeout>;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => { expired = true; reject(timeout); }, 6500);
  });
  const read: Read = async query => {
    if (expired) throw timeout;
    return await query;
  };
  const begin = () => warehouse.begin("isolation level repeatable read read only", async tx => {
    began = true;
    try {
      if (expired) throw timeout;
      return await work(tx, read);
    } catch (error) {
      queryError = error;
      // PostgreSQL has ended this session. Do not let sql.begin send ROLLBACK to
      // its closed socket; its connection-close handler rejects the transaction.
      if (code(error) === "25P04" || code(error) === "CONNECTION_CLOSED")
        return await new Promise<T>(() => {});
      throw error;
    }
  });
  // The driver can deliver the previous session's 25P04 while opening its replacement.
  // Retry that opening once, only before this transaction's callback has run.
  const transaction = begin().catch(error => {
    if (!began && !expired && code(error) === "25P04") return begin();
    throw error;
  });
  try { return await Promise.race([transaction as Promise<T>, deadline]); }
  catch (error) {
    // An idle transaction has no active query to receive PostgreSQL's 25P04.
    const closedAtDeadline = code(error) === "CONNECTION_CLOSED" && performance.now() - started >= 6000;
    if (expired || code(error) === "25P04" || code(queryError) === "25P04" || closedAtDeadline) throw timeout;
    throw error;
  } finally { clearTimeout(timer!); }
}
