"""Regression tests for the chat memory layer (short-term + long-term memory).

Two groups:

* **Structural** tests need no database at all and guard the Port/Adapter/
  UseCase layout plus the HTTP surface of the memory API.
* **Behavioural** tests run the real repository code against an in-memory
  SQLite database through a tiny async wrapper around a synchronous SQLAlchemy
  session, so per-user isolation, pagination, cascade delete and the read ports
  are exercised without any external service (CI has no PostgreSQL).
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


# --------------------------------------------------------------------------- #
# SQLite harness: AsyncSessionLocal-compatible wrapper around a sync Session
# --------------------------------------------------------------------------- #
class _AsyncSessionShim:
    """Expose the awaited subset of ``AsyncSession`` used by the adapters."""

    def __init__(self, session: Session):
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)

    async def scalar(self, statement):
        return self._session.scalar(statement)

    async def get(self, entity, ident):
        return self._session.get(entity, ident)

    def add(self, instance):
        self._session.add(instance)

    def add_all(self, instances):
        self._session.add_all(list(instances))

    async def commit(self):
        self._session.commit()

    async def refresh(self, instance):
        self._session.refresh(instance)


class _SessionContext:
    def __init__(self, session: Session):
        self._session = session

    async def __aenter__(self) -> _AsyncSessionShim:
        return _AsyncSessionShim(self._session)

    async def __aexit__(self, *exc_info) -> bool:
        self._session.close()
        return False


class _AsyncSessionLocalShim:
    """Drop-in for ``AsyncSessionLocal``: callable -> async context manager."""

    def __init__(self, factory: sessionmaker):
        self._factory = factory

    def __call__(self) -> _SessionContext:
        return _SessionContext(self._factory())


@pytest.fixture()
def sqlite_env(monkeypatch):
    """Point both chat-memory adapters at a fresh in-memory SQLite schema."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    from app.models.orm.chat import ChatConversation, ChatMessage, UserChatProfile

    ChatConversation.__table__.create(engine)
    ChatMessage.__table__.create(engine)
    UserChatProfile.__table__.create(engine)

    factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    shim = _AsyncSessionLocalShim(factory)

    import app.adapters.chat_archive.memory_repository as memory_module
    import app.adapters.chat_archive.user_profile_repository as profile_module

    monkeypatch.setattr(memory_module, "AsyncSessionLocal", shim)
    monkeypatch.setattr(profile_module, "AsyncSessionLocal", shim)
    yield engine
    engine.dispose()


def _store():
    from app.adapters.chat_archive.memory_repository import (
        SqlAlchemyChatMemoryRepositoryAdapter,
    )

    return SqlAlchemyChatMemoryRepositoryAdapter()


ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


# --------------------------------------------------------------------------- #
# Structural contract
# --------------------------------------------------------------------------- #
class TestOrmSchema:
    def test_chat_tables_are_registered_in_metadata(self):
        from app.core.database import Base
        import app.models.orm.chat  # noqa: F401 - registers the tables

        names = set(Base.metadata.tables)
        assert {"chat_conversation", "chat_message", "user_chat_profile"} <= names

    def test_conversation_is_owned_and_indexed(self):
        from app.models.orm.chat import ChatConversation

        table = ChatConversation.__table__
        assert table.c.user_id.nullable is False
        assert any(
            index.name == "ix_chat_conversation_user_updated" for index in table.indexes
        )

    def test_message_carries_owner_and_cascades(self):
        from app.models.orm.chat import ChatMessage

        table = ChatMessage.__table__
        assert table.c.user_id.nullable is False
        fk = list(table.c.conversation_id.foreign_keys)[0]
        assert fk.column.table.name == "chat_conversation"
        assert fk.ondelete == "CASCADE"
        # ``metadata`` is reserved on the declarative class; externally the
        # column must still be named ``metadata``.
        assert table.c.metadata is not None

    def test_init_db_tables_imports_chat_models(self):
        from app.core.database import init_db_tables

        source = inspect.getsource(init_db_tables)
        assert "app.models.orm.chat" in source


