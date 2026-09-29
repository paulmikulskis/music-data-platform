# Control

The Node 22/pnpm 10 workspace contains control-api, data-api, control-db, contracts,
data-sdk, mdp-cli, showcase and showcase-auth. Drizzle owns control state; generated dbt contracts drive typed reads.

From the repository root:

```sh
pnpm --dir control install
pnpm --dir control typecheck
pnpm --dir control test
```

[Local startup and status](../docs/DEVELOPING.md) cover migrations, role URLs, dev authentication,
operator pages and the private Fly APIs. [Conventions](CLAUDE.md) cover audited mutations,
OpenAPI conformance, SDK generation and workbench isolation. [Operating steps](../docs/operating.md)
cover source/mart authoring and operator workflows. Offline tests can skip database checks;
[accept-control.sh](../ops/ci/accept-control.sh) requires integration inputs.
