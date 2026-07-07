"""
Automated casting logic from arbitrary data types to Python types.

Mirrors the recursive, dispatch-based design of `_typecheck`: `cast_value`
walks a type hint, deploying a casting strategy per encountered construct
(scalars, `Literal`, `Union`, collections, custom `Converter`s) and returns
an `exhausterr.Result` rather than raising.

@date: 06.07.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Annotated,
    Literal,
    NamedTuple,
    Self,
    Union,
    get_args,
    get_origin,
)

if TYPE_CHECKING:
    from typing_extensions import TypeForm

from exhausterr import Err, Error, Ok, Result

from ._utils import Converter, get_annotation

type CastStrategy = Literal["cfg", "json"]


class CastContext(NamedTuple):
    received_value: object
    expected_type: TypeForm[object]

    def __str__(self) -> str:
        expected_type = self.expected_type
        typehint = expected_type.__name__ if isinstance(expected_type, type) else str(expected_type)
        return f"While casting {self.received_value!r} to `{typehint}`:"


@dataclass
class CastError(Error):
    exception_cls = ValueError
    description = "{context}\n{local_error!s}"
    # attributes
    context: CastContext
    local_error: LocalCastError


@dataclass
class LocalCastError(Error):
    context: CastContext
    description = "* {context}\n"

    def to_err(self) -> Err[Self]:
        return Err(self)


@dataclass
class ScalarCastError(LocalCastError):
    target_type: type
    description = "* {context}\n  -> Cannot cast to `{target_type.__name__}`."


@dataclass
class UnionCastError(LocalCastError):
    tried_types: tuple[TypeForm[object], ...]
    description = "* {context}\n  -> Value matches none of the members in the Union."


@dataclass
class LiteralCastError(LocalCastError):
    accepted_values: tuple[object, ...]
    description = "* {context}\n  -> None of the accepted variants match {accepted_values}."


@dataclass
class ConverterCastError(LocalCastError):
    converter: Converter[object]
    error: Exception
    description = "* {context}\n  -> Custom converter failed: {error!r}."


@dataclass
class MalformedMappingError(LocalCastError):
    line: str
    description = "* {context}\n  -> Malformed `key = value` entry: {line!r}."


def cast_value[T](
    value: str,
    expected_type: TypeForm[T],
    strategy: CastStrategy = "cfg",
) -> Result[T, CastError | LocalCastError]:
    """
    Tries casting `value` to the given type annotation `expected_type`.
    Rich type annotations (Union, Literal, list[...], dict[...], `Annotated`
    with a custom `Converter`, and nested combinations of those) are supported.

    The following rules are applied:
    * For unions, casting is tried in declaration order (e.g. for `Union[bool, int]`
      a boolean cast is attempted before an integer one).
    * With the "cfg" strategy, lists are expected as line-break separated values,
      and dictionaries as newline-separated `key = value` entries.
    * With the "json" strategy, collections are expected to be decoded upstream and
      simply pass through (only leftover string leaves are still coerced).

    This is not a type validator: it does not guarantee the produced value matches
    `expected_type` (a JSON payload may smuggle in wrong types). Validate afterwards
    using `_typecheck.check_type`.

    Returns
    -------
    Result[T, CastError | LocalCastError]
        `Ok` wrapping the casted value on success, `Err` describing the failure otherwise.
    """
    context = CastContext(value, expected_type)
    match _cast_value(value, expected_type, strategy, context):
        case Ok() as ok:
            return ok
        case Err(error=error):
            if context == error.context:
                # error happened at the top-level - no need to nest it
                return Err(error)
            return Err(CastError(context=context, local_error=error))


def _cast_value[T](
    value: str | T,
    expected: TypeForm[T],
    strategy: CastStrategy,
    context: CastContext | None = None,
) -> Result[T, LocalCastError]:
    """
    Internal, recursive implementation of `cast_value`.

    Any construct taking type arguments (Union, Literal, collections, Annotated)
    may re-enter this function to process its members. Every branch stays generic
    over the target type `T`: values are produced through `expected` (or its
    arguments), never as concrete literals, so the whole recursion is `Result[T]`.
    """
    ctx = context or CastContext(value, expected)

    # Already a non-string value: nothing to parse (e.g. nested JSON payloads).
    if not isinstance(value, str):
        return Ok(value)

    # Custom converters take precedence over everything else.
    if (converter := get_annotation(expected, Converter)) is not None:
        try:
            return Ok(converter(value))
        except (TypeError, ValueError) as exc:
            return ConverterCastError(ctx, converter, exc).to_err()

    origin = get_origin(expected) or expected
    type_args = get_args(expected)

    # Transparent `Annotated` wrapper: cast against the underlying type.
    if origin is Annotated:
        return _cast_value(value, type_args[0], strategy)

    # Literal: cast to each variant's type and match by equality, in order.
    if origin is Literal:
        for variant in type_args:
            match _cast_value(value, type(variant), strategy):
                case Ok(value=candidate) if candidate == variant:
                    return Ok(candidate)  # pyright: ignore[reportReturnType]
                case _:
                    continue
        return LiteralCastError(ctx, type_args).to_err()

    # Union (includes Optional): first successful member wins, declaration order.
    if origin is Union:
        for arg in type_args:
            candidate = _cast_value(value, arg, strategy)
            if candidate:
                return candidate
        return UnionCastError(ctx, type_args).to_err()

    if origin is _NONETYPE:
        match _cast_none(value, ctx):
            case Ok():
                ctor = get_origin(expected) or expected
                assert isinstance(ctor, type)  # NoneType, called with no argument
                return Ok(ctor())
            case Err() as err:
                return err

    if origin is bool:
        match _cast_bool(value, ctx):
            case Ok(value=casted_bool):
                return _retag(casted_bool, expected)
            case Err() as err:
                return err

    if strategy == "cfg" and origin is list:
        (item_type,) = type_args
        match _cast_list_cfg(value, item_type, strategy):
            case Ok(value=casted_list):
                return _retag(casted_list, expected)
            case Err() as err:
                return err

    if strategy == "cfg" and origin is dict:
        key_type, value_type = type_args
        match _cast_dict_cfg(value, ctx, key_type, value_type, strategy):
            case Ok(value=casted_dict):
                return _retag(casted_dict, expected)
            case Err() as err:
                return err

    # Default: hand the raw string to the type's constructor.
    if isinstance(origin, type):
        try:
            return Ok(origin(value))
        except (TypeError, ValueError):
            return ScalarCastError(ctx, origin).to_err()

    raise NotImplementedError(f"Unsupported type for casting: {origin}")


# Common keywords for True/False and None
_NONE_ALIASES = ("none", "None", "null", "NULL", "Null")
_TRUE_ALIASES = ("yes", "true", "True", "y")
_FALSE_ALIASES = ("no", "n", "false", "False")
_NONETYPE = type(None)


def _retag[T](inner: object, expected: TypeForm[T]) -> Result[T, LocalCastError]:
    """
    Re-tag an already-correct runtime value with its statically-expected type `T`.

    The concrete leaf casters return their own precise target (`Result[bool]`,
    `Result[list[E]]`, ...). The dynamic dispatcher only knows the target as a
    typevar `T`, so it rebuilds the value through `expected`'s constructor to hand
    it back as `Result[T]` without an unchecked cast.
    """
    ctor = get_origin(expected) or expected
    assert isinstance(ctor, type)
    return Ok(ctor(inner))


def _cast_none(value: str, ctx: CastContext) -> Result[None, LocalCastError]:
    """Casts a string to None, accepting several aliases (None, null, ...)."""
    if value in _NONE_ALIASES:
        return Ok(None)
    return ScalarCastError(ctx, _NONETYPE).to_err()


def _cast_bool(value: str, ctx: CastContext) -> Result[bool, LocalCastError]:
    """Casts a string to bool, accepting several aliases (yes/no, y/n, true/false)."""
    if value in _TRUE_ALIASES:
        return Ok(True)
    if value in _FALSE_ALIASES:
        return Ok(False)
    return ScalarCastError(ctx, bool).to_err()


def _cast_list_cfg[T](
    value: str,
    item_type: TypeForm[T],
    strategy: CastStrategy,
) -> Result[list[T], LocalCastError]:
    """
    Casts a list passed as line-break separated values.
    Empty lines are skipped; any element failing to cast fails the whole list.
    """
    items: list[T] = []
    for line in value.splitlines():
        if not line:
            continue
        match _cast_value(line, item_type, strategy):
            case Ok(value=element):
                items.append(element)
            case Err() as err:
                return err
    return Ok(items)


def _cast_dict_cfg[K, V](
    value: str,
    ctx: CastContext,
    key_type: TypeForm[K],
    value_type: TypeForm[V],
    strategy: CastStrategy,
) -> Result[dict[K, V], LocalCastError]:
    """
    Casts a dictionary passed as newline-separated `key = value` entries.
    """
    pairs: dict[K, V] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, sep, val = line.partition("=")
        if not sep:
            return MalformedMappingError(ctx, line).to_err()
        match _cast_value(key.strip(), key_type, strategy):
            case Ok(value=casted_key):
                pass
            case Err() as err:
                return err
        match _cast_value(val.strip(), value_type, strategy):
            case Ok(value=casted_value):
                pass
            case Err() as err:
                return err
        pairs[casted_key] = casted_value
    return Ok(pairs)
