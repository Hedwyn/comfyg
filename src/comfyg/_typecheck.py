"""
Helpers around generic recursive type validation.

@date: 30.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NamedTuple,
    Protocol,
    Self,
    Union,
    get_args,
    get_origin,
)

if TYPE_CHECKING:
    from typing_extensions import TypeForm

from exhausterr import Err, Error, NoneOr, Ok, is_error


class Context(NamedTuple):
    received_object: object
    expected_type: TypeForm[object]

    def __str__(self) -> str:
        return f"While validating {self.received_object} against {self.expected_type!r}:"


@dataclass
class TypeValidationError(Error):
    exception_cls = TypeError
    description = "{context}\n{local_error!s}"
    # attributes
    context: Context
    local_error: LocalTypeValidationError


@dataclass
class LocalTypeValidationError(Error):
    context: Context
    description = "{context}\n"

    @classmethod
    def get_description(cls) -> str:
        if (description := cls.description) is None:
            raise TypeError("Providing a descripton is mandatory in TypeValidationError classes")
        return description

    def to_err(self) -> Err[Self]:
        return Err(self)


@dataclass
class ConcreteTypeError(LocalTypeValidationError):
    concrete_type: type
    description = "{context}\nNot an instance of `{concrete_type}`"


@dataclass
class SizeMistmatchError(LocalTypeValidationError):
    expected_size: int
    obtained_size: int
    description = (
        "{context}\nSize mismatches: expected {expected_size} items, received {obtained_size}"
    )


@dataclass
class UnionValidationError(LocalTypeValidationError):
    description = "{context}. Not an instance of any of the members in the Union"
    valid_types: tuple[TypeForm[object]]


@dataclass
class LiteralValidationError(LocalTypeValidationError):
    description = "None of the accepted variants are matching {accepted_values}"
    accepted_values: tuple[object, ...]


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


def iterate_dict_argument(instance: dict[object, object], index: int = 0) -> Iterator[object]:
    match index:
        case 0:
            yield from instance.keys()
        case 1:
            yield from instance.values()
        case _:
            raise TypeResolutionError(f"dict has only 2 type arguments, was asked index {index}")


def iterate_list_argument(instance: list[object], index: int = 0) -> Iterator[object]:
    if index == 0:
        yield from instance
        return
    raise TypeResolutionError(f"list has only 1 type argument, was asked index {index}")


def iterate_dynamic_tuple_argument(instance: list[object], index: int = 0) -> Iterator[object]:
    if index == 0:
        yield from instance
        return
    raise TypeResolutionError(f"Dynamic tuple has only 1 type argument, was asked index {index}")


_COLLECTION_GETTERS: dict[TypeForm[object], CollectionGetter[Any]] = {
    dict: iterate_dict_argument,
    list: iterate_list_argument,
}

type SingleItemGetter = Callable[[Any, int], Any]

_SINGLE_ITEM_GETTERS: dict[TypeForm[object], SingleItemGetter] = {
    tuple: tuple.__getitem__,
}
type SizeFn = Callable[[Any], int]
_SIZED_TYPES: dict[TypeForm[object], SizeFn] = {tuple: tuple.__len__}


def check_type(
    instance: object,
    expected: TypeForm[object],
) -> NoneOr[TypeValidationError | LocalTypeValidationError]:
    context = Context(instance, expected)
    match _check_type(instance, expected, context):
        case Ok():
            return Ok(None)
        case Err(error=error):
            if context == error.context:
                # error happened at the top-level - not need to nest it
                return Err(error)
            return Err(TypeValidationError(context=context, local_error=error))


def _check_type(  # noqa: C901, PLR0911, PLR0912
    instance: object,
    expected: TypeForm[object],
    context: Context | None = None,
) -> NoneOr[LocalTypeValidationError]:
    ctx = context or Context(instance, expected)

    origin = get_origin(expected) or expected
    type_args = get_args(expected)

    # Literal edge case: we shall not use type checks but equality
    if origin is Literal:
        any_valid = any((instance == arg) for arg in type_args)
        return (
            Ok(None) if any_valid else Err(LiteralValidationError(ctx, accepted_values=type_args))
        )
    # once we ruled out Literal, all arguments should be TypeForm

    # `Union` edge case: instance is valid if any of the args is valid, not all
    if origin is Union:
        for arg in type_args:
            result = _check_type(instance, arg)
            if result:
                return Ok(None)
        return Err(
            UnionValidationError(
                ctx,
                valid_types=type_args,
            ),
        )
    base_concrete_type = origin if (origin is not None and isinstance(origin, type)) else None
    if base_concrete_type and not isinstance(instance, base_concrete_type):
        return Err(ConcreteTypeError(ctx, base_concrete_type))
    if not type_args:
        return Ok()

    # tuple + ellipsis edge case: tuple[T, ...] accept any number of items
    is_dynamic_tuple = origin is tuple and type_args[-1] is Ellipsis
    iterator: CollectionGetter[Any] | None = None
    if is_dynamic_tuple:
        iterator = iterate_dynamic_tuple_argument
        type_args = type_args[:-1]
    iterator = iterator or _COLLECTION_GETTERS.get(origin)
    if iterator is not None:
        for index, arg in enumerate(type_args):
            for item in iterator(instance, index):  # pyright: ignore[reportArgumentType]
                result = _check_type(item, arg)
                if is_error(result):
                    return result
        return Ok()

    # tuple[object ...]
    size_fn = _SIZED_TYPES.get(origin)
    if size_fn is not None and (obtained_size := size_fn(instance)) != len(type_args):
        return Err(SizeMistmatchError(ctx, len(type_args), obtained_size))
    if (single_item_getter := _SINGLE_ITEM_GETTERS.get(origin)) is not None:
        for index, arg_type in enumerate(type_args):
            result = _check_type(
                single_item_getter(instance, index),
                arg_type,
            )
            if not result:
                return result
        return Ok()

    raise NotImplementedError(f"Unsupported type: {origin}")
