# Showcase artifacts

Deploy the showcase with `ops/deploy.sh --app mdp-showcase`. No local database or prepared overlay is needed.
Run the development checks below from the repository root.
The generated-drift job `showcase-artifacts-postgres` uses `mdp-pg-lake` from
`ops/local/docker-compose.yml` on port 5434 with `WITH_PG_LAKE=0`. This checks PostgreSQL
roles and projections. It does not certify object storage.

```sh
pnpm --dir control install --frozen-lockfile
cp dbt/profiles/profiles.example.yml dbt/profiles/profiles.yml
MDP_PG_PASSWORD=dbt_transform uv run --project dbt dbt parse --project-dir dbt --profiles-dir dbt/profiles --target pg_local
uv run --project functions python ops/showcase/lineage/generate.py
uv run --project functions python ops/showcase/lineage/generate.py --check
```

`lineage.generated.json` records normalized input hashes. It has no deploy revision or clock.
The graph says **Can feed this**. Function defaults never establish a running collector.
The collapse walk follows dbt edges and function reads and writes. Each collapsed edge retains
its hidden nodes. Reviewed labels select the visible relations. Other dependencies stay secondary.

`entries.json` names each Home component separately. The typed reader in
`control/apps/showcase/lib/lineage.ts` accepts the served row from the selected ranking build.
Its locators select evidence links. A missing row or mismatched build gives an unlit graph.
`source_keys` is a conservative rights floor. It never lights an edge.
A relation locator alone does not prove which collector supplied it. Source edges remain unlit
until an exact contributing receipt is available. The optional receipt argument carries the
matching locator, song and ranking build. Only its declared writer and raw-table edge light;
unproved intermediate edges stay dashed. See the existing
`control/apps/showcase/server/proof.ts` for the row and build checks.


## Permissions

CI checks the reviewed queries as `showcase_wh` and `workbench_wh` on a disposable database.
The required `showcase-artifacts-postgres` job also checks drift and the final image.
`ops/ci-wait.sh <sha>` waits for that job on the pinned revision before deployment.
A missing, skipped or neutral job does not satisfy this gate.
Permission results bind to the committed lineage hash. They stay in the CI check, outside the image.

For local permission development, initialize a disposable database and run the adapters:

```sh
export MDP_PG_PORT=5434
bash ops/local/init.sh
bash ops/showcase/check-adapters.sh
```


## Deploy overlay