class TestPortsAndUsecases:
    def test_store_adapter_implements_both_ports(self):
        adapter = _store()
        for name in (
            "create_conversation",
            "append_messages",
            "list_conversations",
            "list_messages",
            "rename_conversation",
            "delete_conversation",
            "list_message_queries",
            "list_recent_dialogues",
            "list_older_dialogues",
        ):
            assert callable(getattr(adapter, name)), name

    def test_orchestrator_port_only_covers_generation(self):
        from app.ports.outbound.chat import ChatOrchestratorPort

        assert hasattr(ChatOrchestratorPort, "stream_message")
        assert hasattr(ChatOrchestratorPort, "stop")
        for removed in (
            "list_conversations",
            "list_messages",
            "rename_conversation",
            "delete_conversation",
        ):
            assert not hasattr(ChatOrchestratorPort, removed), removed

    @pytest.mark.parametrize(
        "module",
        [
            "app.usecases.chat.create_conversation",
            "app.usecases.chat.append_messages",
            "app.usecases.chat.list_conversations",
            "app.usecases.chat.list_messages",
            "app.usecases.chat.rename_conversation",
            "app.usecases.chat.delete_conversation",
        ],
    )
    def test_usecases_stay_in_their_layer(self, module):
        import importlib

        source = open(
            importlib.import_module(module).__file__, encoding="utf-8"
        ).read()
        assert "app.adapters" not in source
        assert "app.api" not in source

    def test_placeholder_adapter_is_gone(self):
        """The always-empty placeholder must not come back."""
        import app.adapters.chat_archive.memory_repository as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "Returns empty lists until" not in source
        assert "not persisted yet" not in source


class TestHttpSurface:
    @staticmethod
    def _methods_by_path() -> dict:
        from app.api.v1.chat import router

        out: dict[str, set] = {}
        for route in router.routes:
            out.setdefault(route.path, set()).update(route.methods or set())
        return out

    def test_memory_routes_are_live(self):
        routes = self._methods_by_path()
        assert {"GET", "POST"} <= routes["/conversations"]
        assert "POST" in routes["/conversations/{conversation_id}/messages"]
        assert "GET" in routes["/messages"]
        assert "POST" in routes["/conversations/{conversation_id}/name"]
        assert "DELETE" in routes["/conversations/{conversation_id}"]

    def test_generation_routes_are_still_reserved(self):
        routes = self._methods_by_path()
        assert "POST" in routes["/chat-messages"]
        assert "POST" in routes["/chat-messages/{task_id}/stop"]

    def test_crud_handlers_use_the_local_store_not_the_orchestrator(self):
        from app.api.v1 import chat

        crud = (
            chat.create_conversation,
            chat.list_conversations,
            chat.list_messages,
            chat.append_conversation_messages,
            chat.rename_conversation,
            chat.delete_conversation,
        )
        for handler in crud:
            source = inspect.getsource(handler)
            assert "_conversation_store()" in source, handler.__name__
            assert "_orchestrator()" not in source, handler.__name__

        for handler in (chat.send_chat_message, chat.stop_chat_message):
            assert "_orchestrator()" in inspect.getsource(handler)


