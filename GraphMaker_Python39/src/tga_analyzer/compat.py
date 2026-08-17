"""Small compatibility helpers used by the Python 3.9 build."""

from typing import Any, Iterable, Iterator, List, Tuple


def strict_zip(*iterables: Iterable[Any], context: str = "parallel sequences") -> Iterator[Tuple[Any, ...]]:
    """Zip iterables after explicitly validating that their lengths match.

    Python 3.9 does not provide the newer strict mode of ``zip``. This helper
    keeps the original validation behavior and raises a concrete error before
    any paired processing starts.
    """

    normalized: List[Iterable[Any]] = []
    lengths: List[int] = []
    for iterable in iterables:
        try:
            length = len(iterable)  # type: ignore[arg-type]
            normalized.append(iterable)
        except TypeError:
            materialized = tuple(iterable)
            length = len(materialized)
            normalized.append(materialized)
        lengths.append(length)

    if len(set(lengths)) > 1:
        rendered = ", ".join(str(length) for length in lengths)
        raise ValueError(
            f"{context}: sequence length mismatch ({rendered})"
        )
    return zip(*normalized)
