from enum import Enum

# Import ToolType from schemas (moved to avoid circular imports)
from mirix.schemas.enums import ToolType  # noqa: F401


class JobType(str, Enum):
    JOB = "job"
    RUN = "run"


class ToolSourceType(str, Enum):
    """Defines what a tool was derived from"""

    python = "python"
    json = "json"


class AccessType(str, Enum):
    """Defines the access scope for ORM operations"""

    ORGANIZATION = "organization"
    USER = "user"


class MemoryBucket(str, Enum):
    """Identifies which memory bucket a row belongs to.

    Used as a discriminator on memory_edges so a single edges table can
    reference rows across the four memory tables without violating
    referential integrity at the DB layer (SQL can't FK to a union of
    parent tables). Application-level integrity is enforced by managers.
    """

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"  # folded into semantic post-bucket-consolidation
    RESOURCE = "resource"
    CORE = "core"


class EdgeType(str, Enum):
    """Typed connections between memory rows.

    The full set of edge types; per-bucket policies (which types are
    allowed when the *source* memory is in a given bucket) are enforced
    by the manager layer at write time. See the plan's connection-graph
    section for bucket-specific policies.
    """

    CAUSE = "cause"  # A caused B
    CHANGED = "changed"  # A changed B (state transition)
    HINDERED_BY = "hindered_by"  # A was hindered by B
    TIME_ORDER = "time_order"  # A happened before B (episodic only)
    REASON = "reason"  # A is the reason for B
    REACT = "react"  # A reacts to / is the way one responds to B
    WANT = "want"  # A wants / desires B
