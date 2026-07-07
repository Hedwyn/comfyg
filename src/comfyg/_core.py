"""
`comfyg` is a single-file package.
Refer to README for usage.

@date: 29.06.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

from abc import abstractmethod
from collections import deque
from copy import deepcopy
from dataclasses import MISSING, Field, asdict, dataclass, field, fields, is_dataclass
from enum import Enum, IntEnum, auto
from functools import cache
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    Protocol,
    TypeVar,
    cast,
    get_type_hints,
    runtime_checkable,
)

from exhausterr import Err, NoneOr, Ok, Result

from ._casting import cast_value as _cast_value
from ._typecheck import Context as _Context
from ._typecheck import LocalTypeValidationError
from ._typecheck import check_type as _check_type

if TYPE_CHECKING:
    from collections.abc import (
        Callable,
        Iterable,
        Iterator,
    )

    from typing_extensions import TypeForm

    from ._casting import CastError, CastStrategy, LocalCastError
    from ._typecheck import TypeValidationError

from ._utils import dict_flattener, dict_key_flattener, get_annotation, get_nested

# mypy's incomplete `TypeForm` (PEP 747) support erases the special `TypeForm`
# form down to a plain `type[T]` whenever a function's signature is resolved
# across a module boundary -- see https://github.com/python/mypy/issues/20124.
# `check_type`'s genuine signature (as verified when `_typecheck.py` is checked
# on its own) is `(object, TypeForm[Any]) -> NoneOr[...]`, not the collapsed
# `(object, type[Any]) -> ...` that mypy reconstructs for callers in this file.
# This cast restores the correct signature for every call site below.
check_type: Callable[
    [object, TypeForm[Any]],
    NoneOr[TypeValidationError | LocalTypeValidationError],
] = cast(
    "Callable[[object, TypeForm[Any]], NoneOr[TypeValidationError | LocalTypeValidationError]]",
    _check_type,
)

# Same collapse, same fix, for `_casting.cast_value` -- see the comment above.
_cast_value_result: Callable[
    [str, TypeForm[Any], CastStrategy],
    Result[Any, CastError | LocalCastError],
] = cast(
    "Callable[[str, TypeForm[Any], CastStrategy], Result[Any, CastError | LocalCastError]]",
    _cast_value,
)

# Same collapse, same fix, for `_typecheck.Context`'s constructor.
Context: Callable[[object, TypeForm[Any]], _Context] = cast(
    "Callable[[object, TypeForm[Any]], _Context]",
    _Context,
)

ValidatorTypes = Literal["Choices", "Range"]


@dataclass
class ValidatorInfo:
    """
    A data export of validators suitable for serialization,
    for 3rd-party tools that might ned to build logic from them
    """

    type: str
    description: str
    parameters: list[float | str] = field(default_factory=list)

    @classmethod
    def extract(cls, validator: ValidatorMixin) -> ValidatorInfo:
        """
        Extracts the information automatically from a class implementing ValidatorMixin
        """
        return ValidatorInfo(
            type=validator.__class__.__name__,
            description=str(validator),
            parameters=list(validator.parameters),
        )

    def __str__(self) -> str:
        """
        Returns
        -------
        str
            A reasonable human-friendly rendering of this object.
        """
        return f"{self.type}: {self.description}"


@runtime_checkable
class DocAnnotationMixin(Protocol):
    """
    Any annotation implementing this interface will be included in the doc.
    """

    def get_doc(self) -> Iterable[str]: ...


@dataclass
class OptionDoc:
    """
    A container for documentation for a given option in a config.
    Necessary to allow plugging different rendering/formatting strategies.
    """

    name: str
    type_hint: str
    docstrings: list[str]
    validators: list[ValidatorInfo]
    default: object = MISSING

    def __str__(self) -> str:
        """
        Returns
        -------
        str
            A reasonable human-friendly rendering of this object.
        """

        def generate_lines() -> Iterator[str]:
            yield self.name + ": " + self.type_hint
            if self.default is not MISSING:
                yield "\tdefault: " + str(self.default)

            for docstring in self.docstrings:
                yield "\t" + docstring
            if self.validators:
                yield "\n\t--- Requirements ---"
                for validator in self.validators:
                    yield "\t" + str(validator)

        return "\n".join(generate_lines())

    def to_dict(self) -> dict[str, Any]:
        """
        Exports this object to a dict
        """
        base_dict = asdict(self)
        if self.default is MISSING:
            del base_dict["default"]
        return base_dict


# The result of `build_documentation`: a dict of option name to its `OptionDoc`,
# plus an optional "__sections__" key nesting each sub-section's own such tree.
type DocumentationTree = dict[str, OptionDoc | dict[str, DocumentationTree]]


class ValidationError(Exception):
    def __init__(self, msg: str, option_path: Iterable[str] | None = None) -> None:
        """
        Parameters
        ----------
        option_path: Full squence of keys to access the option
        that created the error
        """
        super().__init__(msg)
        self.option_path = deque() if option_path is None else deque(option_path)

    def __str__(self) -> str:
        """
        Shows the option path in the error message
        """
        if not self.option_path:
            return super().__str__()
        return f"[{'.'.join(self.option_path)}]: " + super().__str__()


# The errors `check_type` can produce -- exposed directly rather than wrapped
# in a comfyg-specific exception. Raising still goes through each variant's
# own `exception_cls` (see `_typecheck.py`), via `.unwrap()`.
type ConfigValidationError = TypeValidationError | LocalTypeValidationError

# Same, for `_casting.cast_value`.
type ConfigCastError = CastError | LocalCastError


class ConfigLoadingError(Exception):
    """
    Raised when failing to build the config from a config file.
    """


class ConfigAnnotationError(ValidationError):
    """
    Raised when a config type annotation is either not valid or not supported
    """


@dataclass
class ValidatorConstraintError(LocalTypeValidationError):
    """
    Raised when a value fails a `ValidatorMixin` check (e.g. `Range`, `Choices`).
    """

    validator: ValidatorMixin
    description = "* {context}\n  -> Does not satisfy {validator}."


def typecheck_instance(instance: object, type_hint: TypeForm[Any]) -> None:
    """
    Checks if instance respects the type annotation `type_hint`

    Raises
    ------
    TypeError
        If the type is not valid, with detailed reasons (see `_typecheck.check_type`)
    """
    check_type(instance, type_hint).unwrap()


def cast_value(
    value: str,
    type_hint: TypeForm[Any],
    cast_strategy: Literal["cfg", "json"] = "cfg",
) -> Any:
    """
    Tries casting `value` to the given type annotation `type_hint`.
    Rich types annotations (Union, list[...], dict[...], nested combination of those)
    are supported.

    The following rules are applied:
    * For unions, tries casting in order of type declaration.
    For example, for a union[bool, int], first tries to cast `value` to a boolean,
    and only tries to cast to integer if the boolean cast fails.
    * Lists are expected to be passed as multiline values
    (i.e., a line break sould be used as value separator)
    * Dictionaries should be encoded with JSON.

    Note: although this function should catch a number of errors, it's not meant
    as a way to validate types. There are seperate primitives to run type validation.
    Typically JSON dict with invalid types might make it through.
    You should first cast the values then test if their type is correct.
    That's how it is implemented in the config utilities of this module anyway.

    Raises
    ------
    ValueError
        If no valid cast strategy is found for the passed value and annotations
        (see `_casting.cast_value`).
    """
    return _cast_value_result(value, type_hint, cast_strategy).unwrap()


def flatten_iterable(values: object) -> Iterator[object]:
    """
    Converts any type of value or sequence to a single linear sequence
    in a recursive manner.
    Nested sequence will be flattened.
    Single values will be converted to sequences of length 1.
    Dict expand to the sequence of their values.
    """
    match values:
        case dict(_):
            for v in values.values():
                yield from flatten_iterable(v)
        case list(_):
            for v in values:
                yield from flatten_iterable(v)
        # not iterable
        case _:
            # terminal case
            yield values


@runtime_checkable
class ValidatorMixin(Protocol):
    """
    An annotation that provides a way to validate that the
    passed value fulfills the requirements.

    Values are expected to be tested as:
    >>> value in validator
    """

    def __contains__(self, value: object) -> bool: ...
    def __str__(self) -> str: ...

    @property
    def parameters(self) -> Iterable[float | int | str]: ...


class Length(ValidatorMixin):
    """
    For an iterable type, validates that the length of the data is in an acceptable range.
    """

    def __init__(self, min_len: int, max_len: int | None = None) -> None:
        """
        Parameters
        ----------
        min_len
            The minimum length of the iterable, or the precise length expected
            if there's no tolerance (i.e., max_len is None)
        max_len
            The maximum length of the iterable.
        """
        self._min_len = min_len
        self._max_len = max_len or min_len

    @property
    def parameters(self) -> Iterable[float | int | str]:
        """
        Min, maximum length for this validator
        """
        return (self._min_len, self._max_len)

    def __contains__(self, values: object) -> bool:
        """
        Checks if the passed value if an instance of any
        of the expected types.
        """
        values = tuple(flatten_iterable(values))
        try:
            return self._min_len <= len(values) <= self._max_len
        except TypeError:
            return False

    def __str__(self) -> str:
        """
        Formats the list of types that this validator is expected.
        """
        return f"Length[{self._min_len}, {self._max_len}]"


@dataclass
class Range(ValidatorMixin):
    """
    An interval of values that are valid for a given dataclass parameter.
    Boundaries are included.
    """

    min_value: float
    max_value: float

    @property
    def parameters(self) -> Iterable[float | int | str]:
        """
        Min, maximum value for this validator
        """
        return (self.min_value, self.max_value)

    def __contains__(self, values: object) -> bool:
        """
        Validates the value if it is within the given interval
        """
        for value in flatten_iterable(values):
            assert isinstance(value, int | float), (
                f"Range validator expects numeric values, got {value!r}"
            )
            if not self.min_value <= value <= self.max_value:
                return False
        return True

    def __str__(self) -> str:
        """
        Formats this interval as [min_value, max_value]
        """
        return f"Expects values within [{self.min_value:.2f}, {self.max_value:.2f}]"


ChoiceType = TypeVar("ChoiceType", bound=str | int | float)


class Choices[ChoiceType: str | int | float](ValidatorMixin):
    """
    A set of valid values for a given dataclass parameter.
    Case-insensitive.
    """

    def __str__(self) -> str:
        """
        Formats this set as [choice1, choice2, ...]
        """
        return f"Should be any of [{', '.join(str(c) for c in self._choices)}]"

    def __init__(self, *choices: ChoiceType | type[Enum]) -> None:
        """
        Parameters
        ----------
        choices: str | StrEnum
            All the valid options (case-insensitive).
            If StrEnum, all the fields in the enum are added to the valid
            choices.
        """
        # Note: not using a set to preserve order
        self._choices: list[ChoiceType] = []
        for choice in choices:
            if isinstance(choice, type) and issubclass(choice, Enum):
                for member in choice.__members__.values():
                    assert isinstance(member, str | int | float), (
                        f"Choices only supports Enum classes mixed with str, int or "
                        f"float, got {choice}"
                    )
                    # The assert above proves `member` matches ChoiceType's bound
                    # (str | int | float), but mypy can't verify it against this
                    # specific instance's ChoiceType binding since TypeVars aren't
                    # checkable at runtime.
                    self.add_choice(cast("ChoiceType", member))
            else:
                self.add_choice(choice)

    @property
    def parameters(self) -> Iterable[ChoiceType]:
        """
        All the choices acceptable for this validator
        """
        return tuple(self._choices)

    def all(self) -> list[ChoiceType]:
        """
        May be used as a factory method for dataclass definition

        Returns
        -------
        list[ChoiceType]
            All the choices as a list.
        """
        return list(self._choices)

    def add_choice(self, choice: ChoiceType) -> None:
        """
        Adds a choice as lowercase string
        """
        self._choices.append(choice)

    def __contains__(self, values: object | Iterable[object]) -> bool:
        """
        Validates the value if it is a valid choice
        """
        return all(v in self._choices for v in flatten_iterable(values))


def get_validators(type_hint: object) -> Iterator[ValidatorMixin]:
    """
    Extracts the validators from a typing Annotation,
    if any.

    Returns
    -------
    ValidatorMixin
        All found validators
    """
    if (metadata := getattr(type_hint, "__metadata__", None)) is None:
        return
    for validator in metadata:
        if isinstance(validator, str):
            continue
        if isinstance(validator, ValidatorMixin):
            yield validator


def get_doc_hints(type_hint: object) -> Iterator[str]:
    """
    Extracts documentation from a typing Annotation,
    if any.

    Returns
    -------
    str
        All found documentation
    """
    if (metadata := getattr(type_hint, "__metadata__", None)) is None:
        return

    for annotation in metadata:
        if isinstance(annotation, str) and not isinstance(annotation, DefaultScopes):
            yield annotation


def get_doc_annotations(type_hint: object) -> list[DocAnnotationMixin]:
    """
    Extracts the `DocAnnotationMixin` annotations from a typing Annotation,
    if any.

    Note: implemented as a direct isinstance filter rather than via
    `get_annotations` since `DocAnnotationMixin` is a `Protocol`, which
    static type checkers reject as a `type[T]` argument.
    """
    metadata = getattr(type_hint, "__metadata__", ())
    return [annotation for annotation in metadata if isinstance(annotation, DocAnnotationMixin)]


@dataclass
class Alias:
    """
    A small container that you can put in an `Annotated`
    type hint if a parameter shall be imported/exported
    under a different name.
    This can be useful for parameter that require non Python-friendly names.
    """

    alias: str


class DefaultScopes(IntEnum):
    """
    The `scope` of a config parameter/option is the context in which it should be exposed.
    BASIC is public scope, i;e., options should be always shown.
    ADVANCED is a dev/advanced user level which should be explicitly toggled.
    HIDDEN parameters should never be exposed on any interface, and are reserved for internal use.
    """

    # Note: order matters, Scope are meant to be comparable
    BASIC = auto()
    ADVANCED = auto()
    INTERNAL = auto()
    HIDDEN = auto()

    def __str__(self) -> str:
        """
        Returns
        -------
        str
            Lowercase name.
        """
        return self.name.lower()

    def __repr__(self) -> str:
        """
        Returns
        -------
        str
            Lowercase name.
        """
        return self.name.lower()


class Depends:
    """
    Custom annotation that goes into an `Annotated` field.
    Expressed that the scope of a given parameter in a dataclass
    depends on the value of other parameter (e.g., a feature flag).
    If that dependency is not name, the scope will automatically be set to
    hidden.

    Note: `Depends` is currently NOT recursive: if the parameter you are pointing to
    also has a dependencies, they won't be considered, and should be expressed explicitly
    if needeed.


    Examples
    --------

    @dataclass
    class Config(ConfigValidator):
        is_bidirectional: bool
        # Below parameter will be hidden if `is_bidirectional` is False
        max_discharge_current: Annotated[float, Depends("is_biredictional") = 0
        # This is equivalent, but can express equality to other types (int, float, str, enums)
        max_discharge_power: Annotated[float, Depends(is_birectional=True) = 0
    """

    def __init__(self, *bool_dependencies: str, **dependencies: float | bool | str | Enum) -> None:
        """
        Pass your requirements as keyword argument such as param_name=expected_value.
        For boolean expectations (i.e. expected_value is True), you may simply pass
        the param name as a wildcard argument
        """
        self._required_values: dict[str, int | float | bool | str | Enum] = {}
        for bool_dep in bool_dependencies:
            self._required_values[bool_dep] = True
        for dep_name, expected_value in dependencies.items():
            self._required_values[dep_name] = expected_value

    def is_met(self, owner: object) -> bool:
        """
        Returns
        -------
        bool
            Whether `owner` fulfills the dependency, i.e.,
            does it have the required value for the configuured parameters.
        """
        for param_name, required_value in self._required_values.items():
            try:
                value = getattr(owner, param_name)
            except AttributeError as exc:
                msg = (
                    f"Internal failure: dependency declared on param {param_name}  in {owner}, "
                    "but that parameter does not exist"
                )
                raise NameError(msg) from exc
            if value != required_value:
                return False
        return True

    @property
    def required_values(self) -> dict[str, int | float | bool | str | Enum]:
        """
        Returns
        -------
        dict[str, int | float | bool | str | Enum]
            All the dependencies declared in this object
        """
        return self._required_values


def get_alias(type_hint: object) -> str | None:
    """
    Looks for an `Alias` in the type hint of a given parameter.
    Returns
    -------
    str | None
        None if no alias was found
        The alias otherwise
    """
    return alias.alias if (alias := get_annotation(type_hint, Alias)) is not None else None


def is_hidden(type_hint: object) -> bool:
    """
    Whether the config option should be hidden from normal users.
    Hidden parameters will not get exported in generate config files;
    however, they will get imported if they are defined in the persistent config.
    """
    metadata = getattr(type_hint, "__metadata__", None)
    if metadata is None:
        return False
    for annotation in metadata:
        if isinstance(annotation, DefaultScopes):
            return annotation == DefaultScopes.HIDDEN
    return False


def get_scope(type_hint: TypeForm[object]) -> DefaultScopes:
    """
    Whether the config option should be hidden from normal users.
    Hidden parameters will not get exported in generate config files;
    however, they will get imported if they are defined in the persistent config.
    """
    return get_annotation(type_hint, DefaultScopes) or DefaultScopes.BASIC


def get_dependencies(type_hint: TypeForm[object]) -> Iterator[Depends]:
    """
    Yields
    ------
    All the dependencies (aka `Depends` annotations)
    declared in the passed `type_hint`
    """
    metadata = getattr(type_hint, "__metadata__", None)
    if metadata is None:
        return
    for annotation in metadata:
        if isinstance(annotation, Depends):
            yield annotation


def get_type_hint_actual_type(type_hint: object) -> type:
    """
    Walks recursively through `type_hint` until finding the base type.
    """
    if isinstance(type_hint, type):
        return type_hint
    origin = getattr(type_hint, "__origin__", None)
    if origin is None:
        raise ValueError(f"{type_hint} does not seem to be an actual type hint")
    return get_type_hint_actual_type(origin)


class ConfigValidatorBase[Scope: int]:
    """
    A Mixin adding automatic validation of type and values after
    initializing an object
    """

    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]

    # Functions below simply cache the result of get_type_hints
    # and get_type_hints_recursive so we don't have to recompute them all the time
    # storing the result in the dataclass directly would create a lot of annoying noise
    # when iterating over parameters, even with ClassVar
    @classmethod
    @cache
    def get_type_hints(cls, *, include_extras: bool = False) -> dict[str, TypeForm[Any]]:
        """
        Caches and returns the results of `get_type_hints_recursive`
        on this object.
        This will be computed only once in the lifetime of this class

        Note: restricted to actual dataclass fields, as `typing.get_type_hints`
        also surfaces inherited `ClassVar` annotations (e.g. `__dataclass_fields__`)
        that are not config options.
        """
        field_names = {f.name for f in fields(cls)}
        return {
            name: hint
            for name, hint in get_type_hints(cls, include_extras=include_extras).items()
            if name in field_names
        }

    @classmethod
    @cache
    def get_type_hints_recursive(cls) -> dict[str, Any]:
        """
        Caches and returns the results of `get_type_hints`
        on this object.
        This will be computed only once in the lifetime of this class
        """
        return get_type_hints_recursive(cls)

    @classmethod
    @cache
    def get_aliases_lookup(cls) -> dict[str, str]:
        """
        Caches and returns the results of `get_aliases_lookup`
        on this object.
        This will be computed only once in the lifetime of this class
        """
        return {
            name: get_alias(type_hint) or name
            for name, type_hint in cls.get_type_hints(include_extras=True).items()
        }

    @classmethod
    @cache
    def get_aliases_reverse_lookup(cls) -> dict[str, str]:
        """
        Caches and returns the results of `get_aliases_lookup`
        on this object.
        This will be computed only once in the lifetime of this class
        """
        return {alias: name for name, alias in cls.get_aliases_lookup().items()}

    # _aliases may be used by import/export functions to present their keys
    # in formattings that do not follow Python conventions, hence are annoying
    # to declare in dataclasses
    @classmethod
    def toggle_alias(cls, name: str, *, alias_on: bool = True) -> str:
        """
        Can be overriden to replace aliases with their real names
        and vice-versa.
        Otherwise, looks for `Alias` annotation in the `Annotated` object
        and use it as the alias.
        If `alias_on`, is enabled, gets the aliased version of the field,
        else return the original name from this alias
        """
        if alias_on:
            return cls.get_aliases_lookup()[name]
        return cls.get_aliases_reverse_lookup()[name]

    def __post_init__(self) -> None:
        """
        Validates the config automatically after __init__
        """
        self.validate()

    @abstractmethod
    def get_scope(self, parameter: str) -> Scope:
        """
        Returns the scope annotated for `parameter`.
        Raises KeyError if parameter does not exist.
        """

    def get_type_hint(self, parameter: str) -> TypeForm[Any]:
        """
        Returns the type hint  for `parameter`.
        """
        return self.get_type_hints()[parameter]

    def cast_parameter_value(
        self,
        parameter: str,
        value: str,
        cast_strategy: Literal["json", "cfg"] = "json",
    ) -> object:
        """
        Casts `value` for `parameter` to the expected type according to
        the parameter type hint.
        """
        type_hint = self.get_type_hint(parameter)
        return cast_value(
            value,
            type_hint,
            cast_strategy=cast_strategy,
        )

    def _export(
        self,
        max_scope: DefaultScopes = DefaultScopes.BASIC,
        _parent_scope: DefaultScopes = DefaultScopes.BASIC,
        *,
        include_metadata: bool = False,
        apply_dependencies: bool = True,
    ) -> dict[str, Any]:
        """
        Exports the config with metadata.
        Every option is a key
        """
        meta_dict = {}
        documentation = self.build_documentation()
        type_hints = self.get_type_hints(include_extras=True)
        assert is_dataclass(self), "ConfigValidator should be used on dataclasses"
        for f in fields(self):
            field_scope = get_scope(type_hints[f.name])
            field_scope = max(field_scope, _parent_scope)

            # Checking if there's a dependency; if so, scope will be downgraded
            # to hidden if the dependency is not met
            if apply_dependencies:
                for dep in get_dependencies(type_hints[f.name]):
                    if not dep.is_met(self):
                        field_scope = DefaultScopes.HIDDEN

            if field_scope > max_scope:
                continue
            is_section = self.is_section(type_hints[f.name])
            if is_section:
                value = getattr(self, f.name)._export(
                    include_metadata=include_metadata,
                    max_scope=max_scope,
                    _parent_scope=field_scope,
                    apply_dependencies=apply_dependencies,
                )
            else:
                value = getattr(self, f.name)

            option_doc = documentation.get(f.name)
            doc_hint = option_doc.to_dict() if isinstance(option_doc, OptionDoc) else None
            alias = self.toggle_alias(f.name)
            if include_metadata:
                meta_dict[alias] = {
                    "value": value,
                    "doc": doc_hint,
                    "scope": field_scope,
                    "is_section": is_section,
                }
            else:
                meta_dict[alias] = value
        return meta_dict

    def dict_export(
        self,
        scope: DefaultScopes = DefaultScopes.HIDDEN,
        *,
        apply_dependencies: bool = True,
    ) -> dict[str, Any]:
        """
        Exports this object as nested dictionaries.
        This will only include the values, and not the doc hints.
        Use `meta_export` to get all meta information.

        Parameters
        ----------
        include_hidden_parameters: bool
            If enabled (default), fields marked with `Scope.HIDDEN`
            will still be included

        apply_dependencies: bool
            If enabled, the scope for parameters declaring `Depends` annotation
            will be defined by whether their dependency is met.
            If not, the scope will be set to the lowest possible (.HIDDEN).
            When `apply_dependencies` is off, `Depends` annotations become pure
            metadata with no impact on behavior.
        """
        return self._export(
            include_metadata=False,
            max_scope=scope,
            apply_dependencies=apply_dependencies,
        )

    def flat_dict_export(
        self,
        scope: DefaultScopes = DefaultScopes.HIDDEN,
        *,
        apply_dependencies: bool = True,
        separator: str = ".",
    ) -> dict[str, Any]:
        """
        Returns the config as a dict with a single-level of nesting.
        Nested options are handled as a single key with `.` delimited parts,
        e.g. section_1.section_2.option_name.
        Use `dict_export` if you want a nested layout.
        """
        nested_dict = self.dict_export(scope=scope, apply_dependencies=apply_dependencies)
        return {
            separator.join(key_parts): value for key_parts, value in dict_flattener(nested_dict)
        }

    def meta_export(
        self,
        scope: DefaultScopes = DefaultScopes.BASIC,
        *,
        apply_dependencies: bool = True,
    ) -> dict[str, Any]:
        """
        Exports the config with metadata.
        Each option will be sent as a dictionary, with `value` and `doc` keys.

        See also
        --------
        OptionDoc

        Parameters
        ----------
        apply_dependencies: bool
            If enabled, the scope for parameters declaring `Depends` annotation
            will be defined by whether their dependency is met.
            If not, the scope will be set to the lowest possible (.HIDDEN).
            When `apply_dependencies` is off, `Depends` annotations become pure
            metadata with no impact on behavior.
        """
        return self._export(
            include_metadata=True,
            max_scope=scope,
            apply_dependencies=apply_dependencies,
        )

    @staticmethod
    def is_section(type_hint: object) -> bool:
        """
        Returns
        -------
        bool
            Whether the passed field corresponds to a sub-section
        """
        if isinstance(type_hint, type):
            return issubclass(type_hint, ConfigValidatorBase)
        # resolving actual type
        actual_type = get_type_hint_actual_type(type_hint)
        return issubclass(actual_type, ConfigValidatorBase)

    @classmethod
    @cache
    def build_documentation(
        cls,
        use_alias: bool = False,
    ) -> DocumentationTree:
        """
        Builds the documentation hints for all options in this config
        """
        documentation: DocumentationTree = {}
        sections: dict[str, DocumentationTree] = {}
        field_map = {f.name: f for f in fields(cls)}
        for param, type_hint in cls.get_type_hints(include_extras=True).items():
            field = field_map[param]
            aliased_name = cls.toggle_alias(param) if use_alias else param

            default = field.default if isinstance(field, Field) else field

            if cls.is_section(type_hint):
                section_cls = get_type_hint_actual_type(type_hint)
                assert issubclass(section_cls, ConfigValidatorBase)
                sections[aliased_name] = section_cls.build_documentation(use_alias=use_alias)
            origin = getattr(type_hint, "__origin__", type_hint)

            if isinstance(origin, type):
                curated_type_hint = origin.__name__
            else:
                curated_type_hint = (
                    str(origin) if hasattr(type_hint, "__metadata__") else str(type_hint)
                )
            documentation[aliased_name] = OptionDoc(
                name=param,
                type_hint=curated_type_hint,
                default=default,
                validators=[ValidatorInfo.extract(v) for v in get_validators(type_hint)],
                docstrings=list(get_doc_hints(type_hint)),
            )

        if sections:
            documentation["__sections__"] = sections
        return documentation

    def validate_with_result(self) -> Result[None, ConfigValidationError]:
        """
        Result-based counterpart of `validate`.
        Runs the validators for all the fields of this section and
        returns the first detected error instead of raising.
        """
        # Note: include_extras is required to get our custom annotations
        for param, type_hint in self.get_type_hints(include_extras=True).items():
            value = getattr(self, param)
            if is_dataclass(value):
                # letting the sub-section handle its own validation logic
                if isinstance(value, ConfigValidatorBase):
                    match value.validate_with_result():
                        case Err() as err:
                            return err
                continue

            # checking the type - `check_type` reports a user-friendly
            # error if the type is invalid
            match check_type(value, type_hint):
                case Err() as err:
                    return err
            # checking any additional validator
            for validator in get_validators(type_hint):
                if value not in validator:
                    return Err(ValidatorConstraintError(Context(value, type_hint), validator))
        return Ok(None)

    def validate(self) -> None:
        """
        Runs the validators for all the fields of this section.

        Raises
        ------
        TypeError | ValueError | Exception
            Whatever exception the failing check's `exception_cls` specifies
            (see `_typecheck.check_type` and `ValidatorConstraintError`).
        """
        self.validate_with_result().unwrap()

    def get(self, *keys: str, default: object = MISSING, use_alias: bool = False) -> Any:  # noqa: ANN401
        """
        Allows accesing nested elements of arbitrary depths.
        Follows the keys given as argument and returns found leaf element.
        Mainly designed as a convenience method for functions that have to
        update this config object from dictionaries - can be used in combination with
        `dict_flattener`.

        Example:
        config.get("section_one", "sub_section_two", "a_param")
        is equivalent to config.section_one.sub_section_two.a_param
        """
        if not keys:
            raise ValueError("Must pass at least one key")
        target, *remaining = keys
        target = self.toggle_alias(target, alias_on=True) if use_alias else target
        try:
            if remaining:
                return getattr(self, target).get(*remaining, use_alias=use_alias)
            return getattr(self, target)
        except AttributeError as exc:
            if default is MISSING:
                msg = f"{target} while accessing {'->'.join(remaining)}"
                raise KeyError(msg) from exc
            return default

    def set(self, value: Any, *next_keys: str, use_alias: bool = False) -> None:  # noqa: ANN401
        """
        Allows setting nested elements of arbitrary depths.
        Follows the keys given as argument and updates found leaf element.
        Mainly designed as a convenience method for functions that have to
        update this config object from dictionaries - can be used in combination with
        `dict_flattener`.

        Example:
        config.set(42, "section_one", "sub_section_two", "a_param")
        is equivalent to config.section_one.sub_section_two.a_param = 42
        """
        if not next_keys:
            raise ValueError("Must pass at least one key")
        target, *remaining = next_keys
        target = self.toggle_alias(target, alias_on=True) if use_alias else target
        if not remaining:
            setattr(self, target, value)
        else:
            getattr(self, target).set(value, *remaining, use_alias=use_alias)

    def iter_recursive(
        self,
        *,
        use_alias: bool = False,
        max_scope: DefaultScopes = DefaultScopes.HIDDEN,
    ) -> Iterator[tuple[tuple[str, ...], Any]]:
        """
        Similar to `dict_flattener` but works on a ConfigValidator dataclass instead.
        Recursively iterates through this object.

        Yields
        ------
        tuple[tuple[str, ...], Any]
            First element is the series of keys (path) to the option
            Second is the option value
        """
        type_hints = self.get_type_hints(include_extras=True)
        for param in fields(self):
            alias = self.toggle_alias(param.name) if use_alias else param.name
            scope = get_scope(type_hints[param.name])

            if scope > max_scope:
                continue

            if self.is_section(type_hints[param.name]):
                for keys, value in getattr(self, param.name).iter_recursive(
                    use_alias=use_alias,
                    max_scope=max_scope,
                ):
                    yield (alias, *keys), value
            else:
                # checking if we should include in the scope
                yield (alias,), getattr(self, param.name)

    def dealias(self, keys: Iterable[str]) -> Iterator[str]:
        """
        Replaces aliases by their Python name recursively
        """
        *sections, option = keys
        next_section = self
        for s in sections:
            name = next_section.toggle_alias(s, alias_on=False)
            next_section = getattr(next_section, name)
            yield name
            if not isinstance(next_section, ConfigValidatorBase):
                return
        yield next_section.toggle_alias(option, alias_on=False)

    def update(
        self,
        nested_dict: dict[str, Any],
        *,
        cast_first: bool = False,
        cast_strategy: Literal["cfg", "json"] = "cfg",
        validate: bool = True,
        strict: bool = False,
    ) -> None:
        """
        Given a nested dictionary of string values,
        updates recursively this object from the values given in the dictionary.
        If `cast_first` is set,
        tries casting the values to the correct type before updating.
        """
        if validate:
            # running first the process on a deep-copied config
            # if no validation errors are reported,
            # we can apply it to ourselves
            shadow_config = deepcopy(self)
            shadow_config.update(
                nested_dict, cast_first=cast_first, cast_strategy=cast_strategy, validate=False
            )
            shadow_config.validate()

        type_hints = self.get_type_hints_recursive()
        for keys in dict_key_flattener(nested_dict):
            try:
                dealiased_keys = tuple(self.dealias(keys))
            except KeyError:
                if strict:
                    raise
                continue

            value = get_nested(nested_dict, *keys[: len(dealiased_keys)])

            try:
                type_hint = get_nested(type_hints, *dealiased_keys)
            except KeyError:
                if strict:
                    raise
                continue

            converted_value = cast_value(value, type_hint, cast_strategy) if cast_first else value
            try:
                self.set(converted_value, *dealiased_keys)
            except AttributeError:
                continue


def get_type_hints_recursive(config_cls: type[ConfigValidatorBase[Any]]) -> dict[str, Any]:
    type_hint_dict: dict[str, Any] = {}

    def _recurse(section_dict: dict[str, Any], cls: type[ConfigValidatorBase[Any]]) -> None:
        for k, v in cls.get_type_hints().items():
            if is_dataclass(v):
                assert isinstance(v, type)
                assert issubclass(v, ConfigValidatorBase)
                _recurse(section_dict.setdefault(k, {}), v)
            else:
                section_dict[k] = v

    _recurse(type_hint_dict, config_cls)
    return type_hint_dict


class ConfigValidator(ConfigValidatorBase[DefaultScopes]):
    def scopes(self) -> list[DefaultScopes]:
        return list(DefaultScopes.__members__.values())

    def get_scope(self, parameter: str) -> DefaultScopes:
        """
        Returns the scope annotated for `parameter`.
        Raises KeyError if parameter does not exist.
        """
        type_hints = self.get_type_hints(include_extras=True)
        return get_scope(type_hints[parameter])
