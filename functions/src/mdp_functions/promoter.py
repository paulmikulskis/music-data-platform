"""Source chaining uses the same audited target commands as the control UI."""

import csv
import hashlib
import io
from contextlib import asynccontextmanager

import httpx

from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import RefusingClient

PROMOTER_KEY_LABEL = "mdp-functions promoters"


def seed_promoter_key(conn, key: str) -> None:
    """Store the promoters' control-API key (`MDP_CONTROL_API_KEY`) as its sha256, role `promoter`,
    which reaches only the target commands below. A rotated key revokes the previous row; a revoked
    key stays revoked. `conn` is control_rt; the plaintext lives only in secret store and on mdp-functions."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    conn.execute(
        "INSERT INTO control.api_key(key_hash,label,role) VALUES (%s,%s,'promoter') ON CONFLICT(key_hash) DO NOTHING",
        (digest, PROMOTER_KEY_LABEL),
    )
    conn.execute(
        "UPDATE control.api_key SET revoked_at=now() WHERE role='promoter' AND label=%s AND key_hash<>%s AND revoked_at IS NULL",
        (PROMOTER_KEY_LABEL, digest),
    )


class ControlTargets:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.targets = self
        # Tests pass an httpx transport standing in for control-api.
        self.transport = transport

    @asynccontextmanager
    async def client(self):
        if not self.settings.control_api_url:
            raise ServiceError("control_api_unavailable", "Control API URL is required")
        headers = (
            {"x-api-key": self.settings.control_api_key}
            if self.settings.control_api_key
            else {}
        )
        async with RefusingClient(
            base_url=self.settings.control_api_url, headers=headers, timeout=30, transport=self.transport
        ) as client:
            yield client

    async def call(self, client, method, path, **kwargs):
        """One control-api request. An HTTP error status, a non-JSON answer, or a transport failure
        is `control_api_unavailable`, a classed run error (a 409 `target_held` is its own class), so
        no httpx error escapes a promoter run."""
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ServiceError(
                "control_api_unavailable", f"Control API {path} failed: {type(exc).__name__}"
            ) from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        if not response.is_success:
            data = body.get("data") if isinstance(body, dict) else None
            if response.status_code == 409 and isinstance(data, dict) and data.get("error_class") == "target_held":
                raise ServiceError("target_held", f"Control API {path}: {data.get('message')}")
            raise ServiceError(
                "control_api_unavailable", f"Control API {path} answered HTTP {response.status_code}"
            )
        if body is None:
            raise ServiceError("control_api_unavailable", f"Control API {path} answered non-JSON")
        return body

    async def command(self, client, path, body):
        return await self.call(client, "POST", "/api/targets/" + path, json=body)

    async def lookup(self, client, set_id, platform, account):
        """The keyed lookup: one identity's targets in the set, any state, with the spec's reason."""
        return await self.call(
            client,
            "GET",
            "/api/targets/lookup",
            params={"target_set_id": set_id, "platform": platform, "platform_account_id": account},
        )

    @staticmethod
    def forms(account):
        """A playlist's identity forms: the plan's `<market>:<id>` and the seed's bare id."""
        return {account, account.split(":", 1)[1]} if ":" in account else {account}

    @staticmethod
    def live(target):
        return (
            target["resolution_status"] == "resolved"
            and bool(target.get("activated_at"))
            and not target.get("deactivated_at")
        )

    async def imported(self, client, set_id, row):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        result = await self.command(
            client,
            "import",
            {"target_set_id": set_id, "dry_run": False, "csv": buffer.getvalue()},
        )
        return result["rows"]

    async def upsert(self, set_id, rows):
        """Resolve and activate each row's target, reusing a resolved one; a held target another
        writer gave a spec is theirs to activate. Returns the target ids in row order."""
        ids = []
        async with self.client() as client:
            for row in rows:
                held = [
                    t
                    for t in await self.lookup(client, set_id, row["platform"], row["platform_account_id"])
                    if t["resolution_status"] == "resolved"
                ]
                if held:
                    # Only its own spec-less import is its to activate without a reason, and a
                    # person's deactivation stands.
                    if held[0]["promoter_import"] and not held[0]["has_spec"] and not held[0]["person_deactivated"]:
                        await self.command(client, "activate", {"ids": [held[0]["id"]], "active": True})
                    ids.append(held[0]["id"])
                    continue
                for target in await self.imported(client, set_id, row):
                    await self.command(
                        client,
                        "resolve",
                        {"id": target["id"], "platform_account_id": row["platform_account_id"]},
                    )
                    await self.command(client, "activate", {"ids": [target["id"]], "active": True})
                    ids.append(target["id"])
        return ids

    async def promote(self, set_id, row, reason, spec):
        """a new target is imported, resolved, then given its spec with `reason` and activated
        in one control-api call, so it is never active without its provenance; `spec(target_id)`
        returns (resource_kind, canonical_key, params_json). A playlist the set holds in the other
        identity form is skipped. Held in this form: a spec-less target (a run that stopped before
        its spec) of its own import is completed; one carrying this reason and inactive is offered back, and
        control-api reactivates it only after this promoter's own deactivation, so a person's
        stands; any other is left. Returns the new or completed target id, else None."""
        account = row["platform_account_id"]
        async with self.client() as client:
            for other in self.forms(account) - {account}:
                if await self.lookup(client, set_id, row["platform"], other):
                    return None
            held = await self.lookup(client, set_id, row["platform"], account)
            if held and held[0]["has_spec"]:
                target = held[0]
                if target["promotion_reason"] == reason and target["resolution_status"] == "resolved" and not self.live(target):
                    await self.command(
                        client, "activate", {"ids": [target["id"]], "active": True, "promotion_reason": reason}
                    )
                return None
            if held and (not held[0]["promoter_import"] or held[0]["person_deactivated"]):
                # A person's spec-less import stays theirs, and so does their deactivation.
                return None
            target = held[0] if held else (await self.imported(client, set_id, row) or [None])[0]
            if target is None:
                return None
            if target["resolution_status"] != "resolved":
                await self.command(client, "resolve", {"id": target["id"], "platform_account_id": account})
            resource_kind, canonical_key, params = spec(target["id"])
            try:
                await self.command(
                    client,
                    "spec",
                    {
                        "id": target["id"],
                        "resource_kind": resource_kind,
                        "canonical_key": canonical_key,
                        "params_json": params,
                        "promotion_reason": reason,
                        "activate": True,
                    },
                )
            except ServiceError as exc:
                # A person deactivated this spec-less target; it stays so.
                if exc.error_class != "target_held":
                    raise
                return None
            return target["id"]

    async def propose(self, set_id, rows):
        """Import each row as a pending target for an operator to confirm, unless the set already
        holds it in any state. Returns the target ids that still need their spec, in row order:
        a new import, or a held one an earlier run left without its spec (None otherwise)."""
        ids = []
        async with self.client() as client:
            for row in rows:
                held = await self.lookup(client, set_id, row["platform"], row["platform_account_id"])
                if held:
                    ids.append(held[0]["id"] if held[0]["promoter_import"] and not held[0]["has_spec"] else None)
                    continue
                created = await self.imported(client, set_id, row)
                ids.append(created[0]["id"] if created else None)
        return ids

    async def deactivate(self, set_id, rows, reason=None):
        """Deactivate each row's active resolved target; with `reason`, only a target whose spec
        carries it (one this promoter created). Returns the ids deactivated."""
        done = []
        async with self.client() as client:
            for row in rows:
                for target in await self.lookup(client, set_id, row["platform"], row["platform_account_id"]):
                    if not self.live(target) or (reason and target["promotion_reason"] != reason):
                        continue
                    body = {"ids": [target["id"]], "active": False}
                    if reason:
                        body["promotion_reason"] = reason
                    changed = await self.command(client, "activate", body)
                    done.extend(t["id"] for t in changed)
        return done

    async def spec(self, target_id, resource_kind, canonical_key, params_json, reason=None):
        """Freeze a target's typed identity and params; keys its spec already holds win."""
        body = {
            "id": target_id,
            "resource_kind": resource_kind,
            "canonical_key": canonical_key,
            "params_json": params_json,
        }
        if reason:
            body["promotion_reason"] = reason
        async with self.client() as client:
            return await self.command(client, "spec", body)

    async def set_id(self, kind, tenant_id=None, name=None):
        """The id of the scope's set of this kind, created when absent (a tenant `track` set is
        created by its first proposal)."""
        async with self.client() as client:
            for found in await self.call(client, "GET", "/api/targets/sets"):
                if found["kind"] == kind and (found.get("tenant_id") or None) == (
                    str(tenant_id) if tenant_id else None
                ):
                    return found["id"]
            if name is None:
                raise ServiceError("scope_mismatch", f"No target set for {kind} and this scope")
            created = await self.command(
                client,
                "sets/create",
                {"kind": kind, "name": name, "tenant_id": str(tenant_id) if tenant_id else None},
            )
            return created["id"]
