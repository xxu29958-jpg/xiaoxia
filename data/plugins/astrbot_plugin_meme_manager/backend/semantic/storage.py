"""Public compatibility facade; each operation has one domain owner."""

from .models import SCHEMA_VERSION as SCHEMA_VERSION
from .storage_common import LEGACY_METADATA_BACKUP_NAME as LEGACY_METADATA_BACKUP_NAME
from .storage_common import LEGACY_SCHEMA_VERSION as LEGACY_SCHEMA_VERSION
from .storage_common import LOCAL_EMBEDDING_FIELDS as LOCAL_EMBEDDING_FIELDS
from .storage_common import PORTABLE_IMAGE_FIELDS as PORTABLE_IMAGE_FIELDS
from .storage_common import PORTABLE_METADATA_FIELDS as PORTABLE_METADATA_FIELDS
from .storage_common import (
    SemanticMetadataCompatibilityError as SemanticMetadataCompatibilityError,
)
from .storage_common import _create_legacy_backup as _create_legacy_backup
from .storage_common import _write_json_atomic as _write_json_atomic
from .storage_common import file_sha256 as file_sha256
from .storage_common import metadata_path as metadata_path
from .storage_common import safe_relative_path as safe_relative_path
from .storage_edit_snapshot import (
    _apply_manual_semantic_inputs as _apply_manual_semantic_inputs,
)
from .storage_edit_snapshot import (
    _assert_image_snapshot_unchanged as _assert_image_snapshot_unchanged,
)
from .storage_edit_snapshot import (
    _normalize_manual_semantic_inputs as _normalize_manual_semantic_inputs,
)
from .storage_edit_snapshot import (
    _resolve_image_edit_snapshot as _resolve_image_edit_snapshot,
)
from .storage_edit_snapshot import (
    validate_image_edit_snapshot as validate_image_edit_snapshot,
)
from .storage_edits import _build_category_moved_item as _build_category_moved_item
from .storage_edits import _resolve_image_move_target as _resolve_image_move_target
from .storage_edits import confirm_image_category as confirm_image_category
from .storage_edits import restore_image_auto_semantic as restore_image_auto_semantic
from .storage_edits import (
    save_collected_image_semantic as save_collected_image_semantic,
)
from .storage_edits import save_manual_image_semantic as save_manual_image_semantic
from .storage_edits import (
    save_manual_image_semantic_and_move as save_manual_image_semantic_and_move,
)
from .storage_edits import set_image_embedding_failure as set_image_embedding_failure
from .storage_metadata import _normalize_image_records as _normalize_image_records
from .storage_metadata import load_metadata as load_metadata
from .storage_metadata import save_metadata as save_metadata
from .storage_metadata import (
    semantic_metadata_is_complete as semantic_metadata_is_complete,
)
from .storage_migration import _legacy_reusable_record as _legacy_reusable_record
from .storage_migration import migrate_legacy_metadata as migrate_legacy_metadata
from .storage_reclassification import _ensure_pack_category as _ensure_pack_category
from .storage_reclassification import _is_safe_category_key as _is_safe_category_key
from .storage_reclassification import (
    apply_conflict_reclassifications as apply_conflict_reclassifications,
)
from .storage_reconciliation import import_metadata_file as import_metadata_file
from .storage_reconciliation import (
    invalidate_semantic_metadata as invalidate_semantic_metadata,
)
from .storage_reconciliation import reconcile_metadata as reconcile_metadata
from .storage_reconciliation import (
    reset_local_embedding_state as reset_local_embedding_state,
)
from .storage_scan import load_category_descriptions as load_category_descriptions
from .storage_scan import scan_images as scan_images
from .storage_validation import _is_sha256 as _is_sha256
from .storage_validation import _metadata_fault as _metadata_fault
from .storage_validation import _source_schema_version as _source_schema_version
from .storage_validation import _validate_metadata_records as _validate_metadata_records
from .storage_views import get_category_review_overview as get_category_review_overview
from .storage_views import get_image_semantic_detail as get_image_semantic_detail
from .storage_views import get_pack_semantic_summary as get_pack_semantic_summary
from .storage_views import metadata_items as metadata_items
