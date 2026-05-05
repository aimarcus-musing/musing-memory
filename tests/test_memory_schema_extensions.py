"""Schema-level tests for the emotion + recall metadata mixins, memory_edges table,
and the Semantic-bucket discriminator (entry_type + structured_data) introduced
when Procedural was folded into Semantic.

These tests are pure ORM introspection — no database connection required.
They guard against accidental drift in the column set, the per-bucket edge
type policy, the mixin application across models, and the entry_type taxonomy.
"""

import pytest
from pydantic import ValidationError

from mirix.orm import Base, MemoryEdge
from mirix.orm.block import Block
from mirix.orm.enums import EdgeType, MemoryBucket
from mirix.orm.episodic_memory import EpisodicEvent
from mirix.orm.memory_edge import (
    ALLOWED_EDGE_TYPES_BY_SRC_BUCKET,
    is_edge_allowed,
)
from mirix.orm.procedural_memory import ProceduralMemoryItem
from mirix.orm.resource_memory import ResourceMemoryItem
from mirix.orm.semantic_memory import SemanticMemoryItem
from mirix.schemas.semantic_memory import SemanticMemoryItemBase

# --- EmotionContextMixin coverage --------------------------------------------------

EMOTION_COLUMNS = {
    "vad",
    "primary_emotion",
    "scenarios",
    "linguistic_cues",
    "voice_emotion",
}

# Only buckets that participate in decay get recall metadata.
RECALL_COLUMNS = {
    "g_0",
    "consolidation_g_n",
    "last_recalled_at",
    "recall_count",
}


@pytest.mark.parametrize(
    "model",
    [
        EpisodicEvent,
        SemanticMemoryItem,
        ProceduralMemoryItem,
        ResourceMemoryItem,
        Block,
    ],
)
def test_emotion_columns_present(model):
    """All five memory tables carry the EmotionContextMixin columns."""
    cols = {c.name for c in model.__table__.columns}
    missing = EMOTION_COLUMNS - cols
    assert not missing, f"{model.__name__} missing emotion columns: {missing}"


@pytest.mark.parametrize(
    "model",
    [EpisodicEvent, SemanticMemoryItem, ProceduralMemoryItem],
)
def test_recall_metadata_present_on_decaying_buckets(model):
    """Episodic, Semantic, Procedural carry the RecallMetadataMixin columns."""
    cols = {c.name for c in model.__table__.columns}
    missing = RECALL_COLUMNS - cols
    assert not missing, f"{model.__name__} missing recall metadata columns: {missing}"


@pytest.mark.parametrize("model", [ResourceMemoryItem, Block])
def test_recall_metadata_absent_on_non_decaying_buckets(model):
    """Resource and Core (Block) explicitly do NOT carry recall metadata.

    Resource is store-only (precise lookup bypasses decay); Core is always-in-context.
    Adding recall metadata to either would be misleading.
    """
    cols = {c.name for c in model.__table__.columns}
    leaked = RECALL_COLUMNS & cols
    assert not leaked, f"{model.__name__} should not have recall metadata columns but has: {leaked}"


def test_recall_metadata_defaults():
    """g_0 and consolidation_g_n default to 1.0; recall_count defaults to 0."""
    cols = {c.name: c for c in EpisodicEvent.__table__.columns}
    assert cols["g_0"].default.arg == 1.0
    assert cols["consolidation_g_n"].default.arg == 1.0
    assert cols["recall_count"].default.arg == 0
    # last_recalled_at is nullable, no default
    assert cols["last_recalled_at"].nullable is True
    assert cols["last_recalled_at"].default is None


def test_emotion_columns_are_nullable():
    """All emotion fields are optional — not every memory has full emotion context."""
    for model in [
        EpisodicEvent,
        SemanticMemoryItem,
        ProceduralMemoryItem,
        ResourceMemoryItem,
        Block,
    ]:
        cols = {c.name: c for c in model.__table__.columns}
        for col_name in EMOTION_COLUMNS:
            assert cols[col_name].nullable is True, f"{model.__name__}.{col_name} should be nullable"


