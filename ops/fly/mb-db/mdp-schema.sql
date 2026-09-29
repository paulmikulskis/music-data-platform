-- MDP additions to a MusicBrainz generation database, applied by finalize.sql after every import
-- and idempotent: the generation record mb_spine reads in its snapshot, the URL tail-id lookup and
-- the pg_trgm index mb_resolve reads. The mirror is read-only to every MDP reader.
-- pg_trgm goes to public. The first live import created it in musicbrainz (first on the musicbrainz
-- role's search_path), where finalize's REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA musicbrainz takes
-- it from the reader on every later finalize; the grant at the end restores it wherever it lives.
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE SCHEMA IF NOT EXISTS mdp;

-- One row: the generation this database holds. validated_at is set only after verify passes, and
-- mb_spine lands only a validated generation newer than the last one it landed.
CREATE TABLE IF NOT EXISTS mdp.generation (
  generation text PRIMARY KEY,
  export_date timestamptz NOT NULL,
  replication_sequence bigint NOT NULL,
  schema_sequence integer NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT now(),
  validated_at timestamptz,
  counts jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- The trailing platform id of a URL: an Apple collection or song id (without the legacy "id"
-- prefix), otherwise the last path segment. mb_resolve narrows album URLs by it and checks the
-- whole URL in code.
CREATE OR REPLACE FUNCTION mdp.url_tail_id(url text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
  SELECT CASE WHEN url ~* '^https?://(music|itunes)\.apple\.com/'
              THEN substring(url from '/(?:id)?([0-9]+)/?(?:[?#].*)?$')
              ELSE substring(url from '/([A-Za-z0-9_-]+)/?(?:[?#].*)?$') END
$$;

CREATE INDEX IF NOT EXISTS url_idx_mdp_tail_id ON musicbrainz.url (mdp.url_tail_id(url));
-- The planner sizes a tail lookup from this index's statistics, which only an ANALYZE after the index
-- gathers; the import's ANALYZE ran before it.
ANALYZE musicbrainz.url;

-- The URLs of tracked platform domains and of the reference and social hosts, computed once per
-- generation: every spine query reads this instead of matching the pattern against all 22M URLs again
-- (six minutes a scan on the mirror). The pattern is musicbrainz.TRACKED_URL_SQL in the functions
-- package; a test keeps the two equal.
CREATE OR REPLACE FUNCTION mdp.tracked_url_pattern() RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT '((^https?://open\.spotify\.com/(?:intl-[a-z]+/)?(track|album|artist)/([A-Za-z0-9]{22})/?(?:[?#].*)?$)|(^https?://(?:music|itunes|geo\.music)\.apple\.com/[^/]*/(album|song|artist)/(?:[^/?#]+/)?(?:id)?([0-9]+)/?(?:[?#].*)?$)|(^https?://([a-z0-9-]+\.bandcamp\.com/(track|album)/[^/?#]+)/?(?:[?#].*)?$)|(^https?://soundcloud\.com/([^/?#]+(?:/[^/?#]+){0,2})/?(?:[?#].*)?$)|(^https?://(?:www\.)?wikidata\.org/wiki/(Q[1-9][0-9]*)/?(?:[?#].*)?$)|(^https?://(?:www\.)?discogs\.com/(artist|label)/([0-9]+)(?:-[^/?#]*)?/?(?:[?#].*)?$)|(^https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?(?:[?#].*)?$)|(^https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._]+)/?(?:[?#].*)?$)|(^https?://(?:www\.)?youtube\.com/(?:channel/(UC[A-Za-z0-9_-]{22})|@([A-Za-z0-9._-]+))/?(?:[?#].*)?$))'::text
$$;
DROP TABLE IF EXISTS mdp.tracked_url;
CREATE TABLE mdp.tracked_url AS SELECT id, url FROM musicbrainz.url WHERE url ~ mdp.tracked_url_pattern();
CREATE UNIQUE INDEX tracked_url_pkey ON mdp.tracked_url (id);
ANALYZE mdp.tracked_url;
-- mb_resolve starts its trigram candidates from the credit, so recording.name needs no trigram index.
-- gin_trgm_ops is qualified with pg_trgm's schema, so the build does not depend on search_path.
DO $$
DECLARE s text := (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
                   WHERE e.extname = 'pg_trgm');
BEGIN
  EXECUTE format('CREATE INDEX IF NOT EXISTS artist_credit_idx_mdp_name_trgm ON musicbrainz.artist_credit USING gin (name %I.gin_trgm_ops)', s);
END $$;

GRANT USAGE ON SCHEMA mdp TO mb_reader;
GRANT SELECT ON mdp.generation, mdp.tracked_url TO mb_reader;
GRANT EXECUTE ON FUNCTION mdp.url_tail_id(text), mdp.tracked_url_pattern() TO mb_reader;
-- similarity() and the % operator run as the caller, wherever pg_trgm is installed.
DO $$
DECLARE f regprocedure;
BEGIN
  FOR f IN SELECT d.objid::regprocedure FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid
           WHERE e.extname = 'pg_trgm' AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e'
  LOOP
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO mb_reader', f);
  END LOOP;
END $$;
