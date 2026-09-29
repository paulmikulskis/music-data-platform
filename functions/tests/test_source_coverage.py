"""Source policies reach fresh defaults, HTTP receipts and the cadence's SQL caller."""

import textwrap
from dataclasses import replace
from uuid import uuid4

import httpx
import psycopg
import pytest
from mdp_functions.api import create_app
from mdp_functions.chart_targets import seed_chart_targets
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.settings import REPO
from mdp_functions.streamline_defaults import seed_streamline_defaults
from pydantic import BaseModel
from test_udf_poll import BODY, Plpy, PlpyError


async def bound(rt, key):
    from mdp_functions.targets import export_targets
    manifest = REGISTRY[key]
    dbt_id = 'local:' + uuid4().hex
    cycle = await rt.cycles.bind_cycle(manifest.cadence, 'global', dbt_id, 'scheduled',
                                      'local:' + manifest.cadence, runner='core')
    if manifest.targets:
        export_targets(rt.db, rt.warehouse, cycle['cycle_id'], manifest.targets.kind)
    return dbt_id, rt.admit(key, cadence=manifest.cadence, dbt_run_id=dbt_id)


class Row(BaseModel):
    value: int


def defaults(rt, databases, key):
    with psycopg.connect(databases['control_url']) as functions, psycopg.connect(databases['admin_control']) as control:
        control.execute("DELETE FROM control.audit_log WHERE action='streamline_defaults' AND subject=%s", (key,))
        seed_streamline_defaults(functions, control, {key: REGISTRY[key]})


async def poll(rt, run):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
                                base_url='http://test', headers={'Authorization': 'Bearer ' + rt.settings.service_token}) as client:
        response = await client.get(f'/v1/runs/{run["id"]}')
    assert response.status_code == 200, response.text
    return response.json()


def cadence_invoke(state, monkeypatch):
    client = httpx.Client
    def respond(request):
        return httpx.Response(202, json={'run_id': state['run']['id']}) if request.method == 'POST' else httpx.Response(200, json=state)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    namespace = {'plpy': Plpy('http://fixture')}
    exec('def invoke(source_key, params):\n' + textwrap.indent(BODY, '    '), namespace)  # noqa: S102 - checked-in SQL caller
    return namespace['invoke']('fixture', '{}')


@pytest.mark.parametrize('key', ['am_playlist', 'am_playlist_weekly', 'sp_playlist', 'sp_playlist_weekly', 'fixture_accounts', 'sz_chart'])
@pytest.mark.parametrize('size,dead,passes', [(10, 1, True), (10, 5, False), (2, 1, False), (0, 0, True)])
async def test_source_target_policy_through_cadence(rt, databases, monkeypatch, key, size, dead, passes):
    original = REGISTRY[key]
    async def collect(ctx, targets):
        for target in targets:
            response = await ctx.http.get('https://coverage.invalid/' + target['platform_account_id'])
            ctx.observed(1)
            yield response.json()
    monkeypatch.setitem(REGISTRY, key, replace(original, function=collect, schema=Row, key=['value'],
                                             writes=['raw.source_coverage'], hosts=['coverage.invalid']))
    sync(rt.db)
    defaults(rt, databases, key)
    with psycopg.connect(databases['admin_control']) as conn:
        conn.execute('DELETE FROM control.target')
        set_id = conn.execute("INSERT INTO control.target_set(kind,name) VALUES (%s,'Coverage fixture') ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id", (original.targets.kind,)).fetchone()[0]
        conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) SELECT %s,'fixture',n::text,'resolved',now() FROM generate_series(0,%s) n", (set_id, size-1))
        conn.execute("INSERT INTO control.host_health(host,host_rps) VALUES ('coverage.invalid',10000) ON CONFLICT(host) DO UPDATE SET host_rps=10000,blocked_until=NULL")
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(404 if int(request.url.path[1:]) < dead else 200, json={'value': int(request.url.path[1:])}))
    _, run = await bound(rt, key)
    await rt.execute(run['id'])
    state = await poll(rt, run)
    receipt = state['receipts'][0]
    assert receipt['allow_partial'] is True
    assert receipt['targets_succeeded'] == size-dead and receipt['targets_total'] == size
    assert receipt['target_coverage_met'] is passes
    assert state['run']['status'] == ('succeeded' if not dead else 'partial' if passes else 'failed')
    if passes:
        assert cadence_invoke(state, monkeypatch)
        assert rt.cycles.close(run['cycle_id'])['status'] == 'closed'
    else:
        with pytest.raises(PlpyError):
            cadence_invoke(state, monkeypatch)


