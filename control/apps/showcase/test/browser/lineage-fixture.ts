import type postgres from "postgres";
import { at, build } from "./fixtures";
import {
  countInputs,
  buildStamp,
  relationBuildKey,
} from "../../server/relation-counts";
export async function seedLineage(wh: postgres.Sql, db: postgres.Sql) {
  await db`INSERT INTO control.dbt_job(job_id,runner,cadence,scope,due_hour,timezone)
    VALUES ('trace-daily','core','daily','global',2,'UTC') ON CONFLICT(job_id) DO NOTHING`;
  await wh`DELETE FROM marts.mart_shazam_chart_daily WHERE chart='shazam:city:us:new-york' AND chart_date=${at.slice(0, 10)}::date`;
  await wh`INSERT INTO marts.mart_shazam_chart_daily(chart,chart_date,position,title_text,artist_text)
    VALUES ('shazam:city:us:new-york',${at.slice(0, 10)}::date,1,'Night tide','Test recording'),
    ('shazam:city:us:new-york',${at.slice(0, 10)}::date,2,'Another song','Another recording')`;

  await db`INSERT INTO control.streamline(source_key,layer,cadence_tag) VALUES ('sp_playlist','bronze','daily') ON CONFLICT (source_key) DO NOTHING`;
  await db`INSERT INTO control.cycle(id,cadence,scope,opened_by_dbt_run_id,status,closed_at,close_no)
    VALUES (${build("x").cycle_id},'daily','global','trace-fixture','closed',${at},41) ON CONFLICT(id) DO NOTHING`;
  await db`INSERT INTO control.run(id,kind,work_key,scope,streamline_id,warehouse_id,status)
    SELECT '00000000-0000-4000-8000-000000000701','invoke','trace-fixture','global',s.id,w.id,'succeeded'
    FROM control.streamline s CROSS JOIN control.warehouse w WHERE s.source_key='sp_playlist' AND w.is_production ON CONFLICT(id) DO NOTHING`;
  await db`INSERT INTO control.dump(id,kind,run_id,uri_prefix,scope,close_no)
    VALUES ('00000000-0000-4000-8000-000000000702','output','00000000-0000-4000-8000-000000000701','fixture/trace','global',41) ON CONFLICT(id) DO NOTHING`;
  await db`INSERT INTO control.load(dump_id,warehouse_id,target_table,status,loaded_at)
    SELECT '00000000-0000-4000-8000-000000000702',id,'raw.playlist_snapshots','loaded',${at} FROM control.warehouse WHERE is_production ON CONFLICT DO NOTHING`;
  await db`INSERT INTO control.cycle_input(cycle_id,dump_id,phase)
    VALUES (${build("x").cycle_id},'00000000-0000-4000-8000-000000000702','bronze') ON CONFLICT DO NOTHING`;
  await wh`ALTER TABLE marts.mart_playlist_events ADD COLUMN IF NOT EXISTS snapshot_id text`;
  // Disposable acceptance data. Private columns are deliberate negative cases.
  await wh`DELETE FROM marts.mart_playlist_profile WHERE playlist_id='fixture-list'`;
  await wh`INSERT INTO marts.mart_playlist_profile(platform,playlist_id,variant,stream,snapshot_id,observed_at,title,followers,description,owner_id,owner_name,source_keys,learning_eligible,resale_permitted)
    VALUES ('spotify','fixture-list','default','full','trace-snapshot',${at},'Night list',1200,'personal-text-sentinel','creator-sentinel','commenter-sentinel','["sp_playlist"]',false,false)`;
  await wh`DELETE FROM marts.mart_playlist_events WHERE playlist_id='fixture-list'`;
  await wh`INSERT INTO marts.mart_playlist_events(platform,playlist_id,variant,stream,occurrence_key,interval_id,event_type,observed_at,snapshot_id,source_keys,learning_eligible,resale_permitted)
    VALUES ('spotify','fixture-list','default','full','trace-occurrence','trace-interval','add',${at},'trace-snapshot','["sp_playlist"]',false,false)`;
  await wh`DELETE FROM staging.stg_playlist__snapshots WHERE snapshot_id='trace-snapshot'`;
  await wh`INSERT INTO staging.stg_playlist__snapshots(platform,playlist_id,variant,stream,snapshot_id,observed_at,_source_key,_run_id,_dump_id)
    VALUES ('spotify','fixture-list','default','full','trace-snapshot',${at},'sp_playlist','00000000-0000-4000-8000-000000000701','00000000-0000-4000-8000-000000000702')`;
  for (const relation of [
    "marts.mart_playlist_profile",
    "marts.mart_playlist_events",
    "marts.mart_shazam_chart_daily",
  ]) {
    await wh`INSERT INTO marts._build(relation,cycle_id,close_no,built_at) VALUES (${relation},${build("x").cycle_id},41,${at}) ON CONFLICT(relation) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,close_no=EXCLUDED.close_no,built_at=EXCLUDED.built_at`;
    const input = countInputs.find((item) => item.relation === relation)!;
    const [total] = await wh.unsafe(
      `SELECT count(*)::text AS count FROM ${relation}`,
    );
    const stamp = buildStamp.parse({ ...build("x"), relation });
    await db`INSERT INTO control.showcase_relation_count(warehouse_id,relation,build_key,captured_at,row_count,basis,input_hash)
      SELECT id,${relation},${relationBuildKey(stamp)},${at},${total.count},'exact',${input.input_hash} FROM control.warehouse WHERE is_production
      ON CONFLICT(warehouse_id,relation,build_key) DO UPDATE SET row_count=EXCLUDED.row_count,input_hash=EXCLUDED.input_hash`;
  }
}
