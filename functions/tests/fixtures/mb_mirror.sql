-- A small MusicBrainz mirror: the tables mb_spine, mb_resolve and mb_artist_catalog read, with their upstream column
-- names and types, and rows covering every identity path in design. Load it into an empty
-- database, then ops/fly/mb-db/mdp-schema.sql, then insert the mdp.generation row.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mb_reader') THEN CREATE ROLE mb_reader LOGIN PASSWORD 'mb_reader'; END IF;
END $$;
CREATE SCHEMA musicbrainz;
CREATE TABLE musicbrainz.replication_control (id integer PRIMARY KEY, current_schema_sequence integer, current_replication_sequence integer, last_replication_date timestamptz);
CREATE TABLE musicbrainz.artist (id integer PRIMARY KEY, gid uuid NOT NULL, name varchar NOT NULL, sort_name varchar NOT NULL, type integer, comment varchar DEFAULT '', edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now());
CREATE TABLE musicbrainz.artist_credit (id integer PRIMARY KEY, name varchar NOT NULL, artist_count smallint NOT NULL, ref_count integer DEFAULT 0, created timestamptz DEFAULT now(), edits_pending integer DEFAULT 0, gid uuid NOT NULL);
CREATE TABLE musicbrainz.artist_credit_name (artist_credit integer NOT NULL, position smallint NOT NULL, artist integer NOT NULL, name varchar NOT NULL, join_phrase text DEFAULT '', PRIMARY KEY (artist_credit, position));
CREATE TABLE musicbrainz.recording (id integer PRIMARY KEY, gid uuid NOT NULL, name varchar NOT NULL, artist_credit integer NOT NULL, length integer, comment varchar DEFAULT '', edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now(), video boolean DEFAULT false);
CREATE TABLE musicbrainz.release_group (id integer PRIMARY KEY, gid uuid NOT NULL, name varchar NOT NULL, artist_credit integer NOT NULL, type integer, comment varchar DEFAULT '', edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now());
CREATE TABLE musicbrainz.release (id integer PRIMARY KEY, gid uuid NOT NULL, name varchar NOT NULL, artist_credit integer NOT NULL, release_group integer NOT NULL, status integer, packaging integer, language integer, script integer, barcode varchar, comment varchar DEFAULT '', edits_pending integer DEFAULT 0, quality smallint DEFAULT -1, last_updated timestamptz DEFAULT now());
CREATE TABLE musicbrainz.medium (id integer PRIMARY KEY, release integer NOT NULL, position integer NOT NULL, format integer, name varchar DEFAULT '', edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now(), track_count integer DEFAULT 0, gid uuid NOT NULL);
CREATE TABLE musicbrainz.track (id integer PRIMARY KEY, gid uuid NOT NULL, recording integer NOT NULL, medium integer NOT NULL, position integer NOT NULL, number text NOT NULL, name varchar NOT NULL, artist_credit integer NOT NULL, length integer, edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now(), is_data_track boolean DEFAULT false);
CREATE TABLE musicbrainz.isrc (id integer PRIMARY KEY, recording integer NOT NULL, isrc character(12) NOT NULL, edits_pending integer DEFAULT 0, created timestamptz DEFAULT now());
CREATE TABLE musicbrainz.url (id integer PRIMARY KEY, gid uuid NOT NULL, url text NOT NULL UNIQUE, edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now());
CREATE TABLE musicbrainz.link_type (id integer PRIMARY KEY, parent integer, child_order integer DEFAULT 0, gid uuid NOT NULL, entity_type0 varchar NOT NULL, entity_type1 varchar NOT NULL, name varchar NOT NULL, description text, link_phrase varchar DEFAULT '', reverse_link_phrase varchar DEFAULT '', long_link_phrase varchar DEFAULT '', last_updated timestamptz DEFAULT now(), is_deprecated boolean DEFAULT false, has_dates boolean DEFAULT true, entity0_cardinality smallint DEFAULT 0, entity1_cardinality smallint DEFAULT 0);
CREATE TABLE musicbrainz.link (id integer PRIMARY KEY, link_type integer NOT NULL, begin_date_year smallint, begin_date_month smallint, begin_date_day smallint, end_date_year smallint, end_date_month smallint, end_date_day smallint, attribute_count integer DEFAULT 0, created timestamptz DEFAULT now(), ended boolean DEFAULT false);
CREATE TABLE musicbrainz.l_recording_url (id integer PRIMARY KEY, link integer NOT NULL, entity0 integer NOT NULL, entity1 integer NOT NULL, edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now(), link_order integer DEFAULT 0, entity0_credit text DEFAULT '', entity1_credit text DEFAULT '');
CREATE TABLE musicbrainz.l_release_url (LIKE musicbrainz.l_recording_url INCLUDING DEFAULTS, PRIMARY KEY (id));
CREATE TABLE musicbrainz.l_artist_url (LIKE musicbrainz.l_recording_url INCLUDING DEFAULTS, PRIMARY KEY (id));
CREATE TABLE musicbrainz.recording_gid_redirect (gid uuid PRIMARY KEY, new_id integer NOT NULL, created timestamptz DEFAULT now());
CREATE TABLE musicbrainz.release_gid_redirect (LIKE musicbrainz.recording_gid_redirect INCLUDING DEFAULTS, PRIMARY KEY (gid));
CREATE TABLE musicbrainz.artist_gid_redirect (LIKE musicbrainz.recording_gid_redirect INCLUDING DEFAULTS, PRIMARY KEY (gid));
-- Catalog extension: labels, artist-label and label-label relationships, IPI and ISNI codes.
CREATE TABLE musicbrainz.label (id integer PRIMARY KEY, gid uuid NOT NULL, name varchar NOT NULL, begin_date_year smallint, begin_date_month smallint, begin_date_day smallint, end_date_year smallint, end_date_month smallint, end_date_day smallint, label_code integer, type integer, area integer, comment varchar DEFAULT '', edits_pending integer DEFAULT 0, last_updated timestamptz DEFAULT now(), ended boolean DEFAULT false);
CREATE TABLE musicbrainz.l_artist_label (LIKE musicbrainz.l_recording_url INCLUDING DEFAULTS, PRIMARY KEY (id));
CREATE TABLE musicbrainz.l_label_label (LIKE musicbrainz.l_recording_url INCLUDING DEFAULTS, PRIMARY KEY (id));
CREATE TABLE musicbrainz.artist_ipi (artist integer NOT NULL, ipi character(11) NOT NULL, edits_pending integer DEFAULT 0, created timestamptz DEFAULT now(), PRIMARY KEY (artist, ipi));
CREATE TABLE musicbrainz.artist_isni (artist integer NOT NULL, isni character(16) NOT NULL, edits_pending integer DEFAULT 0, created timestamptz DEFAULT now(), PRIMARY KEY (artist, isni));
-- Release events, the CC0 core dates mb_artist_catalog reads (release_group_meta is supplementary).
CREATE TABLE musicbrainz.release_country (release integer NOT NULL, country integer NOT NULL, date_year smallint, date_month smallint, date_day smallint, PRIMARY KEY (release, country));
CREATE TABLE musicbrainz.release_unknown_country (release integer PRIMARY KEY, date_year smallint, date_month smallint, date_day smallint);
-- Release group kinds (core reference tables), with MusicBrainz's own ids and names.
CREATE TABLE musicbrainz.release_group_primary_type (id integer PRIMARY KEY, name varchar NOT NULL, parent integer, child_order integer DEFAULT 0, description text, gid uuid NOT NULL);
CREATE TABLE musicbrainz.release_group_secondary_type (id integer PRIMARY KEY, name text NOT NULL, parent integer, child_order integer DEFAULT 0, description text, gid uuid NOT NULL);
CREATE TABLE musicbrainz.release_group_secondary_type_join (release_group integer NOT NULL, secondary_type integer NOT NULL, created timestamptz DEFAULT now(), PRIMARY KEY (release_group, secondary_type));
CREATE INDEX isrc_idx_isrc ON musicbrainz.isrc (isrc);
CREATE INDEX track_idx_recording ON musicbrainz.track (recording);
CREATE INDEX medium_idx_release_position ON musicbrainz.medium (release, position);
-- Stock MusicBrainz indexes the lookups read: a credit's recordings, an album URL's release links and the
-- relationships of the entities an answer touched.
CREATE INDEX recording_idx_artist_credit ON musicbrainz.recording (artist_credit);
-- Stock indexes the catalog lookup reads: an artist's credits, a credit's release groups, a group's releases.
CREATE INDEX artist_credit_name_idx_artist ON musicbrainz.artist_credit_name (artist);
CREATE INDEX release_group_idx_artist_credit ON musicbrainz.release_group (artist_credit);
CREATE INDEX release_idx_release_group ON musicbrainz.release (release_group);
CREATE INDEX isrc_idx_recording ON musicbrainz.isrc (recording);
CREATE INDEX l_release_url_idx_entity1 ON musicbrainz.l_release_url (entity1);
CREATE UNIQUE INDEX l_recording_url_idx_uniq ON musicbrainz.l_recording_url (entity0, entity1, link, link_order);
CREATE UNIQUE INDEX l_release_url_idx_uniq ON musicbrainz.l_release_url (entity0, entity1, link, link_order);
CREATE UNIQUE INDEX l_artist_url_idx_uniq ON musicbrainz.l_artist_url (entity0, entity1, link, link_order);

