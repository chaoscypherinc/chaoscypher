"""Vulture whitelist for false positives.

This file contains simulated usages to suppress false positives from vulture.
Each entry represents code that IS used but vulture cannot detect it.

Categories:
1. FastAPI endpoint functions (registered via @router decorator)
2. Click CLI commands (registered via @cli.command decorator)
3. Protocol/interface implementations (required for type compliance)
4. SQLAlchemy event listeners (registered via @event.listens_for)
5. Pytest fixtures (used by test discovery)
6. Pydantic validators (called by Pydantic internally)
7. Plugin methods (called dynamically via registry)
"""

# =============================================================================
# FastAPI Endpoints (registered via decorators, not direct calls)
# =============================================================================

# Sources API
get_source_chunks  # noqa: F821
get_source_citations  # noqa: F821
assign_tag_to_source  # noqa: F821
unassign_tag_from_source  # noqa: F821

# Templates API
batch_templates_operation  # noqa: F821

# Workflows API
list_workflow_executions  # noqa: F821
get_workflow_execution  # noqa: F821
cancel_workflow_execution  # noqa: F821
get_workflow_stats  # noqa: F821
reorder_workflow_steps  # noqa: F821
list_workflow_triggers  # noqa: F821

# Main middleware
enforce_read_only  # noqa: F821  # FastAPI middleware
serve_spa  # noqa: F821  # SPA serve endpoint

# Auth API
get_current_user_profile  # noqa: F821
update_profile  # noqa: F821
get_user_by_id  # noqa: F821
activate_user  # noqa: F821
deactivate_user  # noqa: F821

# Chats API
stream_chat  # noqa: F821
get_chat_count  # noqa: F821

# Discovery API
start_discovery_analysis  # noqa: F821
get_discovery_task_status  # noqa: F821
get_active_discovery_tasks  # noqa: F821
cancel_discovery_task  # noqa: F821
delete_suggestion  # noqa: F821
approve_suggestions  # noqa: F821
delete_suggestions  # noqa: F821
get_discovery_stats  # noqa: F821

# Edges API
batch_edges_operation  # noqa: F821

# Export API
create_export  # noqa: F821
create_import  # noqa: F821
create_export_by_sources  # noqa: F821

# Graph API
reload_graph  # noqa: F821
get_neighbors  # noqa: F821

# Source Processing API
start_analysis  # noqa: F821
get_analysis  # noqa: F821
abort_processing  # noqa: F821
get_extraction_progress  # noqa: F821
retry_failed_chunks  # noqa: F821
cancel_extraction  # noqa: F821
get_file_entities  # noqa: F821
get_file_relationships  # noqa: F821

# Lenses API
list_lens_sessions  # noqa: F821
create_lens_session  # noqa: F821
get_lens_session_status  # noqa: F821
delete_lens_session  # noqa: F821
list_lens_rules  # noqa: F821
get_lens_rule  # noqa: F821
update_lens_rule  # noqa: F821
delete_lens_rule  # noqa: F821
add_manual_lens_rule  # noqa: F821
get_lens_stats  # noqa: F821
import_from_lexicon  # noqa: F821

# Lexicon API
search_packages  # noqa: F821
upload_package  # noqa: F821

# LLM API
get_llm_queue_stats  # noqa: F821
delete_llm_queue_stats  # noqa: F821
delete_all_tasks  # noqa: F821
check_llm_queue_health  # noqa: F821
delete_semaphore  # noqa: F821

# Nodes API
batch_nodes_operation  # noqa: F821

# Queue API
queue_task  # noqa: F821
clear_history  # noqa: F821
cancel_tasks  # noqa: F821

# Search API
get_search_stats  # noqa: F821
rebuild_search_indexes  # noqa: F821

# Settings API
reset_settings  # noqa: F821
reset_workflows  # noqa: F821
reset_source_processing  # noqa: F821
reset_queue  # noqa: F821
reset_knowledge  # noqa: F821
reset_all_data  # noqa: F821

