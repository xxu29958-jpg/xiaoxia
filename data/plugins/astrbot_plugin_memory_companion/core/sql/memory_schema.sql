CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    subject_kind TEXT NOT NULL DEFAULT '',
    subject_id TEXT NOT NULL DEFAULT '',
    subject_name TEXT NOT NULL DEFAULT '',
    subject_role TEXT NOT NULL DEFAULT '',
    object_kind TEXT NOT NULL DEFAULT '',
    object_id TEXT NOT NULL DEFAULT '',
    object_name TEXT NOT NULL DEFAULT '',
    object_role TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT '',
    sayability TEXT NOT NULL DEFAULT '',
    reality_level TEXT NOT NULL DEFAULT '',
    lifecycle TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    importance REAL NOT NULL DEFAULT 0.3,
    owner_bot_id TEXT NOT NULL DEFAULT '',
    validity_status TEXT NOT NULL DEFAULT 'active'
        CHECK(validity_status IN ('active','superseded','expired','archived','deleted','quarantined')),
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
    salience REAL NOT NULL DEFAULT 0.3 CHECK(salience >= 0 AND salience <= 1),
    durability TEXT NOT NULL DEFAULT 'normal'
        CHECK(durability IN ('ephemeral','short','normal','durable','pinned')),
    sensitivity TEXT NOT NULL DEFAULT 'private'
        CHECK(sensitivity IN ('public','internal','private','restricted')),
    reinforcement_score REAL NOT NULL DEFAULT 0
        CHECK(reinforcement_score >= 0 AND reinforcement_score <= 1),
    injection_count INTEGER NOT NULL DEFAULT 0 CHECK(injection_count >= 0),
    last_injected_at TEXT NOT NULL DEFAULT '',
    canonical_key TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'auto',
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT '',
    last_accessed_at TEXT NOT NULL DEFAULT '',
    access_count INTEGER NOT NULL DEFAULT 0,
    source_plugin TEXT NOT NULL DEFAULT '',
    import_batch_id TEXT NOT NULL DEFAULT '',
    content_fingerprint TEXT NOT NULL DEFAULT '',
    merged_count INTEGER NOT NULL DEFAULT 1,
    supersedes_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_memories_scope_session
    ON memories(scope, session_id, group_id, subject_id, object_id);
CREATE INDEX IF NOT EXISTS idx_memories_visibility
    ON memories(visibility, review_status, lifecycle);
CREATE INDEX IF NOT EXISTS idx_memories_reality
    ON memories(reality_level, memory_type, occurred_at);
CREATE INDEX IF NOT EXISTS idx_memories_content
    ON memories(content);

CREATE TABLE IF NOT EXISTS core_memory_revisions (
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    record TEXT NOT NULL,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY(memory_id, revision)
);

CREATE TABLE IF NOT EXISTS core_memory_mutations (
    owner TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    receipt TEXT NOT NULL,
    PRIMARY KEY(owner, operation)
);

CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT '',
    entity_kind TEXT NOT NULL DEFAULT 'user',
    entity_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'unknown',
    aliases TEXT NOT NULL DEFAULT '[]',
    profile TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(platform, entity_kind, entity_id)
);