INSERT INTO musicbrainz.release_group_primary_type (id, name, gid) VALUES
  (1, 'Album', gen_random_uuid()), (2, 'Single', gen_random_uuid()), (3, 'EP', gen_random_uuid()),
  (11, 'Other', gen_random_uuid()), (12, 'Broadcast', gen_random_uuid());
INSERT INTO musicbrainz.release_group_secondary_type (id, name, gid) VALUES
  (1, 'Compilation', gen_random_uuid()), (2, 'Soundtrack', gen_random_uuid()), (3, 'Spokenword', gen_random_uuid()),
  (4, 'Interview', gen_random_uuid()), (5, 'Audiobook', gen_random_uuid()), (6, 'Live', gen_random_uuid()),
  (7, 'Remix', gen_random_uuid()), (8, 'DJ-mix', gen_random_uuid()), (9, 'Mixtape/Street', gen_random_uuid()),
  (10, 'Demo', gen_random_uuid()), (11, 'Audio drama', gen_random_uuid()), (12, 'Field recording', gen_random_uuid());
INSERT INTO musicbrainz.replication_control VALUES (1, 31, 189207, '2026-09-23 00:21:21+00');
INSERT INTO musicbrainz.artist (id, gid, name, sort_name, type) VALUES
  (1, '00000000-0000-4000-a000-000000000001', 'Fixture Artist Alpha', 'Alpha, Fixture Artist', 1),
  (2, '00000000-0000-4000-a000-000000000002', 'Fixture Artist Beta', 'Beta, Fixture Artist', 1),
  (3, '00000000-0000-4000-a000-000000000003', 'The Twins', 'Twins, The', 2);