# =============================================================================
# Click CLI Commands (registered via decorators)
# =============================================================================
completions  # noqa: F821
show_config  # noqa: F821
get_value  # noqa: F821
set_value  # noqa: F821
edit_config  # noqa: F821
show_path  # noqa: F821
query_examples  # noqa: F821
health  # noqa: F821

# =============================================================================
# SQLAlchemy Event Listeners (registered via decorator)
# =============================================================================
set_sqlite_pragma  # noqa: F821
connection_record  # noqa: F821  # Event listener parameter

# =============================================================================
# Protocol/Interface Implementations (required for type compliance)
# These methods exist to satisfy Protocol contracts even if not called directly
# =============================================================================

# Storage Protocol methods
delete_suggestions  # noqa: F821
list_chunks  # noqa: F821
update_chunk  # noqa: F821
list_citations  # noqa: F821
list_tool_statistics  # noqa: F821
create_workflow_execution  # noqa: F821
update_workflow_execution  # noqa: F821
get_executions  # noqa: F821
update_current_step  # noqa: F821
create_step_execution  # noqa: F821
update_step_status  # noqa: F821
complete_step_execution  # noqa: F821
fail_step_execution  # noqa: F821
update_chunk_source  # noqa: F821

# Source Processing Protocol methods
start_indexing  # noqa: F821
complete_indexing  # noqa: F821
fail_indexing  # noqa: F821
fail_commit  # noqa: F821
create_chunks_batch  # noqa: F821
get_chunks_without_embeddings  # noqa: F821
delete_file_complete  # noqa: F821
create_extraction_job  # noqa: F821
get_extraction_job_entity  # noqa: F821
start_extraction_job  # noqa: F821
complete_extraction_job  # noqa: F821
fail_extraction_job  # noqa: F821
increment_job_progress  # noqa: F821
create_chunk_task  # noqa: F821
create_chunk_tasks_batch  # noqa: F821
get_chunk_task  # noqa: F821
get_chunk_task_entity  # noqa: F821
mark_chunk_task_queued  # noqa: F821
start_chunk_task  # noqa: F821
complete_chunk_task  # noqa: F821
fail_chunk_task  # noqa: F821
list_chunk_tasks  # noqa: F821
get_chunk_tasks_summary  # noqa: F821
get_failed_chunk_tasks  # noqa: F821
get_completed_chunk_results  # noqa: F821
get_chunk_timing_stats  # noqa: F821
delete_extraction_job  # noqa: F821

# Loader plugin methods
supports_ocr  # noqa: F821

# =============================================================================
# Pydantic Validators (called internally by Pydantic)
# =============================================================================
validate_package_type  # noqa: F821
validate_name_format  # noqa: F821
validate_version_format  # noqa: F821
validate_output_format  # noqa: F821
username_alphanumeric  # noqa: F821
password_strength  # noqa: F821

# =============================================================================
# Workflow System Classes (public API)
# =============================================================================
WorkflowSystemInitializer  # noqa: F821
initialize  # noqa: F821  # Method on WorkflowSystemInitializer

# =============================================================================
# Plugin Registry Methods (public API)
# =============================================================================
_register  # noqa: F821
get_required  # noqa: F821
list_ids  # noqa: F821
list_metadata_dicts  # noqa: F821
contains  # noqa: F821
list_loaders  # noqa: F821

# =============================================================================
# Public API Methods (exposed for external use)
# =============================================================================

# Operations service public API
queue_discovery_analysis  # noqa: F821
queue_lens_build_analysis  # noqa: F821

# Workflow engine public API
log_step_output  # noqa: F821
validate_execution_state  # noqa: F821
finish  # noqa: F821
list_by_category  # noqa: F821
toggle_trigger  # noqa: F821
count_pending_suggestions  # noqa: F821
delete_chunks_by_source_file  # noqa: F821
list_package_versions  # noqa: F821

# LLM Factory public API
check_provider_health  # noqa: F821
get_load_balancer  # noqa: F821
reload_load_balancer  # noqa: F821

