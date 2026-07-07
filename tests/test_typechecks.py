"""
Verifies the recursive type checking logic.

@author: Baptiste Pestourie
@date: 30.06.2026
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

import pytest

from comfyg import check_type

if TYPE_CHECKING:
    from typing_extensions import TypeForm


@pytest.mark.parametrize(
    ("typehint", "instance", "valid"),
    [
        # base atomic types
        (int, 42, True),
        (float, 3.14, True),
        (int, 3.14, False),
        (str, "abcd", True),
        # int is an accepted value for float-typed fields
        (float, 42, True),
        (float, "3.14", False),
        # dict
        (dict[str, int], {}, True),
        (dict[str, int], {"a": 0}, True),
        (dict[str, int], {"a": "0"}, False),
        (dict[str, int], {0: "0"}, False),
        # list
        (list[int], [], True),
        (list[int], [1, 2, 3], True),
        (list[int], [1], True),
        (list[int], [1, "3"], False),
        (list[int], ["1", "3"], False),
        # tuple
        (tuple[int, float], (42, 3.14), True),
        (tuple[int, float], (42), False),
        (tuple[int, float], (42, 3.14, ""), False),
        (tuple[int, float], (42, "3.14"), False),
        (tuple[int, ...], (1, 2, 3), True),
        (tuple[int, ...], (), True),
        # Union
        (int | float, 42, True),
        (int | float, 3.14, True),
        (int | float, "", False),
        # Literals
        (Literal[1, 2], 1, True),
        (Literal[1, 2], 3, False),
        # Annotated: transparently unwraps to the underlying type
        (Annotated[int, "some metadata"], 42, True),
        (Annotated[int, "some metadata"], "abcd", False),
        (Annotated[float, "some metadata"], 42, True),
        (Annotated[list[int], "some metadata"], [1, 2], True),
        (Annotated[list[int], "some metadata"], [1, "2"], False),
    ],
)
def test_type_checks(*, typehint: TypeForm[object], instance: object, valid: bool) -> None:
    result = check_type(instance, typehint)
    assert valid is bool(result), (typehint, instance, result)