INSERT INTO musicbrainz.artist_credit (id, name, artist_count, gid) VALUES
  (1, 'Fixture Artist Alpha', 1, '00000000-0000-4000-ac00-000000000001'),
  (2, 'Fixture Artist Beta', 1, '00000000-0000-4000-ac00-000000000002'),
  (3, 'The Twins', 1, '00000000-0000-4000-ac00-000000000003');
INSERT INTO musicbrainz.artist_credit_name VALUES (1, 0, 1, 'Fixture Artist Alpha', ''), (2, 0, 2, 'Fixture Artist Beta', ''), (3, 0, 3, 'The Twins', '');
-- 101: Spotify track URL and ISRC (exact in SQL). 102: on a Spotify album only (release candidates).
-- 104: no URL and no ISRC (trigram). 106/107: two recordings of one title and length on one Apple
-- album (ambiguous). 108: Apple song URL and ISRC (exact in SQL). 109: a merged-away duplicate.
INSERT INTO musicbrainz.recording (id, gid, name, artist_credit, length) VALUES
  (101, '00000000-0000-4000-b000-000000000101', 'Fixture item 56802', 1, 202460),
  (102, '00000000-0000-4000-b000-000000000102', 'Synthetic Song Alpha', 1, 200455),
  (104, '00000000-0000-4000-b000-000000000104', 'Synthetic Song Beta', 2, 200690),
  (106, '00000000-0000-4000-b000-000000000106', 'Twin', 3, 180000),
  (107, '00000000-0000-4000-b000-000000000107', 'Twin', 3, 180500),
  (108, '00000000-0000-4000-b000-000000000108', 'Synthetic Song Gamma', 2, 239560);
