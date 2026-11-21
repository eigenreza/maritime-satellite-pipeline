-- PostGIS schema for the maritime satellite pipeline.
-- Geometries in the working projection (UTM 32N, EPSG:32632) unless the column name says otherwise.
-- Vessels are identified only by a salted hash; no MMSI, IMO, call sign or name is stored here.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS passes (
    pass_id          text PRIMARY KEY,
    platform         text NOT NULL,
    start_time       timestamptz NOT NULL,
    stop_time        timestamptz,
    relative_orbit   integer,
    orbit_direction  text,
    engine           text,                      -- snap | python
    footprint        geometry(MultiPolygon, 4326),
    valid_area       geometry(MultiPolygon, 32632), -- sea area actually imaged (inside the land mask)
    split            text                       -- not used: splits are derived from acquisition time
);

CREATE TABLE IF NOT EXISTS detections (
    det_id        bigserial PRIMARY KEY,
    pass_id       text NOT NULL REFERENCES passes(pass_id) ON DELETE CASCADE,
    det_idx       integer NOT NULL,
    geom          geometry(Point, 32632) NOT NULL,
    lon           double precision,
    lat           double precision,
    n_pixels      integer,
    fill          real,
    length_m      real,
    width_m       real,
    heading_deg   real,
    peak_vv_db    real,
    mean_vv_db    real,
    peak_vh_db    real,
    bg_vv_db      real,
    scr_db        real,
    wind_speed    real,
    wave_hs       real,
    ship_prob     real,         -- model output (section on the classifier)
    est_length_m  real,         -- model output
    UNIQUE (pass_id, det_idx)
);
CREATE INDEX IF NOT EXISTS detections_geom_idx ON detections USING gist (geom);

CREATE TABLE IF NOT EXISTS vessels (
    vessel_hash   text PRIMARY KEY,
    type_class    text,
    length_m      real,
    length_class  text
);

-- AIS positions near each pass (thinned to the time window that matching needs)
CREATE TABLE IF NOT EXISTS ais_positions (
    pass_id      text NOT NULL REFERENCES passes(pass_id) ON DELETE CASCADE,
    vessel_hash  text NOT NULL,
    t            timestamptz NOT NULL,
    geom         geometry(Point, 32632) NOT NULL,
    sog          real,
    cog          real
);
CREATE INDEX IF NOT EXISTS ais_positions_pass_idx ON ais_positions (pass_id, vessel_hash, t);

-- Each AIS ship interpolated to the radar acquisition time
CREATE TABLE IF NOT EXISTS ais_at_pass (
    pass_id       text NOT NULL REFERENCES passes(pass_id) ON DELETE CASCADE,
    vessel_hash   text NOT NULL,
    t_acq         timestamptz NOT NULL,
    geom          geometry(Point, 32632) NOT NULL,
    lon           double precision,
    lat           double precision,
    sog           real,
    cog           real,
    gap_s         real,            -- largest time gap of the AIS points used for interpolation
    in_scene      boolean,         -- inside the imaged sea area (radar could have seen it)
    wind_speed    real,
    wave_hs       real,
    PRIMARY KEY (pass_id, vessel_hash)
);
CREATE INDEX IF NOT EXISTS ais_at_pass_geom_idx ON ais_at_pass USING gist (geom);

CREATE TABLE IF NOT EXISTS matches (
    pass_id      text NOT NULL,
    det_id       bigint NOT NULL REFERENCES detections(det_id) ON DELETE CASCADE,
    vessel_hash  text NOT NULL,
    distance_m   real NOT NULL,
    PRIMARY KEY (pass_id, det_id)
);

-- Operations: run log (replay clock, not wall clock), quality checks and freshness
CREATE TABLE IF NOT EXISTS run_log (
    run_id        bigserial PRIMARY KEY,
    flow          text NOT NULL,
    task          text,
    replay_time   timestamptz,     -- time on the replay clock (inside the study period)
    status        text NOT NULL,   -- completed | failed | retried | recovered | alert
    attempt       integer DEFAULT 1,
    duration_s    real,
    records       bigint,
    bytes         bigint,
    message       text
);

CREATE TABLE IF NOT EXISTS quality_checks (
    product_id   text NOT NULL,
    product_type text NOT NULL,
    check_name   text NOT NULL,
    passed       boolean NOT NULL,
    value        text,
    PRIMARY KEY (product_id, check_name)
);

CREATE TABLE IF NOT EXISTS freshness (
    source        text NOT NULL,
    replay_time   timestamptz NOT NULL,
    latest_data   timestamptz,
    age_hours     real,
    limit_hours   real,
    status        text NOT NULL,   -- fresh | stale | missing
    PRIMARY KEY (source, replay_time)
);