@pytest.mark.parametrize('accepted,rejected,batch_size', [(9, 1, 10), (1, 9, 10), (0, 10, 10), (1, 9, 1)])
async def test_row_share_is_separate_from_target_success(rt, databases, monkeypatch, accepted, rejected, batch_size):
    key = 'fixture_accounts'
    async def collect(ctx, targets):
        for target in targets:
            for n in range(accepted + rejected):
                ctx.observed(1)
                yield {'value': n if n < accepted else 'invalid'}
    monkeypatch.setitem(REGISTRY, key, replace(REGISTRY[key], function=collect, schema=Row, key=['value'], writes=['raw.row_coverage']))
    sync(rt.db)
    defaults(rt, databases, key)
    with psycopg.connect(databases['admin_control']) as conn:
        conn.execute('UPDATE control.streamline SET batch_size=%s WHERE source_key=%s', (batch_size, key))
    _, run = await bound(rt, key)
    await rt.execute(run['id'])
    state = await poll(rt, run)
    for receipt in state['receipts']:
        assert receipt['row_rejection_share'] == rejected / (accepted+rejected)
        assert receipt['row_acceptance'] == accepted / (accepted+rejected)
        assert receipt['row_coverage_met'] is (accepted >= rejected)
        assert receipt['target_coverage_met'] is bool(accepted)
    assert state['run']['status'] == ('succeeded' if accepted >= rejected else 'failed')
    assert all(receipt['row_coverage'] == 'partial' for receipt in state['receipts'])
    alerts = rt.db.all("SELECT 1 FROM control.alert WHERE run_id=%s AND class='partial_coverage'", (run['id'],))
    assert bool(alerts) is (accepted < rejected)
    if accepted >= rejected:
        assert cadence_invoke(state, monkeypatch)
    else:
        with pytest.raises(PlpyError):
            cadence_invoke(state, monkeypatch)


async def test_shazam_seed_and_initial_defaults(rt, databases):
    defaults(rt, databases, 'sz_chart')
    assert rt.db.one("SELECT allow_partial FROM control.streamline WHERE source_key='sz_chart'")['allow_partial']
    with psycopg.connect(databases['admin_control']) as conn:
        assert seed_chart_targets(conn, REPO / 'inputs/chart_seed.csv') == 4
        rows = conn.execute("SELECT platform_account_id FROM control.target WHERE platform='shazam'").fetchall()
    assert len(rows) == 4 and all('washington' not in row[0] for row in rows)


@pytest.mark.parametrize('broken,passes', [(0, True), (1, True), (50, False), (100, False)])
async def test_billboard_chart_integrity_through_cadence(rt, databases, monkeypatch, broken, passes):
    import gzip
    html = gzip.decompress((REPO / 'functions/src/mdp_functions/sources/billboard/fixtures/synthetic-hot100-2026-09-18.html.gz').read_bytes()).decode()
    if broken:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for row in soup.select(".o-chart-results-list-row-container")[:broken]:
            for title in row.select("h3#title-of-a-story"):
                title.decompose()
        html = str(soup)
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    defaults(rt, databases, 'billboard_hot100')
    _, run = await bound(rt, 'billboard_hot100')
    await rt.execute(run['id'])
    state = await poll(rt, run)
    receipt = state['receipts'][0]
    assert receipt['min_row_coverage'] == 0.9 and receipt['min_target_coverage'] == 1.0
    assert receipt['allow_partial'] is True
    assert receipt['rows_written'] == str(100-broken) and receipt['rows_rejected'] == str(broken)
    assert receipt['row_coverage_met'] is passes
    assert state['run']['status'] == ('succeeded' if passes else 'failed')
    if passes:
        assert cadence_invoke(state, monkeypatch)
    else:
        with pytest.raises(PlpyError):
            cadence_invoke(state, monkeypatch)


