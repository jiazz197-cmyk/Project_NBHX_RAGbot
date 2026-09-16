"""Regression tests for the dead-code cleanup (plan 1784906924324).

Structural safety checks that need no external services (no DB / Redis / MinIO /
SQL Server / llama_index). They guard against accidental re-introduction of
removed symbols and verify that live same-name-different-symbol (同名异物)
counterparts remain intact.
"""
import importlib

import pytest


# ---------------------------------------------------------------------------
# Settings: removed fields absent, kept fields present, extra=ignore
# ---------------------------------------------------------------------------

REMOVED_CONFIG_FIELDS = [
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
    "RATE_LIMIT_REQUESTS_PER_HOUR",
    "CACHE_DEFAULT_TTL",
    "CACHE_ENABLED_METHODS",
    "MAX_FILE_SIZE_MB",
    "ALLOWED_FILE_EXTENSIONS",
    "QWEN3_8B_API_URL",
    "N8N_BASE_URL",
    "N8N_API_KEY",
    "DIFY_API_KEY",
    "DIFY_BASE_URL",
    "DIFY_APP_API_KEY",
    "CHAT_API_KEY",
    "RAGFLOW_BASE_URL",
    "RAGFLOW_API_KEY",
    "RAGFLOW_DATASET_ID",
    "SUPERSET_BASE_URL",
    "SUPERSET_USERNAME",
    "SUPERSET_PASSWORD",
    "SUPERSET_DATABASE_ID",
    "SUPERSET_GUEST_TOKEN_SECRET",
    "SUPERSET_CSRF_TOKEN_TIMEOUT",
    "SUPERSET_CACHE_DEFAULT_TIMEOUT",
    "DATAHUB_GMS_URL",
    "DATAHUB_GMS_TOKEN",
    "DATAHUB_KAFKA_BOOTSTRAP_SERVERS",
    "DATAHUB_SCHEMA_REGISTRY_URL",
    "LOG_FORMAT",
    "LOG_FILE",
    "ENABLE_METRICS",
    "METRICS_PORT",
    "ENABLE_MONITORING",
    "MONITORING_INTERVAL",
    "ENABLE_PROMETHEUS",
    "ENABLE_HEALTH_CHECK",
]

KEPT_CONFIG_FIELDS = [
    "QWEN3_6_35B_API_URL",
    "QWEN3_6_35B_MODEL",
    "LANGCHAIN_CHAT_ENABLED",
    "LANGCHAIN_CHAT_BASE_URL",
    "LANGCHAIN_CHAT_MODEL",
    "LANGCHAIN_CHAT_TIMEOUT_SEC",
    "LANGCHAIN_MAX_CONTEXT_MESSAGES",
    "LANGCHAIN_MAX_OUTPUT_TOKENS",
    "METRICS_REQUIRE_API_KEY",
    "ENABLE_SECURITY_HEADERS",
    "ENABLE_HSTS",
    "HSTS_MAX_AGE",
    "CACHE_TTL",
    "ENABLE_CACHE",
    "CACHE_API_RESPONSE_TTL",
    "CACHE_JOB_STATUS_TTL",
    "MAX_FILE_SIZE",
    "RATE_LIMIT_AUTH",
    "RATE_LIMIT_ANON",
    "RATE_LIMIT_WINDOW",
    "RATE_LIMIT_AUTH_ADMIN",
    "LOG_LEVEL",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
]


class TestSettingsCleanup:
    def test_settings_loads_without_error(self):
        from app.core.config import settings
        assert settings is not None

    @pytest.mark.parametrize("field", REMOVED_CONFIG_FIELDS)
    def test_removed_config_field_absent(self, field):
        from app.core.config import settings
        assert not hasattr(settings, field), f"{field} should have been removed"

    @pytest.mark.parametrize("field", KEPT_CONFIG_FIELDS)
    def test_kept_config_field_present(self, field):
        from app.core.config import settings
        assert hasattr(settings, field), f"{field} must be kept (live config)"

    def test_extra_ignore_prevents_stale_env_crash(self):
        """Settings.Config.extra='ignore' -> stale .env keys for removed
        fields are silently dropped instead of crashing startup."""
        from app.core.config import Settings
        assert getattr(Settings.Config, "extra", None) == "ignore"


