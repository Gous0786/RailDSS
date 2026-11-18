-- KPI snapshots as a time series (spec FR-KPI-08; issue #81). TimescaleDB hypertable on time.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS kpi_snapshot (
    time timestamptz NOT NULL,
    scope text NOT NULL,
    arm text NOT NULL,
    kpi text NOT NULL,
    value double precision NOT NULL,
    policy_version text NOT NULL,
    network_version text NOT NULL
);

SELECT create_hypertable('kpi_snapshot', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS kpi_snapshot_scope_idx ON kpi_snapshot (scope, kpi, time DESC);
