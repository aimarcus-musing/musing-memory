"""Typed connections between memory rows.

Memories form a graph: an episodic event can `cause` a change in semantic
beliefs; a core trait can `reason` for a procedural workflow; a resource
can `react_to` an episodic event. The graph is per-user (and optionally
per-org), and the source bucket determines which edge types are allowed.

We intentionally do NOT use SQL foreign keys to the memory tables — SQL
cannot FK to a union of parent tables, and we want a single edges table
spanning all four buckets. Referential integrity is maintained at the
manager layer (assertions on bucket+id existence, cleanup on delete).

Edge type policy (enforced by ``MemoryEdgeManager`` at write time):

    Source bucket    | Allowed edge types
    -----------------+---------------------------------------------------
    EPISODIC         | CAUSE, CHANGED, HINDERED_BY, TIME_ORDER, REASON,
                     | REACT, WANT  (the full set)
    SEMANTIC         | WANT, REASON, REACT
    PROCEDURAL       | WANT, REASON, REACT  (matches semantic; folded later)
    CORE             | WANT, REACT  (long-standing tendencies + values)
    RESOURCE         | REASON  (a resource gives reason for a belief/event)

Destination bucket has no policy constraint — any edge type can target
any bucket. The asymmetry reflects how connections actually originate:
an episodic event might cause a semantic shift, but a core value
doesn't "cause" anything in the temporal sense — it's expressed via
characteristic reactions to the world.
"""

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from mirix.orm.enums import EdgeType, MemoryBucket
from mirix.orm.mixins import OrganizationMixin, UserMixin
from mirix.orm.sqlalchemy_base import SqlalchemyBase
from mirix.settings import settings

if TYPE_CHECKING:
    from mirix.orm.organization import Organization
    from mirix.orm.user import User


class MemoryEdge(SqlalchemyBase, OrganizationMixin, UserMixin):
    """A typed connection between two memory rows."""

    __tablename__ = "memory_edges"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        doc="Unique edge ID",
    )

    # Source endpoint (polymorphic — bucket discriminator + opaque id)
    src_memory_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="ID of the source memory row",
    )
    src_bucket: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Bucket of the source memory (see MemoryBucket enum)",
    )

    # Destination endpoint
    dst_memory_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="ID of the destination memory row",
    )
    dst_bucket: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Bucket of the destination memory (see MemoryBucket enum)",
    )

    edge_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Type of relationship (see EdgeType enum)",
    )

    # 0..1 — used to weight graph traversals during retrieval. Defaults to 1.0
    # for explicit user-asserted edges; LLM-extracted edges are typically lower.
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        doc="Confidence in this edge (0..1)",
    )

    # Provenance — how the edge was created. Free-form string; common values:
    # "user_explicit", "llm_extraction", "consolidation", "egocentric".
    source: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        default=None,
        doc="Provenance of this edge (e.g. 'llm_extraction', 'user_explicit')",
    )

    # Optional FK to client (for cleanup when a client is removed).
    client_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=True,
        doc="Client that authored this edge",
    )

    __table_args__ = tuple(
        filter(
            None,
            [
                # Forward traversal: "what does memory X point to?"
                Index(
                    "ix_memory_edges_src",
                    "src_bucket",
                    "src_memory_id",
                ),
                # Reverse traversal: "what points to memory Y?"
                Index(
                    "ix_memory_edges_dst",
                    "dst_bucket",
                    "dst_memory_id",
                ),
                # Typed traversal scoped to a user (the common retrieval case)
                Index(
                    "ix_memory_edges_user_type",
                    "user_id",
                    "edge_type",
                ),
                # Org-scoped index for admin / multi-tenant queries
                (
                    Index("ix_memory_edges_organization_id", "organization_id")
                    if settings.mirix_pg_uri_no_default
                    else None
                ),
            ],
        )
    )

    @declared_attr
    def organization(cls) -> Mapped["Organization"]:
        return relationship("Organization", lazy="selectin")

    @declared_attr
    def user(cls) -> Mapped["User"]:
        return relationship("User", lazy="selectin")

    # ---- Convenience accessors -------------------------------------------------

    @property
    def src_bucket_enum(self) -> MemoryBucket:
        return MemoryBucket(self.src_bucket)

    @property
    def dst_bucket_enum(self) -> MemoryBucket:
        return MemoryBucket(self.dst_bucket)

    @property
    def edge_type_enum(self) -> EdgeType:
        return EdgeType(self.edge_type)


# ---- Per-bucket edge type policy -------------------------------------------------
#
# This is the source-side allow-list. Application code (the edge manager) checks
# this before inserting a new edge. Centralized here so the policy is grep-able
# and easy to update.

ALLOWED_EDGE_TYPES_BY_SRC_BUCKET: dict[MemoryBucket, set[EdgeType]] = {
    MemoryBucket.EPISODIC: {
        EdgeType.CAUSE,
        EdgeType.CHANGED,
        EdgeType.HINDERED_BY,
        EdgeType.TIME_ORDER,
        EdgeType.REASON,
        EdgeType.REACT,
        EdgeType.WANT,
    },
    MemoryBucket.SEMANTIC: {
        EdgeType.WANT,
        EdgeType.REASON,
        EdgeType.REACT,
    },
    # Procedural matches semantic — both will be folded into the same bucket
    # in the next milestone, so keeping the policies aligned avoids churn.
    MemoryBucket.PROCEDURAL: {
        EdgeType.WANT,
        EdgeType.REASON,
        EdgeType.REACT,
    },
    MemoryBucket.CORE: {
        EdgeType.WANT,
        EdgeType.REACT,
    },
    MemoryBucket.RESOURCE: {
        EdgeType.REASON,
    },
}


def is_edge_allowed(src_bucket: MemoryBucket, edge_type: EdgeType) -> bool:
    """Check whether an edge of the given type is allowed from the given source bucket."""
    return edge_type in ALLOWED_EDGE_TYPES_BY_SRC_BUCKET.get(src_bucket, set())
