import datetime as dt
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from mirix.constants import MAX_EMBEDDING_DIM
from mirix.orm.custom_columns import CommonVector, EmbeddingConfigColumn
from mirix.orm.mixins import (
    EmotionContextMixin,
    OrganizationMixin,
    RecallMetadataMixin,
    UserMixin,
)
from mirix.orm.sqlalchemy_base import SqlalchemyBase
from mirix.schemas.semantic_memory import SemanticMemoryItem as PydanticSemanticMemoryItem
from mirix.settings import settings

if TYPE_CHECKING:
    from mirix.orm.agent import Agent
    from mirix.orm.organization import Organization
    from mirix.orm.user import User


class SemanticMemoryItem(
    SqlalchemyBase,
    OrganizationMixin,
    UserMixin,
    EmotionContextMixin,
    RecallMetadataMixin,
):
    """
    Stores semantic memory entries that represent general knowledge,
    concepts, facts, and language elements that can be accessed without
    relying on specific contextual experiences.

    Attributes:
        id: Unique ID for this semantic memory entry.
        name: The name of the concept or the object (e.g., "MemoryLLM", "Jane").
        summary: A concise summary of the concept or the object.
        details: A more detailed explanation or contextual description.
        source: The reference or origin of the information (e.g., book, article, movie).
        created_at: Timestamp indicating when the entry was created.
    """

    __tablename__ = "semantic_memory"
    __pydantic_model__ = PydanticSemanticMemoryItem

    # Primary key
    id: Mapped[str] = mapped_column(String, primary_key=True, doc="Unique ID for this semantic memory entry")

    # Foreign key to agent
    agent_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
        doc="ID of the agent this semantic memory item belongs to",
    )

    # Foreign key to client (for access control and filtering)
    client_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=True,
        doc="ID of the client application that created this item",
    )

    # The name of the concept or the object
    name: Mapped[str] = mapped_column(String, doc="The title or main concept for the knowledge entry")

    # A concise summary of the concept
    summary: Mapped[str] = mapped_column(String, doc="A concise summary of the concept or the object.")

    # Detailed explanation or extended context about the concept
    details: Mapped[str] = mapped_column(String, doc="Detailed explanation or additional context for the concept")

    # Reference or source of the general knowledge (e.g., book, article, or movie)
    source: Mapped[str] = mapped_column(
        String,
        doc="The reference or origin of this information (e.g., book, article, or movie)",
    )

    # Discriminator: what kind of semantic entry this is.
    # We've folded MIRIX's separate Procedural bucket into Semantic via this column —
    # workflows, how-tos, and step-by-step guides live here as entry_type='procedure'
    # with their structured steps in `structured_data`. Defaults to 'fact' for backward
    # compatibility with existing rows that predate this column.
    # Allowed values: 'fact' | 'concept' | 'entity' | 'procedure'.
    entry_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="fact",
        server_default="fact",
        doc="Kind of semantic entry (fact, concept, entity, procedure)",
    )

    # Generic structured payload for entries that need it. Used today by entry_type
    # 'procedure' to hold a list of step dicts, e.g.:
    #   {"steps": [{"order": 1, "action": "Open the form", ...}, ...]}
    # Kept generic (not a typed `steps` column) so future entry_type variants can
    # use it without schema changes — e.g., 'entity' might store relationship triples.
    structured_data: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        doc="Optional structured payload (e.g., procedure steps for entry_type='procedure')",
    )

    # NEW: Filter tags for flexible filtering and categorization
    filter_tags: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, default=None, doc="Custom filter tags for filtering and categorization"
    )

    # When was this item last modified and what operation?
    last_modify: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {
            "timestamp": datetime.now(dt.timezone.utc).isoformat(),
            "operation": "created",
        },
        doc="Last modification info including timestamp and operation type",
    )

    # Timestamp indicating when this entry was created
    created_at: Mapped[DateTime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(dt.timezone.utc),
        nullable=False,
        doc="Timestamp when this semantic memory entry was created",
    )

    embedding_config: Mapped[Optional[dict]] = mapped_column(
        EmbeddingConfigColumn, nullable=True, doc="Embedding configuration"
    )

    # Vector embedding field based on database type
    if settings.mirix_pg_uri_no_default:
        from pgvector.sqlalchemy import Vector

        details_embedding = mapped_column(Vector(MAX_EMBEDDING_DIM), nullable=True)
        name_embedding = mapped_column(Vector(MAX_EMBEDDING_DIM), nullable=True)
        summary_embedding = mapped_column(Vector(MAX_EMBEDDING_DIM), nullable=True)
    else:
        details_embedding = Column(CommonVector, nullable=True)
        name_embedding = Column(CommonVector, nullable=True)
        summary_embedding = Column(CommonVector, nullable=True)

    # Database indexes for efficient querying
    __table_args__ = tuple(
        filter(
            None,
            [
                # Organization-level query optimization indexes
                (
                    Index("ix_semantic_memory_organization_id", "organization_id")
                    if settings.mirix_pg_uri_no_default
                    else None
                ),
                # Filter by entry_type within an org/user (used when querying just
                # procedures, just facts, etc.).
                (
                    Index(
                        "ix_semantic_memory_org_entry_type",
                        "organization_id",
                        "entry_type",
                    )
                    if settings.mirix_pg_uri_no_default
                    else None
                ),
                (
                    Index(
                        "ix_semantic_memory_org_created_at",
                        "organization_id",
                        "created_at",
                        postgresql_using="btree",
                    )
                    if settings.mirix_pg_uri_no_default
                    else None
                ),
                (
                    Index(
                        "ix_semantic_memory_filter_tags_gin",
                        text("(filter_tags::jsonb)"),
                        postgresql_using="gin",
                    )
                    if settings.mirix_pg_uri_no_default
                    else None
                ),
                (
                    Index(
                        "ix_semantic_memory_org_filter_scope",
                        "organization_id",
                        text("((filter_tags->>'scope')::text)"),
                        postgresql_using="btree",
                    )
                    if settings.mirix_pg_uri_no_default
                    else None
                ),
                # SQLite indexes
                (
                    Index("ix_semantic_memory_organization_id_sqlite", "organization_id")
                    if not settings.mirix_pg_uri_no_default
                    else None
                ),
            ],
        )
    )

    @declared_attr
    def agent(cls) -> Mapped[Optional["Agent"]]:
        """
        Relationship to the Agent that owns this semantic memory item.
        """
        return relationship("Agent", lazy="selectin")

    @declared_attr
    def organization(cls) -> Mapped["Organization"]:
        """
        Relationship to organization, mirroring existing patterns.
        Adjust 'back_populates' to match the collection name in your `Organization` model.
        """
        return relationship("Organization", back_populates="semantic_memory", lazy="selectin")

    @declared_attr
    def user(cls) -> Mapped["User"]:
        """
        Relationship to the User that owns this semantic memory item.
        """
        return relationship("User", lazy="selectin")
