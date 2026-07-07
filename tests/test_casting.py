"""
Verifies the recursive casting logic.

@author: Baptiste Pestourie
@date: 06.07.2026
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

import pytest

from comfyg import CastStrategy, cast_value
from comfyg._utils import Converter

if TYPE_CHECKING:
    from typing_extensions import TypeForm


@pytest.mark.parametrize(
    ("typehint", "value", "valid", "expected"),
    [
        # base atomic types
        (int, "42", True, 42),
        (int, "-7", True, -7),
        (int, "3.14", False, None),
        (int, "abcd", False, None),
        (float, "3.14", True, 3.14),
        (float, "42", True, 42.0),
        (float, "abcd", False, None),
        # strings are left untouched
        (str, "abcd", True, "abcd"),
        (str, "42", True, "42"),
        # booleans - several aliases accepted
        (bool, "yes", True, True),
        (bool, "y", True, True),
        (bool, "true", True, True),
        (bool, "True", True, True),
        (bool, "no", True, False),
        (bool, "n", True, False),
        (bool, "false", True, False),
        (bool, "xyz", False, None),
        # None - several aliases accepted (NoneType is the hint produced within Optional)
        (type(None), "none", True, None),
        (type(None), "None", True, None),
        (type(None), "null", True, None),
        (type(None), "nope", False, None),
        # Literals - matched by equality after casting to the variant type
        (Literal[1, 2, 3], "1", True, 1),
        (Literal[1, 2, 3], "3", True, 3),
        (Literal[1, 2, 3], "4", False, None),
        (Literal["a", "b"], "a", True, "a"),
        (Literal["a", "b"], "c", False, None),
        # Union / Optional - first successful cast wins, declaration order
        (int | str, "42", True, 42),
        (int | str, "abcd", True, "abcd"),
        (bool | int, "yes", True, True),
        (bool | int, "1", True, 1),
        (int | None, "4", True, 4),
        (int | None, "None", True, None),
        (int | None, "null", True, None),
        (int | None, "abcd", False, None),
    ],
)
def test_scalar_casts(
    *,
    typehint: TypeForm[object],
    value: str,
    valid: bool,
    expected: object,
) -> None:
    result = cast_value(value, typehint)
    assert valid is bool(result), (typehint, value, result)
    if valid:
        assert result.unwrap() == expected


@pytest.mark.parametrize(
    ("typehint", "value", "valid", "expected"),
    [
        # lists as line-break separated values
        (list[str], "hello\nworld", True, ["hello", "world"]),
        (list[str], "hello", True, ["hello"]),
        (list[str], "\nhello\nworld\n", True, ["hello", "world"]),
        (list[int], "1\n2\n3", True, [1, 2, 3]),
        (list[int], "1\nabc", False, None),
        # dicts as newline-separated `key = value` entries
        (dict[str, int], "\nfoo = 5\nbar = 3", True, {"foo": 5, "bar": 3}),
        (dict[str, int], "foo = 5", True, {"foo": 5}),
        (dict[str, int], "foo = abc", False, None),
        (dict[str, int], "malformed", False, None),
        # nested collections
        (dict[str, list[int]], "foo = 1\nbar = 2", True, {"foo": [1], "bar": [2]}),
    ],
)
def test_collection_casts_cfg(
    *,
    typehint: TypeForm[object],
    value: str,
    valid: bool,
    expected: object,
) -> None:
    result = cast_value(value, typehint, "cfg")
    assert valid is bool(result), (typehint, value, result)
    if valid:
        assert result.unwrap() == expected


@pytest.mark.parametrize(
    ("typehint", "value", "expected"),
    [
        # already-decoded collections pass through untouched
        (list[int], [1, 2, 3], [1, 2, 3]),
        (dict[str, int], {"foo": 5}, {"foo": 5}),
        (int, 42, 42),
    ],
)
def test_json_strategy_passthrough(
    *,
    typehint: TypeForm[object],
    value: object,
    expected: object,
) -> None:
    result = cast_value(value, typehint, "json")  # type: ignore[arg-type]
    assert bool(result)
    assert result.unwrap() == expected


def test_annotated_is_transparent() -> None:
    result = cast_value("7", Annotated[int, "some documentation"])
    assert bool(result)
    assert result.unwrap() == 7


class _Color:  # noqa: PLW1641
    def __init__(self, hex_value: str) -> None:
        if len(hex_value) != 6:
            raise ValueError(f"Invalid hex color: {hex_value}")
        self.hex_value = hex_value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Color) and other.hex_value == self.hex_value


@pytest.mark.parametrize(
    ("typehint", "value", "valid", "expected"),
    [
        (Annotated[int, Converter(lambda v: int(v.replace(",", "")))], "1,000", True, 1000),
        (Annotated[bool, Converter(lambda v: v.lower() in ("yes", "y"))], "yes", True, True),
        (Annotated[_Color, Converter(_Color)], "FF0000", True, _Color("FF0000")),
        (Annotated[int, Converter(int)], "abc", False, None),
        # converters take precedence over Union members
        (Annotated[_Color | int, Converter(_Color)], "FF0000", True, _Color("FF0000")),
    ],
)
def test_custom_converters(
    *,
    typehint: TypeForm[object],
    value: str,
    valid: bool,
    expected: object,
) -> None:
    result = cast_value(value, typehint)
    assert valid is bool(result), (typehint, value, result)
    if valid:
        assert result.unwrap() == expected


def test_top_level_error_is_not_nested() -> None:
    """A failure at the top level is returned directly, without a wrapping context."""
    result = cast_value("abc", int)
    assert not result
    rendered = str(result.error)
    assert rendered.startswith("* While casting")
    assert "int" in rendered


def test_nested_error_carries_full_context() -> None:
    """A failure while recursing surfaces both the outer and inner context."""
    result = cast_value("1\nabc", list[int])
    assert not result
    rendered = str(result.error)
    assert "list[int]" in rendered
    assert "'abc'" in rendered
    assert result.error.exception_cls is ValueError


@pytest.mark.parametrize("strategy", ["cfg", "json"])
def test_strategy_is_threaded_through(strategy: CastStrategy) -> None:
    """Scalars behave identically regardless of the collection strategy."""
    result = cast_value("42", int, strategy)
    assert bool(result)
    assert result.unwrap() == 42
