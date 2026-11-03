"""Bounded Unicode shadow text with positions in the unchanged original text."""

from __future__ import annotations

import re
import unicodedata
from array import array
from dataclasses import dataclass, field
from io import StringIO

_MAX_SHADOW_CHARACTERS = 5 * 1024 * 1024
_MAX_COMBINING_CHARACTERS = 1024
_BUFFER_CHARACTERS = 4096
_TEXT_RUNS = re.compile(r"[\x00-\x7f]+|[^\x00-\x7f]")


class NormalizationError(ValueError):
    """Reject unusually complex text without exposing any of its contents."""

    def __init__(self) -> None:
        super().__init__("Text exceeds Unicode normalization limits.")


@dataclass(frozen=True)
class NormalizedText:
    """A normalized shadow and compact, possibly reordered original spans."""

    text: str = field(repr=False)
    _starts: array[int] | None = field(default=None, repr=False)
    _ends: array[int] | None = field(default=None, repr=False)

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """Cover every source character contributing to a nonempty shadow match."""
        if not 0 <= start < end <= len(self.text):
            raise ValueError("Invalid normalized span.")
        if self._starts is None or self._ends is None:
            return start, end
        # Canonical ordering can reverse source positions. Expansion can assign
        # the same source span to several output characters; both are intentional.
        return min(self._starts[start:end]), max(self._ends[start:end])


# (decomposed scalar, original start, original exclusive end)
_Scalar = tuple[str, int, int]


def _compose(run: list[_Scalar]) -> list[_Scalar]:
    """Canonically order one bounded run and compose it while retaining origins."""
    if len(run) == 1:
        return run
    ordered = sorted(run, key=lambda item: unicodedata.combining(item[0]))
    result: list[_Scalar] = []
    starter: int | None = None
    last_class = 0
    for char, start, end in ordered:
        combining = unicodedata.combining(char)
        if starter is not None and (last_class < combining or last_class == 0):
            previous, previous_start, previous_end = result[starter]
            composite = unicodedata.normalize("NFC", previous + char)
            if len(composite) == 1:
                result[starter] = (
                    composite,
                    min(previous_start, start),
                    max(previous_end, end),
                )
                continue
        if combining == 0:
            starter = len(result)
        result.append((char, start, end))
        last_class = combining
    return result


def _normalize(text: str, *, positions: bool) -> NormalizedText:
    if len(text) > _MAX_SHADOW_CHARACTERS:
        raise NormalizationError()
    if text.isascii():
        return NormalizedText(text)

    starts: array[int] | None = array("I") if positions else None
    ends: array[int] | None = array("I") if positions else None
    output = StringIO()
    buffer: list[str] = []
    emitted = 0
    run: list[_Scalar] = []

    def emit(scalars: list[_Scalar]) -> None:
        nonlocal emitted
        emitted += len(scalars)
        if emitted > _MAX_SHADOW_CHARACTERS:
            raise NormalizationError()
        for char, start, end in scalars:
            buffer.append(char)
            if starts is not None and ends is not None:
                starts.append(start)
                ends.append(end)
        if len(buffer) >= _BUFFER_CHARACTERS:
            output.write("".join(buffer))
            buffer.clear()

    def feed(char: str, index: int) -> None:
        nonlocal run
        scalar = (char, index, index + 1)
        if unicodedata.combining(char) == 0 and run:
            composed = _compose(run)
            # Hangul and several vowel signs compose across class-zero
            # boundaries. Test the normalized starter, not its NFKD tail.
            previous, start, end = composed[0]
            pair = (
                unicodedata.normalize("NFC", previous + char)
                if len(composed) == 1 and not (previous.isascii() and char.isascii())
                else ""
            )
            if len(pair) == 1:
                run = [(pair, min(start, index), max(end, index + 1))]
                return
            emit(composed)
            run = []
        run.append(scalar)
        if len(run) > _MAX_COMBINING_CHARACTERS:
            raise NormalizationError()

    for match in _TEXT_RUNS.finditer(text):
        original = match.group()
        index = match.start()
        if original.isascii():
            # Only the first/last ASCII starters can interact with neighboring
            # Unicode. Bulk-copy the middle without per-character NFC work.
            feed(original[0], index)
            if len(original) > 1:
                emit(run)
                run = [(original[-1], match.end() - 1, match.end())]
                middle_start, middle_end = index + 1, match.end() - 1
                emitted += middle_end - middle_start
                if emitted > _MAX_SHADOW_CHARACTERS:
                    raise NormalizationError()
                if buffer:
                    output.write("".join(buffer))
                    buffer.clear()
                output.write(original[1:-1])
                if starts is not None and ends is not None:
                    starts.extend(range(middle_start, middle_end))
                    ends.extend(range(middle_start + 1, middle_end + 1))
            continue
        if unicodedata.category(original) != "Cf":
            for char in unicodedata.normalize("NFKD", original):
                if unicodedata.category(char) != "Cf":
                    feed(char, index)
    if run:
        emit(_compose(run))
    if buffer:
        output.write("".join(buffer))
    return NormalizedText(output.getvalue(), starts, ends)


def normalize_shadow(text: str) -> NormalizedText:
    """Remove format characters, apply NFKC, and preserve original match spans.

    Canonical ordering is bounded and stable; no growing prefixes are repeatedly
    normalized. ASCII returns an identity view without allocating position maps.
    The source text is never changed. NFKC also maps NBSP and narrow NBSP to spaces.
    """
    return _normalize(text, positions=True)


def normalize_text(text: str) -> str:
    """Apply the same normalization without allocating source-position arrays."""
    return _normalize(text, positions=False).text