@pytest.mark.parametrize('broken,supported', [(False, 0), (False, 1), (True, 1)])
async def test_apple_mostly_filtered_page_keeps_counts_and_signals_drift(rt, databases, monkeypatch, broken, supported):
    from copy import deepcopy

    from psycopg.types.json import Jsonb
    from test_playlist_publication import envelope

    _, member, _, tracks, body = envelope('am_playlist')
    track = deepcopy(tracks[0])
    tracks[:] = [deepcopy(track) for _ in range(10)]
    for item in tracks[supported:]:
        item['contentDescriptor']['kind'] = 'renamed_track' if broken else 'musicVideo'
    with psycopg.connect(databases['admin_control']) as conn:
        conn.execute('DELETE FROM control.target')
        set_id = conn.execute("INSERT INTO control.target_set(kind,name) VALUES ('playlist','Coverage fixture') ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id").fetchone()[0]
        target_id = conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) VALUES (%s,'apple_music',%s,%s,'resolved',now()) RETURNING id", (set_id, member.platform_account_id, member.handle)).fetchone()[0]
        conn.execute("INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) VALUES (%s,'playlist','fixture:apple',%s)", (target_id, Jsonb({'cadence': 'daily', 'storefront': 'us'})))
    defaults(rt, databases, 'am_playlist')
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body()))
    _, run = await bound(rt, 'am_playlist')
    await rt.execute(run['id'])
    state = await poll(rt, run)
    receipt = state['receipts'][0]
    assert receipt['rows_written'] == str(1 + supported) and receipt['rows_rejected'] == (str(10 - supported) if broken else '0')
    assert receipt['rows_excluded'] == ('0' if broken else str(10 - supported))
    assert receipt['row_exclusions'] == ({} if broken else {'unsupported_item:musicVideo': 10 - supported})
    assert receipt['target_coverage_met'] is True
    assert receipt['row_coverage_met'] is not broken
    assert state['run']['status'] == ('failed' if broken else 'partial')
    assert state['run']['coverage'] == 'partial'
    assert rt.db.one("SELECT 1 FROM control.alert WHERE run_id=%s AND class='envelope_mismatch'", (run['id'],))
    if broken:
        with pytest.raises(PlpyError):
            cadence_invoke(state, monkeypatch)
    else:
        assert receipt['row_acceptance'] == 1 and receipt['row_rejection_share'] == 0
        assert cadence_invoke(state, monkeypatch)
        assert not rt.db.one("SELECT 1 FROM control.alert WHERE run_id=%s AND class='target_zero_yield'", (run['id'],))
        assert not rt.db.one("SELECT 1 FROM control.run_event WHERE run_id=%s AND event_type='surface_drift'", (run['id'],))


@pytest.mark.parametrize('broken', [False, True])
async def test_kexp_air_breaks_and_parser_errors_through_cadence(rt, monkeypatch, broken):
    plays = [{'id': n, 'airdate': '2026-09-22T11:00:00Z', 'play_type': 'airbreak'} for n in range(10)]
    if broken:
        for play in plays:
            del play['airdate']
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'results': plays, 'next': None}))
    _, run = await bound(rt, 'kexp_plays')
    await rt.execute(run['id'])
    state = await poll(rt, run)
    receipt = state['receipts'][0]
    assert receipt['rows_written'] == '0' and receipt['rows_rejected'] == ('10' if broken else '0')
    assert receipt['rows_excluded'] == ('0' if broken else '10')
    assert receipt['row_exclusions'] == ({} if broken else {'not_a_trackplay': 10})
    assert receipt['row_coverage_met'] is not broken
    assert state['run']['status'] == ('failed' if broken else 'succeeded')
    if broken:
        assert receipt['row_rejection_share'] == 1
        with pytest.raises(PlpyError):
            cadence_invoke(state, monkeypatch)
    else:
        assert not rt.db.one("SELECT 1 FROM control.alert WHERE run_id=%s AND class='target_zero_yield'", (run['id'],))
        assert receipt['row_acceptance'] is None
        assert cadence_invoke(state, monkeypatch)


