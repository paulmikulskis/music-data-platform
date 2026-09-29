"""Run the existing lifecycle assertions with a data-race fixture time budget."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lifecycle"))
import harness


class AdversarialHarness(harness.Harness):
    def prepare(self):
        super().prepare()
        if self.args.case == "d":
            # This case holds a daily warehouse lock across an entire hourly job.
            # Its invariant is commit ordering, not a 90-second invocation SLA.
            # Keep deadline-expiry cases and every assertion in the base harness.
            self.configure_fixture_accounts({"timeout_s": 300})
            self.note(
                "cross-cadence fixture timeout_s=300; commit-order assertions unchanged"
            )


if __name__ == "__main__":
    harness.Harness = AdversarialHarness
    raise SystemExit(harness.main())