# --------------------------------------------------------------------------- #
# Behavioural contract (SQLite)
# --------------------------------------------------------------------------- #
class TestConversationStore:
    def test_conversation_is_scoped_to_its_owner(self, sqlite_env):
        import asyncio

        from app.ports.dto.chat import ConversationPageQuery, CreateConversationCommand

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(user_id=ALICE, name="alice-1")
            )
            alice = await store.list_conversations(
                ConversationPageQuery(user_id=ALICE, page=1, limit=20)
            )
            bob = await store.list_conversations(
                ConversationPageQuery(user_id=BOB, page=1, limit=20)
            )
            return alice, bob

        alice, bob = asyncio.run(scenario())
        assert [c.name for c in alice.data] == ["alice-1"]
        assert bob.data == []

    def test_create_is_idempotent_and_generates_ids(self, sqlite_env):
        import asyncio

        from app.ports.dto.chat import CreateConversationCommand

        store = _store()

        async def scenario():
            first = await store.create_conversation(
                CreateConversationCommand(user_id=ALICE, name="n")
            )
            again = await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id=first.id, name="ignored"
                )
            )
            return first, again

        first, again = asyncio.run(scenario())
        assert first.id and len(first.id) >= 32
        assert again.id == first.id
        assert again.name == "n"

    def test_foreign_conversation_id_is_rejected(self, sqlite_env):
        import asyncio

        from app.core.exceptions import PermissionDeniedError
        from app.ports.dto.chat import CreateConversationCommand

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="shared-id", name="alice"
                )
            )
            with pytest.raises(PermissionDeniedError):
                await store.create_conversation(
                    CreateConversationCommand(
                        user_id=BOB, conversation_id="shared-id", name="bob"
                    )
                )

        asyncio.run(scenario())

    def test_append_and_list_roundtrip(self, sqlite_env):
        import asyncio

        from app.ports.dto.chat import (
            AppendMessagesCommand,
            CreateConversationCommand,
            MessageInput,
            MessagePageQuery,
        )

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="c-1", name="c"
                )
            )
            stored = await store.append_messages(
                AppendMessagesCommand(
                    user_id=ALICE,
                    conversation_id="c-1",
                    messages=[
                        MessageInput(
                            role="user", content="问题", query="问题", created_at=1760000000
                        ),
                        MessageInput(
                            role="assistant",
                            content="回答",
                            answer="回答",
                            created_at=1760000010,
                            metadata={"task_id": "t-1"},
                        ),
                    ],
                )
            )
            page = await store.list_messages(
                MessagePageQuery(user_id=ALICE, conversation_id="c-1", page=1, limit=20)
            )
            return stored, page

        stored, page = asyncio.run(scenario())
        assert [m.role for m in stored] == ["user", "assistant"]
        assert [m.created_at for m in page.data] == [1760000000, 1760000010]
        assert page.data[1].metadata == {"task_id": "t-1"}
        assert page.data[0].query == "问题"
        assert page.data[1].answer == "回答"

    def test_append_to_foreign_conversation_is_not_found(self, sqlite_env):
        import asyncio

        from app.core.exceptions import NotFoundError
        from app.ports.dto.chat import (
            AppendMessagesCommand,
            CreateConversationCommand,
            MessageInput,
        )

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="c-1", name="c"
                )
            )
            with pytest.raises(NotFoundError):
                await store.append_messages(
                    AppendMessagesCommand(
                        user_id=BOB,
                        conversation_id="c-1",
                        messages=[MessageInput(role="user", content="x")],
                    )
                )
            with pytest.raises(NotFoundError):
                await store.append_messages(
                    AppendMessagesCommand(
                        user_id=BOB,
                        conversation_id="ghost",
                        messages=[MessageInput(role="user", content="x")],
                    )
                )

        asyncio.run(scenario())

    def test_invalid_role_is_rejected(self, sqlite_env):
        import asyncio

        from app.ports.dto.chat import (
            AppendMessagesCommand,
            CreateConversationCommand,
            MessageInput,
        )

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="c-1", name="c"
                )
            )
            with pytest.raises(ValueError):
                await store.append_messages(
                    AppendMessagesCommand(
                        user_id=ALICE,
                        conversation_id="c-1",
                        messages=[MessageInput(role="robot", content="x")],
                    )
                )

        asyncio.run(scenario())

    def test_messages_paginate_from_the_newest_window(self, sqlite_env):
        import asyncio

        from app.ports.dto.chat import (
            AppendMessagesCommand,
            CreateConversationCommand,
            MessageInput,
            MessagePageQuery,
        )

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="c-1", name="c"
                )
            )
            await store.append_messages(
                AppendMessagesCommand(
                    user_id=ALICE,
                    conversation_id="c-1",
                    messages=[
                        MessageInput(role="user", content=f"m{i:02d}")
                        for i in range(1, 26)
                    ],
                )
            )
            return [
                await store.list_messages(
                    MessagePageQuery(
                        user_id=ALICE, conversation_id="c-1", page=page, limit=10
                    )
                )
                for page in (1, 2, 3, 4)
            ]

        page1, page2, page3, page4 = asyncio.run(scenario())
        assert [m.content for m in page1.data] == [f"m{i:02d}" for i in range(16, 26)]
        assert page1.has_more is True
        assert [m.content for m in page2.data] == [f"m{i:02d}" for i in range(6, 16)]
        assert page2.has_more is True
        assert [m.content for m in page3.data] == [f"m{i:02d}" for i in range(1, 6)]
        assert page3.has_more is False
        assert page4.data == []

    def test_rename_and_delete_cascade(self, sqlite_env):
        import asyncio

        from app.core.exceptions import NotFoundError
        from app.ports.dto.chat import (
            AppendMessagesCommand,
            CreateConversationCommand,
            DeleteConversationCommand,
            MessageInput,
            MessagePageQuery,
            RenameConversationCommand,
        )

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="c-1", name="old"
                )
            )
            await store.append_messages(
                AppendMessagesCommand(
                    user_id=ALICE,
                    conversation_id="c-1",
                    messages=[MessageInput(role="user", content="hello")],
                )
            )
            renamed = await store.rename_conversation(
                RenameConversationCommand(
                    user_id=ALICE, conversation_id="c-1", name="new"
                )
            )
            await store.delete_conversation(
                DeleteConversationCommand(user_id=ALICE, conversation_id="c-1")
            )
            with pytest.raises(NotFoundError):
                await store.list_messages(
                    MessagePageQuery(
                        user_id=ALICE, conversation_id="c-1", page=1, limit=20
                    )
                )
            return renamed

        renamed = asyncio.run(scenario())
        assert renamed.name == "new"

    def test_admin_style_cross_user_read_uses_the_target_id(self, sqlite_env):
        import asyncio

        from app.ports.dto.chat import (
            AppendMessagesCommand,
            ConversationPageQuery,
            CreateConversationCommand,
            MessageInput,
        )

        store = _store()

        async def scenario():
            for user in (ALICE, BOB):
                await store.create_conversation(
                    CreateConversationCommand(
                        user_id=user, conversation_id=f"c-{user[:4]}", name=user[:4]
                    )
                )
                await store.append_messages(
                    AppendMessagesCommand(
                        user_id=user,
                        conversation_id=f"c-{user[:4]}",
                        messages=[MessageInput(role="user", content=f"q-{user[:4]}")],
                    )
                )
            alice = await store.list_conversations(
                ConversationPageQuery(user_id=ALICE, page=1, limit=20)
            )
            bob = await store.list_conversations(
                ConversationPageQuery(user_id=BOB, page=1, limit=20)
            )
            return alice, bob

        alice, bob = asyncio.run(scenario())
        assert [c.id for c in alice.data] == ["c-1111"]
        assert [c.id for c in bob.data] == ["c-2222"]


