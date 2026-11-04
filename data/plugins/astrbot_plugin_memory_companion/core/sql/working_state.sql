CREATE TABLE IF NOT EXISTS working_objects (
    owner TEXT NOT NULL, id TEXT NOT NULL, record TEXT NOT NULL,
    PRIMARY KEY(owner, id)
);
CREATE TABLE IF NOT EXISTS working_revisions (
    owner TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, record TEXT NOT NULL,
    PRIMARY KEY(owner, id, revision),
    FOREIGN KEY(owner, id) REFERENCES working_objects(owner, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS working_operations (
    owner TEXT NOT NULL, operation TEXT NOT NULL, request_hash TEXT NOT NULL, receipt TEXT NOT NULL,
    PRIMARY KEY(owner, operation)
);
CREATE TABLE IF NOT EXISTS working_imports (
    owner TEXT NOT NULL, source_key TEXT NOT NULL, source_hash TEXT NOT NULL, receipt TEXT NOT NULL,
    PRIMARY KEY(owner, source_key)
);
CREATE TABLE IF NOT EXISTS working_names (
    owner TEXT NOT NULL, name_hash TEXT NOT NULL, id TEXT NOT NULL,
    PRIMARY KEY(owner, name_hash)
);
CREATE TRIGGER IF NOT EXISTS working_source_deleted AFTER DELETE ON timeline BEGIN
    DELETE FROM working_objects
    WHERE json_extract(record, '$.source.capture.source_ref') = 'timeline:' || OLD.id;
    DELETE FROM working_revisions
    WHERE json_extract(record, '$.source.capture.source_ref') = 'timeline:' || OLD.id;
END;
