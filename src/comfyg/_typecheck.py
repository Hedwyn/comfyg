"""
Helpers around generic recursive type validation.

@date: 30.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Literal,
    Union,
    get_args,
    get_origin,
    Iterator,
    Protocol,
)

if TYPE_CHECKING:
    from typing_extensions import TypeForm


class TypeResolutionError(Exception):
    """
    Internal type resolution occured.
    This should not happen under normal circumstances using the public API,
    getting this exception implies that one of the internal properties is broken.
    """


class CollectionGetter[T: TypeForm[object]](Protocol):
    """
    A function which given a type `T`,
    returns all elements designated by the type argument at index `index`.
    For example, calling this on a dict[str, int] is expected to
    return the .keys() iterator for index 0 and .values() for index 1.
    In some cases there could be only one value; for example, calling index 0
    on tuple[int, str] should yield a single integer element, the first element
    of `instance`.
    """

    def __call__(self, instance: T, index: int = 0) -> Iterator[object]: ...


def iterate_dict_argument(
    instance: dict[object, object], index: int = 0, /
) -> Iterator[object]:
    match index:
        case 0:
            yield from instance.keys()
        case 1:
            yield from instance.values()
        case _:
            raise TypeResolutionError(
                f"dict has only 2 type arguments, was asked index {index}"
            )


def iterate_list_argument(
    instance: list[object], index: int = 0, /
) -> Iterator[object]:
    if index == 0:
        yield from instance
        return
    raise TypeResolutionError(f"list has only 1 type argument, was asked index {index}")


type DynamicTuple[T] = tuple[T, ...]


def iterate_dynamic_tuple_argument(
    instance: list[object], index: int = 0, /
) -> Iterator[object]:
    if index == 0:
        yield from instance
        return
    raise TypeResolutionError(
        f"Dynamic tuple has only 1 type argument, was asked index {index}"
    )


_COLLECTION_GETTERS = {
    dict: iterate_dict_argument,
    list: iterate_list_argument,
    DynamicTuple: iterate_dynamic_tuple_argument,
}

_SINGLE_ITEM_GETTERS = {
    tuple: tuple.__getitem__,
}
_SIZED_TYPES = {tuple: tuple.__len__}


def check_type(instance: object, expected: TypeForm[object]) -> bool:
    origin = get_origin(expected) or expected
    type_args = get_args(expected)
    # `Union` edge case: instance is valid if any of the args is valid, not all
    if origin is Union:
        return any(check_type(instance, arg) for arg in type_args)

    base_concrete_type = (
        origin if (origin is not None and isinstance(origin, type)) else None
    )
    if base_concrete_type and not isinstance(instance, base_concrete_type):
        return False
    if not type_args:
        return True

    # Literal edge case: we shall not use type checks but equality
    if origin is Literal:
        return any((instance == arg) for arg in type_args)

    # tuple + ellipsis edge case: tuple[T, ...] accept any number of items
    if origin is tuple and type_args[-1] is Ellipsis:
        origin = DynamicTuple
        type_args = type_args[:-1]
    if (iterator := _COLLECTION_GETTERS.get(origin)) is not None:
        arg_results: list[bool] = []
        for index, arg in enumerate(type_args):
            for item in iterator(instance, index):  # type: ignore[operator]
                arg_ok = check_type(item, arg)
                arg_results.append(arg_ok)

        return all((arg_results))

    # tuple[object, ...]
    if (
        type_args[-1] is not Ellipsis
        and (size_fn := _SIZED_TYPES.get(origin)) is not None  # type: ignore[arg-type]
    ):
        if not size_fn(instance) == len(type_args):  # type: ignore[arg-type]
            return False

    if (single_item_getter := _SINGLE_ITEM_GETTERS.get(origin)) is not None:  # type: ignore[arg-type]
        arg_results: list[bool] = []  # type: ignore[no-redef]
        results = [
            check_type(
                single_item_getter(instance, index),  # type: ignore[call-overload]
                arg_type,
            )
            for index, arg_type in enumerate(type_args)
        ]
        return all(results)

    raise NotImplementedError(f"Unsupported type: {origin}")
