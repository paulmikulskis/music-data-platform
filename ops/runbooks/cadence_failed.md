# Cadence failed

A nonzero runner exit reports the failing model and error immediately.
Recovery also opens this critical alert when a cycle stays open beyond its interval:
one hour, one day or one week. Repeated reports produce one alert per cycle.
The status card shows the cadence as broken.

Open the alert's run and read the model error. Check the source receipt when an invoke
fails. An `invoke_timeout` error names the run, its state and its last target count at the
invocation deadline; open that run before retrying. `service_unreachable` means the functions
service refused the connection or missed three polls in a row; check its health first.
A source declared `blocks_cycle=False` never fails the cadence: its miss stays on its own
run alerts while the cycle closes.

Fix the cause and use Retry on the cycle. Retry keeps its frozen membership.
Check `Retry launcher: configured` in status before retrying.
A missing launcher token is a required-secret failure for the next control API deploy.
Retry rebuilds the newest open scheduled cycle of the cadence and scope. The banner offers
Retry only while that cycle is open, and Retry refuses a superseded cycle with
`cycle_superseded`. When the current cycle is already closed, the banner says so instead;
the next successful build resolves the alert.

Confirm the cycle closes and the marts rebuild. Acknowledge the alert while you work on it.
A build that closes the current scheduled cycle resolves the alert once its transform passes
and its close reaches the warehouse: a scheduled build, a Retry or a deploy restore.
A Replay resolves nothing. See [Alerts](../../docs/operating.md#alerts).
The scheduled gate still limits repeated failed work within one schedule period.
