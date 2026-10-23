"""Optional limits: None means unlimited; zero remains a real limit."""


def minimum_limit(*limits: int | None) -> int | None:
    return min((value for value in limits if value is not None), default=None)


def exceeds_limit(value: int | None, *limits: int | None) -> bool:
    limit = minimum_limit(*limits)
    return limit is not None and (value is None or value > limit)