# ---------------------------------------------------------------------------
# Exceptions: removed absent, kept present, DocumentProcessingError intact
# ---------------------------------------------------------------------------

REMOVED_EXCEPTIONS = [
    "ConflictError",
    "ProcessingError",
    "DatabaseError",
    "RateLimitError",
    "FileUploadError",
]

KEPT_EXCEPTIONS = [
    "APIException",
    "ValidationError",
    "NotFoundError",
    "PermissionDeniedError",
    "ExternalServiceError",
    "AuthenticationError",
]


class TestExceptionsCleanup:
    @pytest.mark.parametrize("name", REMOVED_EXCEPTIONS)
    def test_removed_exception_absent(self, name):
        import app.core.exceptions as mod
        assert not hasattr(mod, name), f"{name} should have been removed"

    @pytest.mark.parametrize("name", KEPT_EXCEPTIONS)
    def test_kept_exception_present(self, name):
        import app.core.exceptions as mod
        assert hasattr(mod, name), f"{name} must be kept"

    def test_document_processing_error_intact(self):
        """同名异物: the live DocumentProcessingError must remain importable
        and distinct from the deleted core ProcessingError."""
        from app.adapters.doc_processing.exceptions import DocumentProcessingError
        import app.core.exceptions as core_mod
        assert not hasattr(core_mod, "ProcessingError")
        assert DocumentProcessingError is not None
        assert issubclass(DocumentProcessingError, Exception)


# ---------------------------------------------------------------------------
# Core utilities: removed absent, kept present
# ---------------------------------------------------------------------------

class TestCoreUtilsCleanup:
    def test_get_db_removed(self):
        import app.core.dependencies as mod
        assert not hasattr(mod, "get_db")

    def test_get_async_db_kept(self):
        import app.core.dependencies as mod
        assert hasattr(mod, "get_async_db")

    def test_database_logger_removed(self):
        import app.core.logging as mod
        assert not hasattr(mod, "database_logger")

    def test_function_observer_removed(self):
        import app.core.observer as mod
        assert not hasattr(mod, "FunctionObserver")

    def test_task_subject_kept(self):
        import app.core.observer as mod
        assert hasattr(mod, "TaskSubject")

    def test_async_redis_check_rate_limit_removed(self):
        """Dead code: AsyncRedisManager.check_rate_limit (racy get/setex/incr)
        was removed; the middleware uses its own epoch-bucketed incr/expire path."""
        from app.core.cache import AsyncRedisManager
        assert not hasattr(AsyncRedisManager, "check_rate_limit")


# ---------------------------------------------------------------------------
# ORM: deleted knowledge models absent from metadata & module;
#      KnowledgeInstance kept and registered for create_all
# ---------------------------------------------------------------------------

DELETED_KNOWLEDGE_TABLES = [
    "knowledge_fragment",
    "knowledge_resource_tag",
    "knowledge_instance_permission",
]

DELETED_KNOWLEDGE_CLASSES = [
    "KnowledgeFragment",
    "KnowledgeResourceTag",
    "KnowledgeInstancePermission",
]


class TestOrmCleanup:
    def test_knowledge_instance_in_metadata(self):
        from app.models.orm.platform.base import Base
        import app.models.orm.knowledge  # noqa: F401  (trigger registration)
        assert "knowledge_instance" in Base.metadata.tables

    @pytest.mark.parametrize("table_name", DELETED_KNOWLEDGE_TABLES)
    def test_deleted_knowledge_table_absent_from_metadata(self, table_name):
        from app.models.orm.platform.base import Base
        import app.models.orm.knowledge  # noqa: F401
        assert table_name not in Base.metadata.tables

    @pytest.mark.parametrize("cls_name", DELETED_KNOWLEDGE_CLASSES)
    def test_deleted_knowledge_class_absent_from_module(self, cls_name):
        import app.models.orm.knowledge as mod
        assert not hasattr(mod, cls_name)

    def test_knowledge_instance_class_present(self):
        import app.models.orm.knowledge as mod
        assert hasattr(mod, "KnowledgeInstance")