class TestMessageReadPorts:
    def _seed(self, store):
        import asyncio

        from app.ports.dto.chat import (
            AppendMessagesCommand,
            CreateConversationCommand,
            MessageInput,
        )

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="c-1", name="c"
                )
            )
            messages = []
            for index in range(1, 13):
                if index % 2:
                    messages.append(
                        MessageInput(role="user", content=f"问{index}", query=f"问{index}")
                    )
                else:
                    messages.append(
                        MessageInput(
                            role="assistant", content=f"答{index}", answer=f"答{index}"
                        )
                    )
            await store.append_messages(
                AppendMessagesCommand(
                    user_id=ALICE, conversation_id="c-1", messages=messages
                )
            )

        asyncio.run(scenario())

    def test_message_queries_only_expose_user_turns(self, sqlite_env):
        import asyncio

        store = _store()
        self._seed(store)
        queries = asyncio.run(store.list_message_queries(ALICE, "c-1", 3))
        assert queries == ["问7", "问9", "问11"]

    def test_queries_are_scoped_to_the_owner(self, sqlite_env):
        import asyncio

        store = _store()
        self._seed(store)
        assert asyncio.run(store.list_message_queries(BOB, "c-1", 3)) == []

    def test_recent_and_older_windows_do_not_overlap(self, sqlite_env):
        import asyncio

        store = _store()
        self._seed(store)

        async def scenario():
            recent = await store.list_recent_dialogues(ALICE, "c-1", 2)
            older = await store.list_older_dialogues(ALICE, "c-1", 4, recent=2)
            return recent, older

        recent, older = asyncio.run(scenario())
        assert recent == ["用户: 问11", "助手: 答12"]
        assert older == ["用户: 问7", "助手: 答8", "用户: 问9", "助手: 答10"]
        assert not set(recent) & set(older)

    def test_older_window_returns_nothing_when_history_is_short(self, sqlite_env):
        import asyncio

        from app.ports.dto.chat import (
            AppendMessagesCommand,
            CreateConversationCommand,
            MessageInput,
        )

        store = _store()

        async def scenario():
            await store.create_conversation(
                CreateConversationCommand(
                    user_id=ALICE, conversation_id="c-2", name="c"
                )
            )
            await store.append_messages(
                AppendMessagesCommand(
                    user_id=ALICE,
                    conversation_id="c-2",
                    messages=[MessageInput(role="user", content="only", query="only")],
                )
            )
            return await store.list_older_dialogues(ALICE, "c-2", 10, recent=5)

        assert asyncio.run(scenario()) == []


