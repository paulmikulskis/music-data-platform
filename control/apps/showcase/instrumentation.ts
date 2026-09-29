export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { sessionLifetimes, people } = await import("@mdp/showcase-auth");
    sessionLifetimes();
    people();
    if (process.env.MDP_CONTROL_RT_URL && process.env.MDP_SHOWCASE_PEOPLE) {
      const { startDraftJob } = await import("./server/draft-job");
      startDraftJob();
    }
    if (process.env.MDP_SHOWCASE_INVENTORY_ENABLED === "1") {
      const { startInventoryJob } = await import("./server/inventory-job");
      startInventoryJob();
    }
  }
}
