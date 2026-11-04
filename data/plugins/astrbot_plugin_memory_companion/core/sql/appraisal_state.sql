CREATE TABLE IF NOT EXISTS appraisal_objects (
    owner TEXT NOT NULL, id TEXT NOT NULL, record TEXT NOT NULL,
    PRIMARY KEY(owner,id)
);
CREATE TABLE IF NOT EXISTS appraisal_revisions (
    owner TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, record TEXT NOT NULL,
    PRIMARY KEY(owner,id,revision),
    FOREIGN KEY(owner,id) REFERENCES appraisal_objects(owner,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS appraisal_operations (
    owner TEXT NOT NULL, operation TEXT NOT NULL, request_hash TEXT NOT NULL, receipt TEXT NOT NULL,
    PRIMARY KEY(owner,operation)
);
CREATE TRIGGER IF NOT EXISTS appraisal_source_deleted AFTER DELETE ON timeline BEGIN
    DELETE FROM appraisal_objects WHERE (owner,id) IN (
        SELECT owner,id FROM appraisal_revisions
        WHERE json_extract(record,'$.source.capture.source_ref') = 'timeline:' || OLD.id
    );
END;
