CREATE TABLE repos (
  id          TEXT PRIMARY KEY,
  path        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  added_at    TEXT NOT NULL,
  last_seen   TEXT
);
CREATE TABLE port_leases (
  base        INTEGER PRIMARY KEY,
  repo_id     TEXT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  job_id      TEXT NOT NULL,
  leased_at   TEXT NOT NULL,
  UNIQUE (repo_id, job_id)
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