When showcase is selected, `ops/deploy.sh` archives the pinned revision and prepares its Stack facts
in a temporary directory inside that context. The collector uses `--dated-only`.
Hand facts keep their individual dates. Machine measurements and health probes say `Not checked`.
The validator also runs from the archive. Collection or validation failure stops image replacement.
See [Recover](#recover) for the retry command.

To build and check the image locally, commit generated lineage first:

```sh
export MDP_DEPLOY_REVISION=$(git rev-parse HEAD)
context=$(mktemp -d /tmp/showcase-context.XXXXXX)
python3 ops/fly/build-context.py "$context" --showcase
docker build --build-arg "MDP_DEPLOY_REVISION=$MDP_DEPLOY_REVISION" -f "$context/control/apps/showcase/Dockerfile" -t showcase-artifacts "$context"
bash ops/showcase/check-image.sh showcase-artifacts "$context"
rm -rf "$context"
```

`facts.json` holds the dated hand facts by service alias, plus an `Analysts` entry the Team page
reads. The showcase imports the file at build time, so the Stack cards render from the same dated
facts when the image carries no valid artifact. The browser gate collects a dated-only artifact
and starts the app with `MDP_SHOWCASE_ARTIFACTS_DIR` pointed at it.

`MDP_SHOWCASE_OVERLAY` optionally points to a directory containing `stack.generated.json`.
Use this override to supply measured facts from the same revision:

```sh
export MDP_SHOWCASE_OVERLAY=$(mktemp -d /tmp/showcase-overlay.XXXXXX)
bash ops/showcase/stack/collect.sh --output "$MDP_SHOWCASE_OVERLAY/stack.generated.json"
ops/deploy.sh --app mdp-showcase
rm -rf "$MDP_SHOWCASE_OVERLAY"
unset MDP_SHOWCASE_OVERLAY
```

The collector only reads allowlisted Fly machine and volume lists. It discards identifiers and
retains public aliases and aggregate measurements. Add `--dated-only` to skip those reads.
No service claims health from a machine list. Each probe starts as `Not checked`.
Readers and Workbench have separate process roles. Temporary restore machines do not count.

The context script installs Stack facts and writes a separate revision envelope.
It refuses mismatched hashes and measurements older than seven days.
The Dockerfile checks schemas, hashes and revision before building Next.js.
The image test reads lineage, Stack facts and the envelope from the final image.
It checks their input hashes and the image revision label. PostgreSQL privileges come from the CI gate.
Files live outside the public directory. Authenticated UI reads use `server/artifacts.ts`.
Run `bash ops/showcase/check-image.sh <image> <context>` to check a local image.


## Recover

The deploy counts client tenants before it collects links. When the control API is private, set
`MDP_SHOWCASE_TENANT_COUNT` from `select count(*) from control.tenant` (read-only) before the deploy.

If artifact preparation fails, the deploy names a user-only log in `/tmp` that holds the collector and
validator output; read it first, then delete it. To check the automatic path, rerun the context command
from the pinned checkout:

```sh
unset MDP_SHOWCASE_OVERLAY
context=$(mktemp -d /tmp/showcase-context.XXXXXX)
python3 ops/fly/build-context.py "$context" --showcase
rm -rf "$context"
```

If it still fails, run `bash ops/showcase/stack/collect.sh --dated-only --output /tmp/showcase-stack.json`
to inspect the collector error. For a rejected override, collect a fresh file from the pinned revision.
After the context check passes, rerun `ops/deploy.sh --app mdp-showcase`.

No migration or deployment is needed for these files alone. Ship them with the first UI that reads them.
Use the app selections in [Deploy](../../docs/operating.md#deploy).


## Source readers

`source-registry.generated.json` separates music readers from platform jobs.
It uses the function registry's invocation kind and external-access declaration.
Model calls, local derived steps and undeclared jobs stay out of the viewer source list.
A reader's enabled state and reading dates still come from `platform.sources`.
After changing a declaration, run:

```sh
uv run --project functions python ops/showcase/source_registry.py
uv run --project functions python ops/showcase/source_registry.py --check
```

The Home strip and Sources list group a reader with its `_weekly` variant.
Source details keeps each variant's own counts and dates. Open Daily targets or Weekly targets.


## Link previews

`ops/showcase/links/links.json` lists reviewed destinations and card words.
The build reads their files and folder listings from its pinned git revision.
It keeps only reviewed highlights, counts and headings. Open the manifest to add a destination.

The archive always collects links, including when `MDP_SHOWCASE_OVERLAY` supplies measured Stack facts.
No GitHub credential or network request is needed for this build step.
CI records `names_not_checked`, `Not checked at deploy` and `not_checked` for tenants.
Run the context command above to check a revision.

The GitHub landing view and the preview card have separate scans.
Code is expected to name its infrastructure. GitHub access is collaborator-only.
Cards never show raw code, listings or response bodies. Keep their words in the manifest.

`lib/linked-content-policy.json` defines the landing-view classes.
The build refuses email addresses, credential-shaped values, currency amounts and home paths
that contain a user name. The deploy also refuses names in its private deny list.
Known token prefixes, JWT-shaped values and private-key headers count as credentials.
So do `token=` markers, password-bearing database URLs and opaque tokens or keys.
The opaque check reads whole tokens of at least 20 characters only in a secret context.
The same line or field name must contain a secret word: `key`, `token`, `secret`,
`password`, `passwd`, `pwd`, `auth`, `authorization`, `bearer`, `api-key`, `apikey`,
`credential` or `private`. Case does not matter.
Words and name parts count, including `api_key`, `api-key`, `apiKey` and `credentials`.
The collector reads each CSV cell with its original header and reads JSON or YAML keys, including nested values.
CSV headers must contain a word and stay distinct when case, spaces and separators are ignored.
Malformed CSV or a row with too many or too few fields refuses the file.
For example, two `secret` headers refuse instead of discarding one value.
Artifact checks follow object keys and same-line words. Cards never contain raw source files.
For example, a public ID in `playlist_id` passes this check.
The same opaque value in `secret` or after `api_key=` refuses.
Known key prefixes, JWT shapes and private-key headers refuse even without a secret context.
It accepts lowercase hex strings of exactly 40 or 64 characters as public hashes.
It also accepts lowercase words joined by hyphens, unless a digit-only word exceeds eight digits.
Strings outside these exceptions still need to meet the opaque-token check below.
These exceptions never override known key prefixes, JWT shapes or private-key headers.
The same check applies to linked content and preview artifacts.
Its shared settings live in `opaque_token` in `lib/linked-content-policy.json`.
Mixed letters with digits refuse in unbroken strings or strings with base64 padding or `+`.
Other candidates use a 4.5-bit entropy threshold, capped a quarter bit below the maximum for their length.
Unbroken alphanumeric strings of 40 characters or more also refuse unless they are public hashes.
This keeps ordinary lowercase paths separate from base64-shaped secrets.
Run the audit below to check the resulting classes.
Every credential check reads the original text. Secret context also comes from the original text.
No infrastructure value, database URL or fixture marker masks any part of the credential scan.
Password-bearing database URLs refuse on every host, including local and placeholder hosts.
Preview cards keep all pattern classes hard.
Noreply senders, GitHub bot noreply addresses and the Workbench noreply bot are excepted.
Shell positional expansions and SQL parameters are code syntax, not currency amounts.
Literal amounts in quoted SQL values, shell literals, comments and prose still fail.
Read the link ID and class in the build log to find the manifest entry to review.
Rejected identifiers use the fixed diagnostic ID `links`. Correct the manifest ID, then rerun collection.

The Team readiness link opens [Before a PR](../../docs/DEVELOPING.md#before-a-pr).
That guide explains the selected checks without connection strings.
Run `bash ops/ready.sh` from the repository root to check a proposal.

Infrastructure inside linked code passes with a class-only record: `internal_host`, `ip_address`,
`env_name`, `machine_id`, `volume_id` or `image_id`. No matched value enters a log or artifact.
The preview artifact keeps every rule in `lib/sensitive-patterns.json` hard, plus the landing
view's hard rules. The reader applies the same strict preview checks.
Run the audit to count distinct link IDs per class. A link can belong to several classes:

```sh
uv run --project functions python ops/showcase/links/collect.py --audit --revision HEAD --output /tmp/showcase-link-audit.json
cat /tmp/showcase-link-audit.json
```

The audit reports all classes without stopping at the first match.
Its counts cover linked files, folder entry names, rendered READMEs and, when the name list and
full history are present, the listed entries' latest commit subjects.
Its `credential_review` entries report each refused link and its checked file paths.
They never contain matched values.
It records `names_not_checked` without those inputs. It does not approve an image.
Run the normal context command to enforce the hard checks before building an image.

`ops/showcase/links/name-exemptions.json` records reviewed exceptions to the name scan.
Each entry names one exact file, a reason and a review date. Its hash is bound to the image.
The public registry of rights-holding organizations has a reviewed name exception.
It reveals no client relationship. The exception covers that file's contents only.
Filenames, commit subjects and preview cards keep the name check.
Credential, email, price and home-path checks never use these exceptions.
Each link fact records `exempted_name_matches` as a count; the audit also totals those counts.
Repeated occurrences count separately. No matched name enters a log or artifact.
Review the list, then rerun the audit command above to check its counts.

A showcase deploy requires `MDP_SHOWCASE_DENY_NAMES` in its secret store configuration and full git history.
The deploy reads the tenant count without logging records and checks the three public health endpoints.
A timeout leaves the sign-in link available with `No answer at deploy`.
A positive tenant count requires an owner review in `console_tenant_review`.
Read the link id and error class in the build log, then follow the error catalog's next step.


```sh
```

Review and commit that diff. The normal build validates the saved facts without calling GitHub.
The commit date describes the reviewed version and has no freshness deadline.

The measured-overlay recipe also collects links automatically. To check its link step separately:

```sh
uv run --project functions python ops/showcase/links/collect.py --revision "$MDP_DEPLOY_REVISION" --output "$MDP_SHOWCASE_OVERLAY/links.generated.json"
```

The context command repeats collection from its archive before binding the hashes.
The image check reads the schema and hashes without git or a freshness recheck.
Run `bash ops/showcase/check-image.sh <image> <context>` to check an image.

Only `server/links.ts` reads the generated file in the app.
`readLinks().get(id, variant)` returns small props for a page or authenticated response.
The variant is a lineage file path, a global reader key, or a graphic part.
The provider-credit row uses the existing Mark catalog and has no preview facts.
Missing facts keep a plain reviewed link where a pinned destination is known.
Missing revision metadata removes code links. Stack and lineage reads stay independent.
Use `ops/showcase/links/fixture.py` to refresh the synthetic fixture for UI tests.