INSERT INTO musicbrainz.recording_gid_redirect (gid, new_id) VALUES
  ('00000000-0000-4000-b000-000000000109', 101),
  ('00000000-0000-4000-b000-000000000110', 104);
INSERT INTO musicbrainz.release_group (id, gid, name, artist_credit, type) VALUES
  (301, '00000000-0000-4000-c000-000000000301', 'Synthetic Album Alpha', 1, 1),
  (302, '00000000-0000-4000-c000-000000000302', 'Twin EP', 3, 3),
  (303, '00000000-0000-4000-c000-000000000303', 'Synthetic Album Beta', 2, 1);
INSERT INTO musicbrainz.release (id, gid, name, artist_credit, release_group, status, barcode) VALUES
  (201, '00000000-0000-4000-d000-000000000201', 'Synthetic Album Alpha', 1, 301, 1, '900000000001'),
  (202, '00000000-0000-4000-d000-000000000202', 'Twin EP', 3, 302, 1, NULL),
  (203, '00000000-0000-4000-d000-000000000203', 'Synthetic Album Beta', 2, 303, 1, '900000000002');
INSERT INTO musicbrainz.medium (id, release, position, format, track_count, gid) VALUES
  (401, 201, 1, 1, 2, '00000000-0000-4000-e000-000000000401'),
  (402, 202, 1, 1, 2, '00000000-0000-4000-e000-000000000402'),
  (403, 203, 1, 1, 2, '00000000-0000-4000-e000-000000000403');
INSERT INTO musicbrainz.track (id, gid, recording, medium, position, number, name, artist_credit, length) VALUES
  (501, '00000000-0000-4000-f000-000000000501', 102, 401, 1, '1', 'Synthetic Song Alpha', 1, 200455),
  (502, '00000000-0000-4000-f000-000000000502', 101, 401, 2, '2', 'Fixture item 56802', 1, 202460),
  (503, '00000000-0000-4000-f000-000000000503', 106, 402, 1, '1', 'Twin', 3, 180000),
  (504, '00000000-0000-4000-f000-000000000504', 107, 402, 2, '2', 'Twin', 3, 180500),
  (505, '00000000-0000-4000-f000-000000000505', 104, 403, 1, '1', 'Synthetic Song Beta', 2, 200690),
  (506, '00000000-0000-4000-f000-000000000506', 108, 403, 2, '2', 'Synthetic Song Gamma', 2, 239560);
INSERT INTO musicbrainz.isrc (id, recording, isrc) VALUES
  (1, 101, 'USXXX2600001'), (2, 102, 'USXXX2600002'), (3, 108, 'USXXX2600003');
INSERT INTO musicbrainz.link_type (id, gid, entity_type0, entity_type1, name) VALUES
  (1, '00000000-0000-4000-9000-000000000001', 'recording', 'url', 'free streaming'),
  (2, '00000000-0000-4000-9000-000000000002', 'release', 'url', 'streaming'),
  (3, '00000000-0000-4000-9000-000000000003', 'artist', 'url', 'free streaming'),
  (4, '00000000-0000-4000-9000-000000000004', 'release', 'url', 'discogs'),
  (5, '00000000-0000-4000-9000-000000000005', 'artist', 'url', 'wikidata'),
  (6, '00000000-0000-4000-9000-000000000006', 'artist', 'url', 'social network'),
  (11, '00000000-0000-4000-9000-000000000011', 'artist', 'label', 'recording contract'),
  (12, '00000000-0000-4000-9000-000000000012', 'label', 'label', 'label ownership'),
  (13, '00000000-0000-4000-9000-000000000013', 'label', 'label', 'imprint'),
  (14, '00000000-0000-4000-9000-000000000014', 'label', 'label', 'label distribution'),
  (15, '00000000-0000-4000-9000-000000000015', 'label', 'label', 'label rename'),
  (16, '00000000-0000-4000-9000-000000000016', 'artist', 'label', 'producer position at');
