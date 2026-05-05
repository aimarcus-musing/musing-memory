from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from mirix.orm.base import Base


def is_valid_uuid4(uuid_string: str) -> bool:
    """Check if a string is a valid UUID4."""
    try:
        uuid_obj = UUID(uuid_string)
        return uuid_obj.version == 4
    except ValueError:
        return False


class OrganizationMixin(Base):
    """Mixin for models that belong to an organization."""

    __abstract__ = True

    organization_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("organizations.id"), nullable=True)


class UserMixin(Base):
    """Mixin for models that belong to a user."""

    __abstract__ = True

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))


class AgentMixin(Base):
    """Mixin for models that belong to an agent."""

    __abstract__ = True

    agent_id: Mapped[str] = mapped_column(String, ForeignKey("agents.id", ondelete="CASCADE"))


class EmotionContextMixin(Base):
    """Emotion signals attached to a memory at write time.

    Sourced from the upstream emotion pipeline (VAD, primary discrete emotion,
    detected scenarios, linguistic cues). For audio inputs that go through
    transcription, ``voice_emotion`` carries the prosodic/paralinguistic
    features so the memory captures *how* something was said in addition to
    *what* was said. All fields are nullable — not every memory will have
    full emotion context (e.g., backfilled memories from documents).
    """

    __abstract__ = True

    # Valence/Arousal/Dominance + confidence as a JSON dict, e.g.:
    # {"v": 0.62, "a": 0.55, "d": 0.48, "confidence": 0.81}
    vad: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        doc="VAD score at write time (valence, arousal, dominance, confidence)",
    )

    # Primary discrete emotion classification (e.g., 'joy', 'anxiety', 'frustration').
    primary_emotion: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        doc="Primary discrete emotion at write time",
    )

    # List of scenario tags detected by the emotion pipeline (stored as JSON list
    # for cross-DB compatibility with SQLite).
    scenarios: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        doc="Scenario tags detected at write time",
    )

    # Quantified linguistic cues (hedging, urgency, certainty, etc.) as a JSON dict.
    linguistic_cues: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        doc="Quantified linguistic micro-signals at write time",
    )

    # Voice-derived prosodic/paralinguistic features (for memories sourced from audio).
    # Captures tone, pace, breathiness, etc. — what the words don't carry.
    voice_emotion: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        doc="Voice-derived emotional/prosodic features for audio-sourced memories",
    )


class RecallMetadataMixin(Base):
    """Recall and consolidation state for memories that participate in decay.

    Implements the Hou et al. 2024 recall-strengthened consolidation:
        p_n(t) = (1 - exp(-r_eff * e^(-t/g_n))) / (1 - e^-1)
        g_n   = g_{n-1} + (1 - e^-t) / (1 + e^-t)

    ``g_0`` captures the initial consolidation strength, boosted at write time
    when emotional salience is high (per our extension of the model). ``g_n``
    increments on each recall via the sigmoid above. ``last_recalled_at`` and
    ``recall_count`` track the history needed to compute ``t`` (elapsed time
    since last recall) and ``n`` (number of recalls).

    Applied to Episodic, Semantic, and Procedural memories. NOT applied to
    Core or Resource — Core never decays, Resource is store-only with a
    direct lookup path bypassing decay.
    """

    __abstract__ = True

    g_0: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        doc="Initial consolidation strength at write time (boosted by emotional salience)",
    )

    consolidation_g_n: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        doc="Current consolidation strength (g_n in Hou et al. 2024); strengthened on each recall",
    )

    last_recalled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        default=None,
        doc="When this memory was last successfully recalled (drives elapsed-time term in decay)",
    )

    recall_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of times this memory has been recalled (n in Hou et al. 2024)",
    )
