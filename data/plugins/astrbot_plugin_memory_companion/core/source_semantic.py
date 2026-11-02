"""Public compatibility facade; each operation has one domain owner."""

from .source_semantic_authorization import (
    _authorization_result as _authorization_result,
)
from .source_semantic_authorization import _build_scope_matches as _build_scope_matches
from .source_semantic_authorization import _build_scope_sql as _build_scope_sql
from .source_semantic_authorization import _writable_generation as _writable_generation
from .source_semantic_commit import _current_dependency_rows as _current_dependency_rows
from .source_semantic_commit import capture_semantic_fence as capture_semantic_fence
from .source_semantic_commit import store_semantic_document as store_semantic_document
from .source_semantic_commit import (
    validate_semantic_snapshot as validate_semantic_snapshot,
)
from .source_semantic_common import _SOURCE_EVENT_SQL as _SOURCE_EVENT_SQL
from .source_semantic_common import _SOURCE_PARTITION_FIELDS as _SOURCE_PARTITION_FIELDS
from .source_semantic_common import (
    _TRACKED_GENERATION_STATES_SQL as _TRACKED_GENERATION_STATES_SQL,
)
from .source_semantic_common import FRAGMENT_OVERLAP_CHARS as FRAGMENT_OVERLAP_CHARS
from .source_semantic_common import FRAGMENT_PROFILE as FRAGMENT_PROFILE
from .source_semantic_common import FRAGMENT_TARGET_CHARS as FRAGMENT_TARGET_CHARS
from .source_semantic_common import _canonical_json as _canonical_json
from .source_semantic_common import _digest as _digest
from .source_semantic_common import _json_object as _json_object
from .source_semantic_common import _source_partition as _source_partition
from .source_semantic_common import _source_position as _source_position
from .source_semantic_common import _source_snapshot as _source_snapshot
from .source_semantic_common import _trusted_timestamp as _trusted_timestamp
from .source_semantic_common import pack_semantic_vector as pack_semantic_vector
from .source_semantic_common import semantic_document_id as semantic_document_id
from .source_semantic_common import split_source_fragments as split_source_fragments
from .source_semantic_dirty import _dirty_values_from_row as _dirty_values_from_row
from .source_semantic_dirty import _snapshot_neighbors as _snapshot_neighbors
from .source_semantic_dirty import _snapshot_position as _snapshot_position
from .source_semantic_dirty import (
    expand_semantic_order_changes as expand_semantic_order_changes,
)
from .source_semantic_dirty import list_semantic_dirty as list_semantic_dirty
from .source_semantic_documents import acknowledge_dirty as acknowledge_dirty
from .source_semantic_documents import (
    prune_semantic_documents as prune_semantic_documents,
)
from .source_semantic_documents import (
    remove_semantic_documents_for_source as remove_semantic_documents_for_source,
)
from .source_semantic_documents import (
    semantic_document_is_ready as semantic_document_is_ready,
)
from .source_semantic_generations import (
    enqueue_semantic_source as enqueue_semantic_source,
)
from .source_semantic_generations import (
    find_active_generation as find_active_generation,
)
from .source_semantic_generations import generation_status as generation_status
from .source_semantic_generations import list_generations as list_generations
from .source_semantic_generations import mark_generation_ready as mark_generation_ready
from .source_semantic_generations import pause_generation as pause_generation
from .source_semantic_generations import register_generation as register_generation
from .source_semantic_generations import resume_generation as resume_generation
from .source_semantic_generations import retire_generation as retire_generation
from .source_semantic_queries import read_semantic_neighbors as read_semantic_neighbors
from .source_semantic_queries import read_semantic_source as read_semantic_source
from .source_semantic_queries import scan_generation_sources as scan_generation_sources
from .source_semantic_schema import initialize as initialize
from .source_semantic_windows import build_semantic_window as build_semantic_window