# --- MemoryEdge schema -------------------------------------------------------------


def test_memory_edge_table_registered():
    """The memory_edges table is registered with Base.metadata so DDL runs on startup."""
    assert "memory_edges" in Base.metadata.tables


def test_memory_edge_required_columns():
    """All edge columns required by the design are present and correctly typed."""
    cols = {c.name: c for c in MemoryEdge.__table__.columns}
    expected = {
        "id",
        "src_memory_id",
        "src_bucket",
        "dst_memory_id",
        "dst_bucket",
        "edge_type",
        "confidence",
        "source",
        "client_id",
        "user_id",
        "organization_id",
    }
    missing = expected - set(cols)
    assert not missing, f"memory_edges missing columns: {missing}"

    # confidence defaults to 1.0
    assert cols["confidence"].default.arg == 1.0

    # Bucket / edge_type discriminators are NOT NULL
    assert cols["src_bucket"].nullable is False
    assert cols["dst_bucket"].nullable is False
    assert cols["edge_type"].nullable is False


def test_memory_edge_indexes_present():
    """The three core traversal indexes exist (forward, reverse, typed-by-user)."""
    index_names = {i.name for i in MemoryEdge.__table__.indexes}
    assert "ix_memory_edges_src" in index_names
    assert "ix_memory_edges_dst" in index_names
    assert "ix_memory_edges_user_type" in index_names


# --- Per-bucket edge type policy ----------------------------------------------------


def test_episodic_allows_full_set():
    """Episodic memories — being temporal — can express every edge type."""
    allowed = ALLOWED_EDGE_TYPES_BY_SRC_BUCKET[MemoryBucket.EPISODIC]
    assert allowed == {
        EdgeType.CAUSE,
        EdgeType.CHANGED,
        EdgeType.HINDERED_BY,
        EdgeType.TIME_ORDER,
        EdgeType.REASON,
        EdgeType.REACT,
        EdgeType.WANT,
    }


def test_semantic_allows_want_reason_react():
    """Semantic facts express desires, reasons, and characteristic responses."""
    allowed = ALLOWED_EDGE_TYPES_BY_SRC_BUCKET[MemoryBucket.SEMANTIC]
    assert allowed == {EdgeType.WANT, EdgeType.REASON, EdgeType.REACT}


def test_core_allows_want_react_only():
    """Core memories represent stable tendencies — wants and characteristic reactions.

    Time-bound edge types (CAUSE, TIME_ORDER, etc.) don't make sense for
    long-standing personal characteristics.
    """
    allowed = ALLOWED_EDGE_TYPES_BY_SRC_BUCKET[MemoryBucket.CORE]
    assert allowed == {EdgeType.WANT, EdgeType.REACT}
    assert EdgeType.CAUSE not in allowed
    assert EdgeType.TIME_ORDER not in allowed


def test_resource_allows_only_reason():
    """Resources (documents/media) can give reason for beliefs but don't cause events."""
    allowed = ALLOWED_EDGE_TYPES_BY_SRC_BUCKET[MemoryBucket.RESOURCE]
    assert allowed == {EdgeType.REASON}


def test_procedural_matches_semantic_policy():
    """Procedural is being folded into Semantic — keep the policies aligned to avoid churn."""
    procedural = ALLOWED_EDGE_TYPES_BY_SRC_BUCKET[MemoryBucket.PROCEDURAL]
    semantic = ALLOWED_EDGE_TYPES_BY_SRC_BUCKET[MemoryBucket.SEMANTIC]
    assert procedural == semantic


def test_is_edge_allowed_positive_cases():
    assert is_edge_allowed(MemoryBucket.EPISODIC, EdgeType.CAUSE)
    assert is_edge_allowed(MemoryBucket.EPISODIC, EdgeType.WANT)
    assert is_edge_allowed(MemoryBucket.SEMANTIC, EdgeType.WANT)
    assert is_edge_allowed(MemoryBucket.CORE, EdgeType.REACT)
    assert is_edge_allowed(MemoryBucket.RESOURCE, EdgeType.REASON)