INSERT INTO musicbrainz.link (id, link_type) VALUES (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6);
-- Artist 2's current recording contract (dated), an ended one with a label since renamed, and a
-- producer position that never lands; the imprint's parent, its owner, its distributor and the rename.
INSERT INTO musicbrainz.link (id, link_type, begin_date_year, begin_date_month, begin_date_day, end_date_year, ended) VALUES
  (11, 11, 2019, 3, 1, NULL, false), (12, 11, 2012, NULL, NULL, 2018, true), (13, 12, 2004, NULL, NULL, NULL, false),
  (14, 13, 2010, NULL, NULL, NULL, false), (15, 14, NULL, NULL, NULL, NULL, false), (16, 15, 2018, NULL, NULL, NULL, false),
  (17, 16, 2015, NULL, NULL, NULL, false), (18, 12, 2001, NULL, NULL, NULL, false);
INSERT INTO musicbrainz.label (id, gid, name, type, label_code, begin_date_year) VALUES
  (701, '00000000-0000-4000-7000-000000000701', 'Fixture Imprint', 4, NULL, 2010),
  (702, '00000000-0000-4000-7000-000000000702', 'Fixture Records', 4, 12345, 1990),
  (703, '00000000-0000-4000-7000-000000000703', 'Fixture Music Group', 2, NULL, 1985),
  (704, '00000000-0000-4000-7000-000000000704', 'Fixture Distribution', 7, NULL, 1995),
  (705, '00000000-0000-4000-7000-000000000705', 'Fixture Old Records', 4, NULL, 2000),
  (706, '00000000-0000-4000-7000-000000000706', 'Unrelated Subsidiary', 4, NULL, 2001),
  (707, '00000000-0000-4000-7000-000000000707', 'Employer Label', 4, NULL, 2005);
INSERT INTO musicbrainz.l_artist_label (id, link, entity0, entity1) VALUES (1, 11, 2, 701), (2, 12, 2, 705), (3, 17, 2, 707);
INSERT INTO musicbrainz.l_label_label (id, link, entity0, entity1) VALUES
  (1, 14, 702, 701), (2, 13, 703, 702), (3, 15, 704, 702), (4, 16, 705, 702), (5, 18, 703, 706);
INSERT INTO musicbrainz.artist_ipi (artist, ipi) VALUES (2, '00000000001');
INSERT INTO musicbrainz.artist_isni (artist, isni) VALUES (2, '0000000123456789');
INSERT INTO musicbrainz.url (id, gid, url) VALUES
  (1001, '00000000-0000-4000-8000-000000001001', 'https://open.spotify.com/track/SynthTrack000000000001'),
  (1002, '00000000-0000-4000-8000-000000001002', 'https://open.spotify.com/album/SynthAlbum000000000001'),
  (1003, '00000000-0000-4000-8000-000000001003', 'https://music.apple.com/us/album/twin-ep/1500000202'),
  (1004, '00000000-0000-4000-8000-000000001004', 'https://music.apple.com/us/song/1600000108'),
  (1005, '00000000-0000-4000-8000-000000001005', 'https://open.spotify.com/artist/SynthArtist00000000001'),
  (1006, '00000000-0000-4000-8000-000000001006', 'https://www.discogs.com/release/1'),
  (1007, '00000000-0000-4000-8000-000000001007', 'https://www.wikidata.org/wiki/Q9000002'),
  (1008, '00000000-0000-4000-8000-000000001008', 'https://www.instagram.com/fixture.act/');
INSERT INTO musicbrainz.l_recording_url (id, link, entity0, entity1) VALUES (1, 1, 101, 1001), (2, 1, 108, 1004);
INSERT INTO musicbrainz.l_release_url (id, link, entity0, entity1) VALUES (1, 2, 201, 1002), (2, 2, 202, 1003), (3, 4, 203, 1006);
INSERT INTO musicbrainz.l_artist_url (id, link, entity0, entity1) VALUES (1, 3, 2, 1005), (2, 5, 2, 1007), (3, 6, 2, 1008);
DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO mb_reader', current_database()); END $$;
GRANT USAGE ON SCHEMA musicbrainz TO mb_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA musicbrainz TO mb_reader;
