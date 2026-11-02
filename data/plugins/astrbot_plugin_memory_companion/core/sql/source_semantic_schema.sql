CREATE TABLE IF NOT EXISTS source_semantic_revision (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO source_semantic_revision(singleton,revision) VALUES(1,0);

CREATE TABLE IF NOT EXISTS source_semantic_generations (
    generation TEXT PRIMARY KEY,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('building','ready','paused','failed','retired')),
    base_sequence INTEGER NOT NULL,
    scan_cursor TEXT NOT NULL DEFAULT '',
    checkpoint_sequence INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_semantic_documents (
    document_id TEXT PRIMARY KEY,
    generation TEXT NOT NULL,
    view_kind TEXT NOT NULL CHECK(view_kind IN ('fragment','window')),
    anchor_source_id TEXT NOT NULL,
    anchor_source_version TEXT NOT NULL,
    char_start INTEGER NOT NULL CHECK(char_start>=0),
    char_end INTEGER NOT NULL CHECK(char_end>=char_start),
    input_hash TEXT NOT NULL,
    source_change_sequence INTEGER NOT NULL,
    vector_blob BLOB,
    vector_dimension INTEGER,
    state TEXT NOT NULL CHECK(state IN ('pending','ready','stale')),
    updated_at TEXT NOT NULL,
    UNIQUE(generation,anchor_source_id,view_kind,char_start,char_end),
    FOREIGN KEY(generation) REFERENCES source_semantic_generations(generation) ON DELETE CASCADE,
    CHECK((vector_blob IS NULL AND vector_dimension IS NULL) OR
          (vector_blob IS NOT NULL AND vector_dimension>0))
);
CREATE INDEX IF NOT EXISTS idx_source_semantic_document_anchor
    ON source_semantic_documents(generation,anchor_source_id,view_kind);

CREATE TABLE IF NOT EXISTS source_semantic_dependencies (
    document_id TEXT NOT NULL,
    dependency_order INTEGER NOT NULL CHECK(dependency_order>=0),
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    char_start INTEGER NOT NULL CHECK(char_start>=0),
    char_end INTEGER NOT NULL CHECK(char_end>=char_start),
    role TEXT NOT NULL CHECK(role IN ('anchor','context')),
    PRIMARY KEY(document_id,dependency_order),
    FOREIGN KEY(document_id) REFERENCES source_semantic_documents(document_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_source_semantic_dependency_source
    ON source_semantic_dependencies(source_id,source_version,document_id);

CREATE TABLE IF NOT EXISTS source_semantic_dirty (
    generation TEXT NOT NULL,
    source_id TEXT NOT NULL,
    change_sequence INTEGER NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
    old_present INTEGER NOT NULL CHECK(old_present IN (0,1)),
    old_scope TEXT,
    old_session_id TEXT,
    old_event_type TEXT,
    old_subject_id TEXT,
    old_object_id TEXT,
    old_owner_bot_id TEXT,
    old_bot_id TEXT,
    old_platform TEXT,
    old_persona_id TEXT,
    old_participant_user_id TEXT,
    old_occurred_at TEXT,
    old_created_at TEXT,
    new_source_id TEXT,
    new_present INTEGER NOT NULL CHECK(new_present IN (0,1)),
    new_scope TEXT,
    new_session_id TEXT,
    new_event_type TEXT,
    new_subject_id TEXT,
    new_object_id TEXT,
    new_owner_bot_id TEXT,
    new_bot_id TEXT,
    new_platform TEXT,
    new_persona_id TEXT,
    new_participant_user_id TEXT,
    new_occurred_at TEXT,
    new_created_at TEXT,
    PRIMARY KEY(generation,source_id),
    FOREIGN KEY(generation) REFERENCES source_semantic_generations(generation) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_source_semantic_dirty_position
    ON source_semantic_dirty(generation,old_scope,old_session_id,old_occurred_at,old_created_at);
CREATE INDEX IF NOT EXISTS idx_source_semantic_dirty_new_position
    ON source_semantic_dirty(generation,new_scope,new_session_id,new_occurred_at,new_created_at);

CREATE TABLE IF NOT EXISTS source_semantic_order_changes (
    generation TEXT NOT NULL,
    change_sequence INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    old_present INTEGER NOT NULL CHECK(old_present IN (0,1)),
    old_snapshot TEXT NOT NULL DEFAULT '{}',
    new_present INTEGER NOT NULL CHECK(new_present IN (0,1)),
    new_snapshot TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(generation,change_sequence,source_id),
    FOREIGN KEY(generation) REFERENCES source_semantic_generations(generation) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_source_semantic_order_sequence
    ON source_semantic_order_changes(generation,change_sequence,source_id);

CREATE TRIGGER IF NOT EXISTS trg_source_semantic_generation_config_immutable
BEFORE UPDATE OF config_hash,config_json ON source_semantic_generations
WHEN old.config_hash IS NOT new.config_hash OR old.config_json IS NOT new.config_json
BEGIN
    SELECT RAISE(ABORT,'semantic_generation_config_immutable');
END;

DROP TRIGGER IF EXISTS trg_source_semantic_timeline_ai;
CREATE TRIGGER trg_source_semantic_timeline_ai AFTER INSERT ON timeline
WHEN new.event_type IN ${SOURCE_EVENT_TYPES}
  AND EXISTS (SELECT 1 FROM source_semantic_generations WHERE state IN ${TRACKED_GENERATION_STATES})
BEGIN
    UPDATE source_semantic_revision SET revision=revision+1 WHERE singleton=1;
    UPDATE source_semantic_generations SET state='building',updated_at=datetime('now')
    WHERE state='ready';
    UPDATE source_semantic_documents SET state='stale',updated_at=datetime('now')
    WHERE state!='stale'
      AND generation IN (SELECT generation FROM source_semantic_generations
                         WHERE state IN ${TRACKED_GENERATION_STATES})
      AND (anchor_source_id=new.id OR document_id IN (
            SELECT document_id FROM source_semantic_dependencies WHERE source_id=new.id));
    INSERT INTO source_semantic_dirty (
        generation,source_id,change_sequence,operation,old_present,
        new_source_id,new_present,new_scope,new_session_id,new_event_type,
        new_subject_id,new_object_id,new_owner_bot_id,new_bot_id,new_platform,
        new_persona_id,new_participant_user_id,new_occurred_at,new_created_at
    )
    SELECT g.generation,new.id,r.revision,'upsert',0,
        new.id,1,new.scope,new.session_id,new.event_type,new.subject_id,new.object_id,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.owner_bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.platform'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.persona_id'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.participant_user_id'),'') ELSE '' END,
        new.occurred_at,new.created_at
    FROM source_semantic_generations g CROSS JOIN source_semantic_revision r
    WHERE g.state IN ${TRACKED_GENERATION_STATES} AND r.singleton=1
    ON CONFLICT(generation,source_id) DO UPDATE SET
        change_sequence=excluded.change_sequence,operation='upsert',new_source_id=excluded.new_source_id,
        new_present=1,new_scope=excluded.new_scope,new_session_id=excluded.new_session_id,
        new_event_type=excluded.new_event_type,new_subject_id=excluded.new_subject_id,
        new_object_id=excluded.new_object_id,new_owner_bot_id=excluded.new_owner_bot_id,
        new_bot_id=excluded.new_bot_id,new_platform=excluded.new_platform,
        new_persona_id=excluded.new_persona_id,new_participant_user_id=excluded.new_participant_user_id,
        new_occurred_at=excluded.new_occurred_at,new_created_at=excluded.new_created_at;
    INSERT INTO source_semantic_order_changes(
        generation,change_sequence,source_id,old_present,old_snapshot,new_present,new_snapshot
    )
    SELECT g.generation,r.revision,new.id,0,'{}',1,${NEW_SOURCE_SNAPSHOT}
    FROM source_semantic_generations g CROSS JOIN source_semantic_revision r
    WHERE g.state IN ${TRACKED_GENERATION_STATES} AND r.singleton=1;
END;

DROP TRIGGER IF EXISTS trg_source_semantic_timeline_ad;
CREATE TRIGGER trg_source_semantic_timeline_ad AFTER DELETE ON timeline
WHEN old.event_type IN ${SOURCE_EVENT_TYPES}
  AND EXISTS (SELECT 1 FROM source_semantic_generations WHERE state IN ${TRACKED_GENERATION_STATES})
BEGIN
    UPDATE source_semantic_revision SET revision=revision+1 WHERE singleton=1;
    UPDATE source_semantic_generations SET state='building',updated_at=datetime('now')
    WHERE state='ready';
    UPDATE source_semantic_documents SET state='stale',updated_at=datetime('now')
    WHERE state!='stale'
      AND generation IN (SELECT generation FROM source_semantic_generations
                         WHERE state IN ${TRACKED_GENERATION_STATES})
      AND (anchor_source_id=old.id OR document_id IN (
            SELECT document_id FROM source_semantic_dependencies WHERE source_id=old.id));
    INSERT INTO source_semantic_dirty (
        generation,source_id,change_sequence,operation,old_present,
        old_scope,old_session_id,old_event_type,old_subject_id,old_object_id,
        old_owner_bot_id,old_bot_id,old_platform,old_persona_id,old_participant_user_id,
        old_occurred_at,old_created_at,new_source_id,new_present
    )
    SELECT g.generation,old.id,r.revision,'delete',1,
        old.scope,old.session_id,old.event_type,old.subject_id,old.object_id,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.owner_bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.platform'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.persona_id'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.participant_user_id'),'') ELSE '' END,
        old.occurred_at,old.created_at,'',0
    FROM source_semantic_generations g CROSS JOIN source_semantic_revision r
    WHERE g.state IN ${TRACKED_GENERATION_STATES} AND r.singleton=1
    ON CONFLICT(generation,source_id) DO UPDATE SET
        change_sequence=excluded.change_sequence,operation='delete',new_source_id='',new_present=0,
        new_scope=NULL,new_session_id=NULL,new_event_type=NULL,new_subject_id=NULL,new_object_id=NULL,
        new_owner_bot_id=NULL,new_bot_id=NULL,new_platform=NULL,new_persona_id=NULL,
        new_participant_user_id=NULL,new_occurred_at=NULL,new_created_at=NULL;
    INSERT INTO source_semantic_order_changes(
        generation,change_sequence,source_id,old_present,old_snapshot,new_present,new_snapshot
    )
    SELECT g.generation,r.revision,old.id,1,${OLD_SOURCE_SNAPSHOT},0,'{}'
    FROM source_semantic_generations g CROSS JOIN source_semantic_revision r
    WHERE g.state IN ${TRACKED_GENERATION_STATES} AND r.singleton=1;
END;

DROP TRIGGER IF EXISTS trg_source_semantic_timeline_au;
CREATE TRIGGER trg_source_semantic_timeline_au
AFTER UPDATE OF id,event_type,scope,session_id,subject_id,object_id,content,metadata,occurred_at,created_at ON timeline
WHEN (old.event_type IN ${SOURCE_EVENT_TYPES} OR new.event_type IN ${SOURCE_EVENT_TYPES})
  AND (old.id IS NOT new.id OR old.event_type IS NOT new.event_type OR old.scope IS NOT new.scope
    OR old.session_id IS NOT new.session_id OR old.subject_id IS NOT new.subject_id
    OR old.object_id IS NOT new.object_id OR old.content IS NOT new.content
    OR old.metadata IS NOT new.metadata OR old.occurred_at IS NOT new.occurred_at
    OR old.created_at IS NOT new.created_at)
  AND EXISTS (SELECT 1 FROM source_semantic_generations WHERE state IN ${TRACKED_GENERATION_STATES})
BEGIN
    UPDATE source_semantic_revision SET revision=revision+1 WHERE singleton=1;
    UPDATE source_semantic_generations SET state='building',updated_at=datetime('now')
    WHERE state='ready';
    UPDATE source_semantic_documents SET state='stale',updated_at=datetime('now')
    WHERE state!='stale'
      AND generation IN (SELECT generation FROM source_semantic_generations
                         WHERE state IN ${TRACKED_GENERATION_STATES})
      AND (anchor_source_id IN (old.id,new.id) OR document_id IN (
            SELECT document_id FROM source_semantic_dependencies WHERE source_id IN (old.id,new.id)));
    INSERT INTO source_semantic_dirty (
        generation,source_id,change_sequence,operation,old_present,
        old_scope,old_session_id,old_event_type,old_subject_id,old_object_id,
        old_owner_bot_id,old_bot_id,old_platform,old_persona_id,old_participant_user_id,
        old_occurred_at,old_created_at,new_source_id,new_present,new_scope,new_session_id,
        new_event_type,new_subject_id,new_object_id,new_owner_bot_id,new_bot_id,new_platform,
        new_persona_id,new_participant_user_id,new_occurred_at,new_created_at
    )
    SELECT g.generation,old.id,r.revision,
        CASE WHEN new.event_type IN ${SOURCE_EVENT_TYPES} THEN 'upsert' ELSE 'delete' END,
        CASE WHEN old.event_type IN ${SOURCE_EVENT_TYPES} THEN 1 ELSE 0 END,
        old.scope,old.session_id,old.event_type,old.subject_id,old.object_id,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.owner_bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.platform'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.persona_id'),'') ELSE '' END,
        CASE WHEN json_valid(old.metadata) THEN COALESCE(json_extract(old.metadata,'$.participant_user_id'),'') ELSE '' END,
        old.occurred_at,old.created_at,new.id,
        CASE WHEN new.event_type IN ${SOURCE_EVENT_TYPES} THEN 1 ELSE 0 END,
        new.scope,new.session_id,new.event_type,new.subject_id,new.object_id,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.owner_bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.bot_id'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.platform'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.persona_id'),'') ELSE '' END,
        CASE WHEN json_valid(new.metadata) THEN COALESCE(json_extract(new.metadata,'$.participant_user_id'),'') ELSE '' END,
        new.occurred_at,new.created_at
    FROM source_semantic_generations g CROSS JOIN source_semantic_revision r
    WHERE g.state IN ${TRACKED_GENERATION_STATES} AND r.singleton=1
    ON CONFLICT(generation,source_id) DO UPDATE SET
        change_sequence=excluded.change_sequence,operation=excluded.operation,
        new_source_id=excluded.new_source_id,new_present=excluded.new_present,
        new_scope=excluded.new_scope,new_session_id=excluded.new_session_id,
        new_event_type=excluded.new_event_type,new_subject_id=excluded.new_subject_id,
        new_object_id=excluded.new_object_id,new_owner_bot_id=excluded.new_owner_bot_id,
        new_bot_id=excluded.new_bot_id,new_platform=excluded.new_platform,
        new_persona_id=excluded.new_persona_id,new_participant_user_id=excluded.new_participant_user_id,
        new_occurred_at=excluded.new_occurred_at,new_created_at=excluded.new_created_at;
    INSERT INTO source_semantic_order_changes(
        generation,change_sequence,source_id,old_present,old_snapshot,new_present,new_snapshot
    )
    SELECT g.generation,r.revision,old.id,
        CASE WHEN old.event_type IN ${SOURCE_EVENT_TYPES} THEN 1 ELSE 0 END,
        CASE WHEN old.event_type IN ${SOURCE_EVENT_TYPES} THEN ${OLD_SOURCE_SNAPSHOT} ELSE '{}' END,
        CASE WHEN new.event_type IN ${SOURCE_EVENT_TYPES} THEN 1 ELSE 0 END,
        CASE WHEN new.event_type IN ${SOURCE_EVENT_TYPES} THEN ${NEW_SOURCE_SNAPSHOT} ELSE '{}' END
    FROM source_semantic_generations g CROSS JOIN source_semantic_revision r
    WHERE g.state IN ${TRACKED_GENERATION_STATES} AND r.singleton=1;
END;
