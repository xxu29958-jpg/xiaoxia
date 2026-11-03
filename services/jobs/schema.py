"""Version one of the local media-job journal; application state is never imported."""

SCHEMA_VERSION = 1
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
        intent_hash TEXT NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued','running','cancelling','cancelled','completed','failed')),
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        stage TEXT NOT NULL DEFAULT 'queued', done INTEGER NOT NULL DEFAULT 0,
        total INTEGER, attempt INTEGER NOT NULL DEFAULT 0,
        token TEXT, lease_until REAL, result TEXT, error TEXT,
        created REAL NOT NULL, updated REAL NOT NULL,
        UNIQUE(owner, request_key)
    )""",
    "CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, created)",
    """CREATE TABLE IF NOT EXISTS job_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL,
        at REAL NOT NULL, detail TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS job_events_lookup ON job_events(job_id, sequence)",
)