@pytest.mark.parametrize("mode", ["exclude", "reject", "undeclared"])
async def test_one_empty_target_among_ten_is_visible(rt, databases, monkeypatch, mode):
    key = "fixture_accounts"
    async def collect(ctx, targets):
        for target in targets:
            ctx.observed(1)
            if target["platform_account_id"] == "0":
                if mode == "reject":
                    ctx.reject({"position": 0}, reason="missing_field")
                else:
                    ctx.exclude({"position": 0}, reason="outside_scope" if mode == "exclude" else "unknown")
            else:
                yield {"value": int(target["platform_account_id"])}
    monkeypatch.setitem(REGISTRY, key, replace(REGISTRY[key], function=collect, schema=Row,
        key=["value"], writes=["raw.zero_yield_fixture"], exclusion_reasons=("outside_scope",)))
    sync(rt.db)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.target")
        set_id = conn.execute("SELECT id FROM control.target_set WHERE kind='account' LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,resolution_status,activated_at) "
                     "SELECT %s,'fixture',n::text,'resolved',now() FROM generate_series(0,9) n", (set_id,))
        empty_id = str(conn.execute("SELECT id FROM control.target WHERE platform_account_id='0'").fetchone()[0])
    _, run = await bound(rt, key)
    await rt.execute(run['id'])
    rt.settle(run['id'])
    state = await poll(rt, run)
    if mode == "undeclared":
        assert state['run']['status'] == 'failed'
        assert state['run']['error_class'] == 'undeclared_exclusion'
        assert state['receipts'][0]['rows_excluded'] == '0'
    else:
        assert state['run']['status'] == ('succeeded' if mode == 'exclude' else 'partial')
        warnings = rt.db.all("SELECT severity,subject_type,subject_id,runbook_slug FROM control.alert "
                             "WHERE run_id=%s AND class='target_zero_yield'", (run['id'],))
        assert warnings == [{"severity": "warning", "subject_type": "target", "subject_id": empty_id,
                             "runbook_slug": "target-zero-yield"}]
        assert len(rt.db.all("SELECT 1 FROM control.run_event WHERE run_id=%s AND event_type='target_zero_yield'", (run['id'],))) == 1


def test_every_known_non_track_kind_excludes_under_a_declared_reason():
    from mdp_functions.playlist import DECLARATION, NON_TRACK_KINDS, UnsupportedItem

    for kind in NON_TRACK_KINDS:
        item = UnsupportedItem(kind)
        assert item.known
        assert f"unsupported_item:{item.kind}" in DECLARATION["exclusion_reasons"]


async def test_second_partial_scheduled_run_opens_warning_and_full_run_resolves(rt, databases, monkeypatch):
    key = 'fixture_accounts'
    broken = [True]
    async def collect(ctx, targets):
        for target in targets:
            for n in range(10):
                ctx.observed(1)
                yield {'value': 'invalid' if broken[0] and n == 0 else n}
    monkeypatch.setitem(REGISTRY, key, replace(REGISTRY[key], function=collect, schema=Row,
        key=['value'], writes=['raw.recovery_rows']))
    sync(rt.db)
    for index in range(3):
        if index == 2:
            broken[0] = False
        _, run = await bound(rt, key)
        with psycopg.connect(databases['admin_control']) as conn:
            conn.execute("UPDATE control.run SET resolved_config=resolved_config-'fixture' WHERE id=%s", (run['id'],))
        await rt.execute(run['id'])
        rt.settle(run['id'])
        state = await poll(rt, run)
        assert state['run']['status'] == 'succeeded'
        assert state['receipts'][0]['row_coverage'] == ('full' if index == 2 else 'partial')
        warnings = rt.db.all("SELECT * FROM control.alert WHERE class='partial_coverage'")
        assert len(warnings) == (0 if index == 0 else 1)
        if index == 2:
            assert warnings[0]['resolved_by'] == 'system:recovery'
        rt.cycles.close(run['cycle_id'])