def test_is_edge_allowed_blocks_invalid_combos():
    # Core can't cause things — those are episodic
    assert not is_edge_allowed(MemoryBucket.CORE, EdgeType.CAUSE)
    # Semantic isn't temporal
    assert not is_edge_allowed(MemoryBucket.SEMANTIC, EdgeType.TIME_ORDER)
    # Resource is reference data, can't actively cause
    assert not is_edge_allowed(MemoryBucket.RESOURCE, EdgeType.CAUSE)
    # Resource doesn't express wants
    assert not is_edge_allowed(MemoryBucket.RESOURCE, EdgeType.WANT)


def test_all_buckets_have_a_policy():
    """Every value in MemoryBucket has an allow-list (no silent drops)."""
    for bucket in MemoryBucket:
        assert bucket in ALLOWED_EDGE_TYPES_BY_SRC_BUCKET, f"{bucket.value} has no edge policy registered"


# --- Semantic-bucket discriminator (entry_type + structured_data) ---
# These guard the bucket consolidation: Procedural was folded into Semantic via
# entry_type='procedure'. A regression in this taxonomy would silently break
# routing for procedural content.


def test_semantic_memory_has_entry_type_column():
    """SemanticMemoryItem ORM has the entry_type discriminator with default 'fact'."""
    cols = {c.name: c for c in SemanticMemoryItem.__table__.columns}
    assert "entry_type" in cols, "SemanticMemoryItem missing entry_type column"
    assert cols["entry_type"].nullable is False
    assert cols["entry_type"].default.arg == "fact"


def test_semantic_memory_has_structured_data_column():
    """SemanticMemoryItem ORM has the JSON structured_data column for procedure steps."""
    cols = {c.name: c for c in SemanticMemoryItem.__table__.columns}
    assert "structured_data" in cols, "SemanticMemoryItem missing structured_data column"
    assert cols["structured_data"].nullable is True


def test_semantic_entry_type_index_declared_for_postgres():
    """The (organization_id, entry_type) index is declared in source for pg deployments.

    Indexes on this model are gated behind `settings.mirix_pg_uri_no_default`, so
    at runtime they only attach to the Table when running against Postgres. To
    keep this test DB-agnostic, we assert the index is declared in the ORM source
    rather than that it's currently bound to the SQLite Table.
    """
    import inspect

    from mirix.orm import semantic_memory as sm_module

    source = inspect.getsource(sm_module)
    assert (
        "ix_semantic_memory_org_entry_type" in source
    ), "Source-level pg index for (organization_id, entry_type) is missing — entry_type filters will full-scan in prod"


def test_semantic_pydantic_default_entry_type_is_fact():
    """The Pydantic schema defaults entry_type to 'fact' so existing callers don't break."""
    item = SemanticMemoryItemBase(
        name="X",
        summary="s",
        details="d",
        source="user",
    )
    assert item.entry_type == "fact"
    assert item.structured_data is None


def test_semantic_pydantic_accepts_procedure_with_steps():
    """A procedure entry with structured steps validates."""
    item = SemanticMemoryItemBase(
        name="Reset router",
        summary="Steps to reset the home router",
        details="Standard router-reset procedure",
        source="user",
        entry_type="procedure",
        structured_data={"steps": [{"order": 1, "action": "Unplug"}, {"order": 2, "action": "Wait 10s"}]},
    )
    assert item.entry_type == "procedure"
    assert item.structured_data["steps"][0]["action"] == "Unplug"


def test_semantic_pydantic_rejects_unknown_entry_type():
    """Invalid entry_type values are rejected at schema validation time."""
    with pytest.raises(ValidationError):
        SemanticMemoryItemBase(
            name="X",
            summary="s",
            details="d",
            source="user",
            entry_type="not_a_real_kind",
        )


def test_semantic_pydantic_accepts_all_taxonomy_values():
    """All four entry_type values in the taxonomy are accepted."""
    for kind in ("fact", "concept", "entity", "procedure"):
        item = SemanticMemoryItemBase(
            name=f"X-{kind}",
            summary="s",
            details="d",
            source="user",
            entry_type=kind,
        )
        assert item.entry_type == kind