# Queue client public API
get_recent_tasks  # noqa: F821
get_recent_tasks_count  # noqa: F821
get_result  # noqa: F821
cancel_tasks_batch  # noqa: F821
retry_task  # noqa: F821
cancel_by_metadata  # noqa: F821
cancel_all_tasks  # noqa: F821
clear_old_completed_tasks  # noqa: F821
clear_all_stats  # noqa: F821
get_worker_status  # noqa: F821

# Operations repository public API
enqueue_operation  # noqa: F821
abort_operation  # noqa: F821
queue_import_commit  # noqa: F821
queue_import_indexing  # noqa: F821
get_operations_service  # noqa: F821

# Workflow triggers public API
start  # noqa: F821
stop  # noqa: F821

# Graph repository public API
delete_edges_batch  # noqa: F821
delete_nodes_batch  # noqa: F821
enrich_search_results  # noqa: F821

# Source processing service public API
start_analysis_task  # noqa: F821
start_commit_task  # noqa: F821
cancel_analysis_task  # noqa: F821

# Validators public API
validate_file_for_commit  # noqa: F821
validate_file_for_edges  # noqa: F821
validate_file_for_cancellation  # noqa: F821

# Model methods
to_dict_list  # noqa: F821
to_dict_list_summary  # noqa: F821
parse_output_dir  # noqa: F821
get_source_id  # noqa: F821
remap_edge  # noqa: F821
remap_citation  # noqa: F821

# Service methods
calculate_file_checksum  # noqa: F821
mark_complete  # noqa: F821
mark_error  # noqa: F821
cleanup_task  # noqa: F821
get_active_task_count  # noqa: F821
is_task_active  # noqa: F821
poll_device_token_blocking  # noqa: F821
import_from_path  # noqa: F821

# Database repository public API
get_database_path  # noqa: F821

# =============================================================================
# Pydantic Models (used for API serialization)
# =============================================================================

# Sources models
TagAssignmentResponse  # noqa: F821

# Source processing models
AnalysisRequest  # noqa: F821
BatchUploadResponse  # noqa: F821
EdgesResponse  # noqa: F821
MaintenanceResponse  # noqa: F821

# Lenses models
SessionListResponse  # noqa: F821
RuleUpdateRequest  # noqa: F821
LexiconImportResponse  # noqa: F821

# =============================================================================
# Pytest Fixtures (discovered by pytest)
# =============================================================================
cli_runner  # noqa: F821
mock_get_context  # noqa: F821
mock_get_context_with_llm  # noqa: F821
mock_hub_api_client  # noqa: F821

# =============================================================================
# CLI Utilities (public API)
# =============================================================================
ensure_absolute_path  # noqa: F821
ProcessingResult  # noqa: F821
list_pending_files  # noqa: F821

# Hub API client methods
download_package  # noqa: F821

# =============================================================================
# Extraction utilities (internal helpers)
# =============================================================================
_get_current_embedding_model  # noqa: F821

# =============================================================================
# TYPE_CHECKING imports (used for type hints)
# =============================================================================
EventDict  # noqa: F821  # Used in structlog processor type hints
Processor  # noqa: F821  # Used in structlog processor type hints
Agent  # noqa: F821  # Used in pydantic-ai agent type hints
SQLite3Connection  # noqa: F821  # Used in event listener type hint
AbstractContextManager  # noqa: F821  # Used in session.py return type hint

# =============================================================================
# Protocol interface parameters (required for interface definition)
# =============================================================================
query_vector  # noqa: F821  # VectorSearchProtocol.vector_search parameter
template_creates  # noqa: F821  # GraphStorageProtocol.create_templates_batch parameter

# =============================================================================
# Context-manager protocol parameters (required by __exit__/__aexit__ signature)
# =============================================================================
exc_type  # noqa: F821  # __exit__/__aexit__ first positional parameter
exc_val   # noqa: F821  # __exit__/__aexit__ second positional parameter
exc_tb    # noqa: F821  # __exit__/__aexit__ third positional parameter

# =============================================================================
# Pytest fixture names — injected by name; body uses them for side-effects only
# =============================================================================
patched_adapter_factory  # noqa: F821  # queue/test_upgrade_recovery.py fixture
tiny_body_limit          # noqa: F821  # cortex test_middleware.py fixture