@pytest.mark.parametrize('legacy', [False, True])
async def test_declared_exclusions_stay_full_across_scheduled_runs(rt, databases, monkeypatch, legacy):
    from psycopg.types.json import Jsonb

    key = 'fixture_accounts'

    async def collect(ctx, targets):
        for target in targets:
            ctx.observed(2)
            ctx.exclude({'position': 1}, reason='unsupported_item:Episode')
            yield {'value': 1}

    monkeypatch.setitem(REGISTRY, key, replace(
        REGISTRY[key], function=collect, schema=Row, key=['value'],
        writes=['raw.exclusion_fixture'], exclusion_reasons=('unsupported_item:Episode',),
    ))
    sync(rt.db)
    for _ in range(2):
        _, run = await bound(rt, key)
        with psycopg.connect(databases['admin_control']) as conn:
            conn.execute("UPDATE control.run SET resolved_config=resolved_config-'fixture' WHERE id=%s", (run['id'],))
        await rt.execute(run['id'])
        if legacy:
            for page in rt.db.all("SELECT id,attrs FROM control.run_event WHERE run_id=%s AND event_type='page_published'", (run['id'],)):
                attrs = page['attrs']
                attrs.pop('accounting_version')
                attrs['rejected'] += sum(attrs['exclusions'].values())
                with psycopg.connect(databases['admin_control']) as conn:
                    conn.execute('UPDATE control.run_event SET attrs=%s WHERE id=%s', (Jsonb(attrs), page['id']))
        rt.settle(run['id'])
        state = await poll(rt, run)
        assert state['run']['status'] == 'succeeded'
        assert state['run']['coverage'] == 'full'
        assert state['run']['rows_rejected'] == '0'
        assert all(r['rows_rejected'] == '0' and r['row_coverage'] == 'full' for r in state['receipts'])
        assert sum(int(r['rows_excluded']) for r in state['receipts']) > 0
        assert not rt.db.all("SELECT 1 FROM control.dump WHERE run_id=%s AND kind='rejected'", (run['id'],))
        assert not rt.db.all("SELECT 1 FROM control.alert WHERE run_id=%s AND class='partial_coverage'", (run['id'],))
        rt.cycles.close(run['cycle_id'])


@pytest.mark.parametrize("excluded,has_uri", [(1, False), (2, False), (3, False), (1, True)])
async def test_spotify_unavailable_items_preserve_run_counts_and_health(rt, databases, monkeypatch, excluded, has_uri):
    from copy import deepcopy

    from psycopg.types.json import Jsonb
    from test_playlist_publication import envelope

    fixture_ctx, member, entity, tracks, body = envelope("sp_playlist_page")
    # Keep the production declaration and runtime; this fixture supplies only the page surface.
    monkeypatch.setitem(REGISTRY, "sp_playlist", replace(REGISTRY["sp_playlist"], function=fixture_ctx.manifest.function))
    sync(rt.db)
    tracks[:] = [deepcopy(tracks[0]) for _ in range(3)]
    entity["content"]["totalCount"] = len(tracks)
    for position in range(excluded):
        if has_uri:
            tracks[position]["itemV2"]["data"]["__typename"] = "NotFound"
        else:
            tracks[position] = {"itemV2": {"__typename": "TrackResponseWrapper", "data": {"__typename": "NotFound"}}}
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.target")
        set_id = conn.execute("INSERT INTO control.target_set(kind,name) VALUES ('playlist','Coverage fixture') ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id").fetchone()[0]
        target_id = conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) VALUES (%s,'spotify',%s,%s,'resolved',now()) RETURNING id", (set_id, member.platform_account_id, member.handle)).fetchone()[0]
        conn.execute("INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) VALUES (%s,'playlist','fixture:spotify',%s)", (target_id, Jsonb({"cadence": "daily"})))
    defaults(rt, databases, "sp_playlist")
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body()))
    _, run = await bound(rt, "sp_playlist")
    await rt.execute(run["id"])
    state = await poll(rt, run)
    receipt = state["receipts"][0]
    assert state["run"]["coverage"] == ("partial" if has_uri or excluded > 1 else "full")
    assert receipt["rows_written"] == str(4 - excluded)
    assert receipt["rows_rejected"] == (str(excluded) if has_uri else "0")
    assert receipt["rows_excluded"] == ("0" if has_uri else str(excluded))
    misses = rt.db.all("SELECT attrs->>'path' AS path FROM control.run_event WHERE run_id=%s AND event_type='envelope_mismatch'", (run["id"],))
    assert [row["path"] for row in misses] == (["empty" if excluded == 3 else "exclusions"] if excluded > 1 else [])
    if excluded > 1:
        assert state["run"]["status"] == "partial"
        assert rt.db.one("SELECT 1 FROM control.alert WHERE run_id=%s AND class='envelope_mismatch'", (run["id"],))
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        snapshot = conn.execute("SELECT coverage, items_observed FROM raw.playlist_snapshots WHERE _run_id=%s", (run["id"],)).fetchone()
        assert snapshot == ("partial" if has_uri or excluded > 1 else "full", 3 - excluded)