class TestUserProfileRepository:
    def test_upsert_and_read_latest_summary(self, sqlite_env):
        import asyncio

        from app.adapters.chat_archive.user_profile_repository import (
            SqlAlchemyUserProfileRepositoryAdapter,
        )

        repo = SqlAlchemyUserProfileRepositoryAdapter()

        async def scenario():
            assert await repo.get_latest_summary(ALICE) is None
            assert await repo.upsert_latest_summary(ALICE, "first") is True
            assert await repo.upsert_latest_summary(ALICE, "second") is True
            return await repo.get_latest_summary(ALICE), await repo.get_latest_summary(BOB)

        alice_summary, bob_summary = asyncio.run(scenario())
        assert alice_summary == "second"
        assert bob_summary is None


class TestLlmFailureHandling:
    """The LangChain stack ships in ``requirements-rag.txt`` only, so these
    tests skip wherever it is absent (e.g. the CI runner's minimal install)."""

    def test_summarizer_raises_instead_of_persisting_an_error_string(self):
        """A failed LLM call must never be stored as if it were a profile."""
        import asyncio

        pytest.importorskip("langchain_openai")
        from app.adapters.chat_archive.message_extractor import (
            LlmSummarizationError,
            MessageExtractor,
        )

        class _Repo:
            async def list_message_queries(self, user_id, conversation_id, limit):
                return ["q"]

        extractor = MessageExtractor(
            _Repo(), llm_base_url="http://127.0.0.1:9/v1", timeout=1
        )
        with pytest.raises(LlmSummarizationError):
            asyncio.run(extractor.summarize_queries_with_llm(["q"]))

    def test_empty_query_list_raises(self):
        import asyncio

        pytest.importorskip("langchain_openai")
        from app.adapters.chat_archive.message_extractor import (
            LlmSummarizationError,
            MessageExtractor,
        )

        class _Repo:
            async def list_message_queries(self, user_id, conversation_id, limit):
                return []

        extractor = MessageExtractor(_Repo())
        with pytest.raises(LlmSummarizationError):
            asyncio.run(extractor.summarize_queries_with_llm([]))

    def test_compressor_has_no_hardcoded_llm_defaults(self):
        import pathlib

        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app"
            / "adapters"
            / "chat_archive"
            / "message_extractor.py"
        ).read_text(encoding="utf-8")
        assert "localhost:80" not in source
        assert "settings.QWEN3_6_35B_API_URL" in source

    def test_compression_maps_llm_failure_to_external_service_error(self):
        import asyncio

        pytest.importorskip("langchain_openai")
        from app.core.exceptions import ExternalServiceError
        from app.adapters.context_compression import (
            IntegrationContextCompressorAdapter,
        )
        import app.adapters.context_compression as adapter_module
        import app.adapters.context_compressor as compressor_module

        class _Repo:
            async def list_recent_dialogues(self, **kwargs):
                return []

            async def list_older_dialogues(self, **kwargs):
                return []

        adapter = IntegrationContextCompressorAdapter(_Repo())

        async def _boom(_context_data):
            raise compressor_module.LlmCallFailedError("unreachable")

        original = adapter_module.compress_context
        adapter_module.compress_context = _boom
        try:
            with pytest.raises(ExternalServiceError):
                asyncio.run(adapter.compress({"user_id": ALICE, "conversation_id": "c"}))
        finally:
            adapter_module.compress_context = original
