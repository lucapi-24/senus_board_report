"""Document lifecycle state machine.

    uploaded → processing → extracted → in_review → published
                    ↘ error (from any state; ``error → processing`` retries)

Kept small and pure so the transitions can be unit-tested and reused by both
the API and the ingestion service.
"""

from typing import FrozenSet, Set

UPLOADED = "uploaded"
PROCESSING = "processing"
EXTRACTED = "extracted"
IN_REVIEW = "in_review"
PUBLISHED = "published"
ERROR = "error"

STATES: Set[str] = {UPLOADED, PROCESSING, EXTRACTED, IN_REVIEW, PUBLISHED, ERROR}

TRANSITIONS: dict[str, FrozenSet[str]] = {
    UPLOADED: frozenset({PROCESSING, ERROR}),
    PROCESSING: frozenset({EXTRACTED, ERROR}),
    EXTRACTED: frozenset({IN_REVIEW, PUBLISHED, PROCESSING, ERROR}),
    IN_REVIEW: frozenset({PUBLISHED, EXTRACTED, ERROR}),
    PUBLISHED: frozenset({IN_REVIEW, ERROR}),
    ERROR: frozenset({PROCESSING}),
}


class InvalidTransition(ValueError):
    def __init__(self, current: str, target: str):
        super().__init__(f"Cannot move document from '{current}' to '{target}'")
        self.current = current
        self.target = target


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def advance(current: str, target: str) -> str:
    """Return ``target`` if the move is legal, else raise InvalidTransition."""
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target