CREATE TABLE IF NOT EXISTS timeline (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    subject_id TEXT NOT NULL DEFAULT '',
    object_id TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    message_id TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    summarized_at TEXT NOT NULL DEFAULT '',
    retention_class TEXT NOT NULL DEFAULT 'normal',
    import_batch_id TEXT NOT NULL DEFAULT '',
    source_sequence INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS summary_failures (
    session_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT '',
    start_timeline_id TEXT NOT NULL DEFAULT '',
    end_timeline_id TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chat_import_batches (
    id TEXT PRIMARY KEY,
    upload_id TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'prepared',
    session_id TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'private',
    platform TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    user_name TEXT NOT NULL DEFAULT '',
    bot_id TEXT NOT NULL DEFAULT '',
    bot_name TEXT NOT NULL DEFAULT '',
    speaker_map TEXT NOT NULL DEFAULT '{}',
    options TEXT NOT NULL DEFAULT '{}',
    stats TEXT NOT NULL DEFAULT '{}',
    checkpoint_segment INTEGER NOT NULL DEFAULT 0,
    total_segments INTEGER NOT NULL DEFAULT 0,
    completed_segments INTEGER NOT NULL DEFAULT 0,
    summary_memory_count INTEGER NOT NULL DEFAULT 0,
    important_event_count INTEGER NOT NULL DEFAULT 0,
    relationship_observation_count INTEGER NOT NULL DEFAULT 0,
    backup_path TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chat_import_segments (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL DEFAULT '',
    segment_index INTEGER NOT NULL DEFAULT 0,
    start_at TEXT NOT NULL DEFAULT '',
    end_at TEXT NOT NULL DEFAULT '',
    local_date TEXT NOT NULL DEFAULT '',
    message_ids TEXT NOT NULL DEFAULT '[]',
    transcript TEXT NOT NULL DEFAULT '',
    char_count INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '{}',
    summary_memory_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(batch_id, segment_index)
);

CREATE TABLE IF NOT EXISTS relationship_edges (
    id TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL DEFAULT '',
    subject_id TEXT NOT NULL DEFAULT '',
    subject_name TEXT NOT NULL DEFAULT '',
    object_kind TEXT NOT NULL DEFAULT '',
    object_id TEXT NOT NULL DEFAULT '',
    object_name TEXT NOT NULL DEFAULT '',
    relation_type TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'internal',
    evidence TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL DEFAULT 'auto',
    source_memory_id TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(subject_kind, subject_id, object_kind, object_id, relation_type, scope, session_id)
);

CREATE INDEX IF NOT EXISTS idx_relationship_edges_subject
    ON relationship_edges(subject_kind, subject_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_relationship_edges_object
    ON relationship_edges(object_kind, object_id, relation_type);

CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL DEFAULT '',
    node_key TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(node_type, node_key, scope, session_id)
);

CREATE TABLE IF NOT EXISTS knowledge_edges (
    id TEXT PRIMARY KEY,
    source_node_id TEXT NOT NULL DEFAULT '',
    target_node_id TEXT NOT NULL DEFAULT '',
    relation_type TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    source_memory_id TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL DEFAULT 'auto',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(source_node_id, target_node_id, relation_type, source_memory_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_lookup
    ON knowledge_nodes(scope, session_id, node_type, label);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_source
    ON knowledge_edges(source_node_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_target
    ON knowledge_edges(target_node_id, relation_type);

CREATE TABLE IF NOT EXISTS cross_window_threads (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'open',
    from_session TEXT NOT NULL DEFAULT '',
    to_session TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'shareable',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS review_queue (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(memory_id, reason)
);

CREATE TABLE IF NOT EXISTS injection_logs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    selected_memory_ids TEXT NOT NULL DEFAULT '[]',
    blocked_reasons TEXT NOT NULL DEFAULT '[]',
    injection_chars INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    source_plugin TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '',
    stats TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memory_acl_rules (
    id TEXT PRIMARY KEY,
    owner_scope TEXT NOT NULL DEFAULT '',
    owner_id TEXT NOT NULL DEFAULT '',
    reader_scope TEXT NOT NULL DEFAULT '',
    reader_id TEXT NOT NULL DEFAULT '',
    effect TEXT NOT NULL DEFAULT 'allow',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(owner_scope, owner_id, reader_scope, reader_id)
);

CREATE TABLE IF NOT EXISTS memory_acl_policies (
    id TEXT PRIMARY KEY,
    window_scope TEXT NOT NULL DEFAULT '',
    window_id TEXT NOT NULL DEFAULT '',
    read_mode TEXT NOT NULL DEFAULT 'whitelist',
    share_mode TEXT NOT NULL DEFAULT 'whitelist',
    capture_enabled INTEGER CHECK(capture_enabled IS NULL OR capture_enabled IN (0, 1)),
    recall_enabled INTEGER CHECK(recall_enabled IS NULL OR recall_enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(window_scope, window_id)
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    text_hash TEXT NOT NULL DEFAULT '',
    dimension INTEGER NOT NULL DEFAULT 0,
    vector TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(memory_id, provider_id)
);

CREATE TABLE IF NOT EXISTS emotion_events (
    event_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    trace_id TEXT NOT NULL DEFAULT '',
    producer_plugin TEXT NOT NULL DEFAULT '',
    origin_kind TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    bot_id TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    actor_ref TEXT NOT NULL DEFAULT '{}',
    target_ref TEXT NOT NULL DEFAULT '{}',
    quoted_target_ref TEXT NOT NULL DEFAULT '{}',
    event_type TEXT NOT NULL DEFAULT 'neutral',
    intensity REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    valence_hint REAL NOT NULL DEFAULT 0,
    arousal_hint REAL NOT NULL DEFAULT 0,
    vulnerability_hint REAL NOT NULL DEFAULT 0,
    source_rule TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '',
    payload_hash TEXT NOT NULL DEFAULT '',
    privacy_level TEXT NOT NULL DEFAULT 'redacted',
    applied_interaction TEXT NOT NULL DEFAULT '',
    applied_energy_delta REAL NOT NULL DEFAULT 0,
    correction_of TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'observed',
    reason_codes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(event_id, revision)
);

CREATE TABLE IF NOT EXISTS emotion_event_deliveries (
    event_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    consumer_id TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    first_delivered_at TEXT NOT NULL DEFAULT '',
    last_delivered_at TEXT NOT NULL DEFAULT '',
    acked_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(event_id, revision, consumer_id),
    FOREIGN KEY(event_id, revision)
        REFERENCES emotion_events(event_id, revision)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS portrait_people (
    person_id TEXT PRIMARY KEY,
    resolved_identity_key TEXT NOT NULL DEFAULT '',
    projection_revision INTEGER NOT NULL DEFAULT 0,
    identity_assurance TEXT NOT NULL DEFAULT '',
    profile_status TEXT NOT NULL DEFAULT '',
    capability_summary TEXT NOT NULL DEFAULT '{}',
    portrait_revision INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS portrait_scope_capabilities (
    person_id TEXT NOT NULL DEFAULT '',
    source_scope TEXT NOT NULL DEFAULT '',
    capability_summary TEXT NOT NULL DEFAULT '{}',
    projection_revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(person_id, source_scope)
);

CREATE TABLE IF NOT EXISTS portrait_evidence (
    evidence_hash TEXT PRIMARY KEY,
    person_id TEXT NOT NULL DEFAULT '',
    origin_identity_key TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    statement_fingerprint TEXT NOT NULL DEFAULT '',
    context_refs TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS portrait_facts (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL DEFAULT '',
    dimension TEXT NOT NULL DEFAULT '',
    normalized_claim_hash TEXT NOT NULL DEFAULT '',
    claim_summary TEXT NOT NULL DEFAULT '',
    portrait_tier TEXT NOT NULL DEFAULT '',
    producer_kind TEXT NOT NULL DEFAULT '',
    producer_version TEXT NOT NULL DEFAULT '',
    derivation_kind TEXT NOT NULL DEFAULT '',
    epistemic_status TEXT NOT NULL DEFAULT '',
    source_scope TEXT NOT NULL DEFAULT '',
    usable_scope TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    sensitivity TEXT NOT NULL DEFAULT 'high',
    status TEXT NOT NULL DEFAULT 'active',
    evidence_hashes TEXT NOT NULL DEFAULT '[]',
    context_refs TEXT NOT NULL DEFAULT '[]',
    first_evidence_at TEXT NOT NULL DEFAULT '',
    last_evidence_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    supersedes_id TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    operation_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(person_id, dimension, normalized_claim_hash, portrait_tier, source_scope)
);

CREATE TABLE IF NOT EXISTS portrait_suppressions (
    suppression_key TEXT PRIMARY KEY,
    person_id TEXT NOT NULL DEFAULT '',
    dimension TEXT NOT NULL DEFAULT '',
    normalized_claim_hash TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    origin_identity_key TEXT NOT NULL DEFAULT '',
    operation_id TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    revoked_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS portrait_operations (
    operation_id TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL DEFAULT '',
    payload_hash TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS profile_repair_operations (
    operation_id TEXT PRIMARY KEY,
    rule_version TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    backup_path TEXT NOT NULL DEFAULT '',
    plan_fingerprint TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS portrait_learning_queue (
    queue_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL DEFAULT '',
    fact_id TEXT NOT NULL DEFAULT '',
    evidence_hash TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(person_id, fact_id, evidence_hash)
);

CREATE TABLE IF NOT EXISTS portrait_daily_runs (
    person_id TEXT NOT NULL DEFAULT '',
    run_day TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0,
    last_code TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(person_id, run_day)
);

CREATE TRIGGER IF NOT EXISTS core_history_source_deleted AFTER DELETE ON timeline BEGIN
    DELETE FROM core_memory_revisions
    WHERE json_extract(source, '$.capture.source_ref') = 'timeline:' || OLD.id;
END;
