"""
Shared helpers used across the type-checking and casting subsystems.

Mainly utilities to introspect `typing.Annotated` metadata and the
`Converter` container used to plug custom string-to-type conversion functions.

@date: 06.07.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, MutableMapping
from dataclasses import MISSING, dataclass
from typing import Any

type ConvertionFn[T] = Callable[[str], T]


@dataclass
class Converter[T]:
    """
    Wraps a custom conversion function for use in Annotated type hints.
    When applied to a config parameter, enables custom string-to-type casting.
    """

    conversion_fn: ConvertionFn[T]

    def __call__(self, value: str) -> T:
        """Applies the conversion function to the input value."""
        return self.conversion_fn(value)


def iter_annotations[T](type_hint: object, annotation_type: type[T]) -> Iterator[T]:
    """
    Yields every annotation of type `annotation_type` found in the
    `__metadata__` of an `Annotated` type hint.
    """
    metadata = getattr(type_hint, "__metadata__", None)
    if metadata is None:
        return
    for annotation in metadata:
        if isinstance(annotation, annotation_type):
            yield annotation


def get_annotations[T](type_hint: object, annotation_type: type[T]) -> list[T]:
    """
    Returns all annotations of type `T` found in the given type hint, as a list.
    """
    return list(iter_annotations(type_hint, annotation_type=annotation_type))


def get_annotation[T](
    type_hint: object, annotation_type: type[T], *, strict: bool = False
) -> T | None:
    """
    Inspects the annotations in the `Annotated` fields of `type_hint`.
    Returns None if `type_hint` is not an Annotated or if no annotations
    of type `annotation_type` could be found.
    If strict is False, returns the first found annotation of type `annotation_type`,
    otherwise ensure that no more than one annotation of that type is there and raises
    ValueError otherwise.
    """
    annotations = get_annotations(type_hint, annotation_type)
    if not annotations:
        return None

    if strict and len(annotations) > 1:
        raise ValueError(f"Found more than one annotation of type {annotation_type} in {type_hint}")
    return annotations.pop()


def dict_flattener(base_dict: dict[str, Any]) -> Iterator[tuple[list[str], Any]]:
    for k, v in base_dict.items():
        if (
            v.__class__ is dict
        ):  # faster than isinstance(), we are only dealing with pure-dicts here
            yield from (([k] + keys, v) for keys, v in dict_flattener(v))
        else:
            yield [k], v


def dict_key_flattener(base_dict: dict[str, Any]) -> Iterator[list[str]]:
    for k, v in base_dict.items():
        if (
            v.__class__ is dict
        ):  # faster than isinstance(), we are only dealing with pure-dicts here
            yield from ([k, *keys] for keys in dict_key_flattener(v))
        else:
            yield [k]


def get_nested(dict_like: Mapping[str, Any], *keys: str, default: Any = MISSING) -> Any:
    """
    Gets a value with arbitrary depth in a nested dictionary.
    Follows the path given by keys, and creates missing  empty dictionaries
    on the way if needed
    """
    next_element = dict_like
    try:
        for k in keys:
            next_element = next_element[k]
        return next_element
    except KeyError:
        if default is MISSING:
            raise KeyError(f"{k} while accessing {'->'.join(keys)}")
        return default


def set_nested(dict_like: MutableMapping[str, Any], value: Any, *keys: str) -> None:
    """
    Sets a value with arbitrary depth in a nested dictionary.
    Follows the path given by keys, and creates missing  empty dictionaries
    on the way if needed
    """
    next_element = dict_like
    if not keys:
        raise ValueError("Must pass at least one key")
    *parents, leaf = keys
    for k in parents:
        next_element = next_element.setdefault(k, {})
    next_element[leaf] = value
