"""Immutable public-field projections, never authentication or account data."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """An observation row, not a permanent lecture identity.

    UTC instants are integer microseconds from the Unix epoch. Source offsets
    are whole seconds, not an inferred IANA timezone. Event details are excluded
    from repr so ordinary diagnostic formatting does not expose a timetable.
    """

    title: str = field(repr=False)
    start_utc_us: int = field(repr=False)
    end_utc_us: int = field(repr=False)
    start_offset_seconds: int = field(repr=False)
    end_offset_seconds: int = field(repr=False)
    building: str | None = field(repr=False)
    room: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class SnapshotCandidate:
    """Structurally valid projected data; acceptance requires later assessment.

    Tuple order and duplicate multiplicity are preserved here. Content hashing,
    baseline policy and persistence are deliberately outside this model.
    """

    events: tuple[NormalizedEvent, ...] = field(repr=False)
    event_count: int = field(init=False)
    normalization_version: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, NormalizedEvent) for event in self.events
        ):
            raise TypeError("candidate_requires_immutable_event_tuple")
        object.__setattr__(self, "event_count", len(self.events))
