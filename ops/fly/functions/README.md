# Private functions deployment

Build from the repository root with `ops/fly/functions/Dockerfile`. The existing
`functions/Dockerfile` remains the package-only local image. This deployment
image adds volume ownership setup and an ops-owned private server entrypoint.
The api process binds IPv6 for direct `.internal` access. Its `dumps` volume is
mounted at `/data`, with dump and schema directories owned by the service UID.

`workbench` runs alongside api as a separate private process on port 8085.
Deployment selects both process groups; the `dumps` volume mounts only on api.
The workbench receives the secret store-owned control, reader, workbench and role-admin
DSNs required by the workbench runtime. The api child drops the additional
workbench credentials from its environment. Until R2 is supplied, workbench
artifacts use its machine filesystem; API retained dumps remain volume-backed.

`/v1/backfill` creates a fresh isolated run and `/v1/migrate` lands retained
committed output into an explicitly registered warehouse through normal receipts.
Keep the API dump volume and snapshots until migration receipts are verified.
