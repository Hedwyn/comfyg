"""
Loading/saving primitives for `ConfigValidatorBase` configs -- from and to
`ConfigParser`-backed .ini/.cfg files, and from and to .toml files (reading
via the stdlib `tomllib`, writing via a handrolled serializer since `tomllib`
is read-only).

@date: 07.07.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import math
import tomllib
from configparser import ConfigParser, Interpolation
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    IO,
    Any,
    TypeVar,
)

from ._core import ConfigValidatorBase, DefaultScopes, DocumentationTree, OptionDoc
from ._utils import set_nested


def value_to_string(value: Any) -> str:
    """
    Returns a string representation of the value
    """
    value_type = type(value)

    if value_type is dict:
        return "\n" + "\n".join(f"{key!s} = {value!s}" for key, value in value.items())

    if value_type in (list, tuple):
        return "\n" + "\n".join(str(v) for v in value)

    return str(value)


def import_from_configparser(
    config: type[ConfigValidatorBase[Any]] | ConfigValidatorBase[Any],
    parser: ConfigParser,
    *,
    strict: bool = False,
) -> ConfigValidatorBase[Any]:
    """
    Exports this config to a ConfigParser object.
    Note: use `:` separators to create subsections in the INI file,
    e.g., [section1:subsection1]

    Parameters
    ----------
    config: type[ConfigValidator] | ConfigValidator
        If already a config instance, updates the values using the ConfigParser.
        Otherwise, builds a brand new instance.

    strict: bool
        If enabled, will raise an error if an option is not declared in the target config.
    """
    config_dict: dict[str, Any] = {}
    for section_name, section in parser.items():
        sections = section_name.split(":")

        # casting the string values to their respective type
        for option_name, option_value in section.items():
            try:
                set_nested(config_dict, option_value, *sections, option_name)
            except AttributeError:
                if strict:
                    raise

    imported_config = config if isinstance(config, ConfigValidatorBase) else config()
    imported_config.update(config_dict, cast_first=True, strict=strict)
    return imported_config


def export_to_configparser(
    config: ConfigValidatorBase[Any],
    config_parser: ConfigParser | None = None,
    *,
    diff_only: bool = False,
    max_scope: DefaultScopes = DefaultScopes.INTERNAL,
) -> ConfigParser:
    """
    Exports this config to a ConfigParser object.

    Parameters
    ----------
    diff_only: bool
        Only applies if a config parser instance is passed.
        When enabled, compares the original and new config and only
        adds the differences to the config parser.
    """
    original_config = (
        import_from_configparser(config.__class__, config_parser)
        if config_parser and diff_only
        else None
    )
    config_parser = config_parser or ConfigParser(interpolation=Interpolation())
    # Note: asdict() already recursively converts member dataclasses to dicts as well
    for (*sections, option_name), option_value in config.iter_recursive(
        use_alias=True,
        max_scope=max_scope,
    ):
        section_name = ":".join(sections)
        if section_name not in config_parser.sections():
            config_parser.add_section(section_name)
        # section = config_parser[section_name]
        if (
            original_config is not None
            and diff_only
            and original_config.get(*sections, option_name) == option_value
        ):
            continue

        config_parser.set(section_name, option_name, value_to_string(option_value))

    return config_parser


class _CaseSensitiveConfigParser(ConfigParser):
    """
    A `ConfigParser` that preserves the case of option names.
    Overriding `optionxform` as a proper method (rather than reassigning the
    instance attribute) keeps this compatible with static type checkers.
    """

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def import_from_ini_file(
    config: type[ConfigValidatorBase[Any]] | ConfigValidatorBase[Any],
    filepath: str,
) -> ConfigValidatorBase[Any]:
    """
    Imports this config from a .ini file.
    """
    cf = _CaseSensitiveConfigParser()
    cf.read(filepath)
    return import_from_configparser(config, cf)


def export_to_ini_file(config: ConfigValidatorBase[Any], filepath: str) -> None:
    """
    Renders this config to a .ini config file
    Note: this does not support doc strings nor non-lowercase option names.
    Use ConfigLoader.save() if you need this functionality.
    """
    # Fetching the current config
    # so we do not overwrite fields that do not need to
    new_config = export_to_configparser(config, None)
    with Path(filepath).open("w+") as f:  # pylint: disable=unspecified-encoding
        new_config.write(f)


def import_from_toml_file(
    config: type[ConfigValidatorBase[Any]] | ConfigValidatorBase[Any],
    filepath: str,
    *,
    strict: bool = False,
) -> ConfigValidatorBase[Any]:
    """
    Imports this config from a .toml file.

    Parameters
    ----------
    config: type[ConfigValidator] | ConfigValidator
        If already a config instance, updates the values using the parsed TOML.
        Otherwise, builds a brand new instance.

    strict: bool
        If enabled, will raise an error if an option is not declared in the target config.
    """
    with Path(filepath).open("rb") as f:
        toml_dict = tomllib.load(f)

    imported_config = config if isinstance(config, ConfigValidatorBase) else config()
    # TOML values are already natively typed (int, float, bool, list, nested
    # tables, ...) -- the "json" cast strategy passes non-string values through
    # untouched, so no per-line string casting is needed here (unlike .ini).
    imported_config.update(toml_dict, cast_first=True, cast_strategy="json", strict=strict)
    return imported_config


_BARE_TOML_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _toml_escape_string(value: str) -> str:
    """
    Escapes `value` for use inside a TOML basic (double-quoted) string.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _toml_key(key: str) -> str:
    """
    Renders `key` as a bare TOML key if possible, else as a quoted string key.
    """
    if key and all(c in _BARE_TOML_KEY_CHARS for c in key):
        return key
    return _toml_escape_string(key)


def _toml_scalar(value: object) -> str:
    """
    Renders `value` as a TOML value literal.
    `None` has no TOML representation and must be filtered out by the caller
    before reaching here.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_escape_string(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{_toml_key(k)} = {_toml_scalar(v)}" for k, v in value.items()) + " }"
        )
    raise TypeError(f"Cannot serialize {value!r} to TOML")


def _write_toml_table(
    fp: IO[str],
    table: dict[str, Any],
    documentation: DocumentationTree,
    *,
    path: tuple[str, ...],
    skip_doc: bool,
) -> None:
    """
    Recursively writes `table` (a plain nested dict, as produced from
    `ConfigValidatorBase.iter_recursive` + `set_nested`) to `fp` as TOML.

    A key is written as a nested `[a.b.c]` table header (rather than an
    inline `{ ... }` value) only if it is declared as a section in
    `documentation["__sections__"]` -- this is what disambiguates an actual
    config sub-section from a plain dict-typed option value, since both show
    up as a `dict` in `table`.
    """
    subsections_doc = documentation.get("__sections__", {})
    assert isinstance(subsections_doc, dict)

    for key, value in table.items():
        if key in subsections_doc:
            continue
        option_doc = documentation.get(key)
        if not skip_doc and isinstance(option_doc, OptionDoc):
            for docstring in option_doc.docstrings:
                for line in docstring.split("\n"):
                    fp.write(f"# {line}\n")
        fp.write(f"{_toml_key(key)} = {_toml_scalar(value)}\n")

    for key, sub_doc in subsections_doc.items():
        if key not in table:
            continue
        value = table[key]
        assert isinstance(value, dict), f"Expected a section dict for {key!r}, got {value!r}"
        assert isinstance(sub_doc, dict)
        sub_path = (*path, key)
        fp.write("\n[" + ".".join(_toml_key(p) for p in sub_path) + "]\n")
        _write_toml_table(fp, value, sub_doc, path=sub_path, skip_doc=skip_doc)


def export_to_toml_file(
    config: ConfigValidatorBase[Any],
    filepath: str,
    *,
    skip_doc: bool = False,
    max_scope: DefaultScopes = DefaultScopes.INTERNAL,
) -> None:
    """
    Renders this config to a .toml config file.
    Note: `None`-valued options are omitted (TOML has no null literal) --
    on reload, the dataclass default is used for them instead.
    """
    tree: dict[str, Any] = {}
    for keys, value in config.iter_recursive(use_alias=True, max_scope=max_scope):
        if value is None:
            continue
        set_nested(tree, value, *keys)

    documentation = config.build_documentation(use_alias=True)
    with Path(filepath).open("w+") as f:  # pylint: disable=unspecified-encoding
        _write_toml_table(f, tree, documentation, path=(), skip_doc=skip_doc)


class _DocInterpolation(Interpolation):
    """
    A custom interpolator that allows injects docstrings in a ConfigParser
    before an actual value gets written.
    Slighly hackish since `before_write` hook from Interpolators
    are only applying to the value itself, not the entire `key=value` formatting.
    As a consequence we are getting a reference to the file pointer we are writing
    to itself to prepend the doc everytime `before_write` is called
    (the return value of `before_write` itself is then left identical).
    """

    def __init__(self, doc: dict[str, Any], fp: IO[str]) -> None:
        """
        Parameters
        ----------
        doc: dict[str, Any]
            Mapping each parameter to its docstring.

        fp: IO[str]
            The actual file pointer to which the config parser is writing to.
            This interpolator will inject the doc before the parameter gets written.
        """
        self._doc = doc
        self._fp = fp

    def before_write(  # type: ignore
        self,
        parser: ConfigParser,
        section: str,
        option: str,
        value: Any,
    ) -> str:
        """
        Writes the doc string to the file pointer before parent's `before_write`
        gets called.
        """
        sections = section.split(":")
        section_doc = self._doc
        for subsection in sections:
            section_doc = section_doc.get("__sections__", {}).get(subsection)

        option_doc = section_doc.get(option)

        # section_doc = get_nested(self._doc, *section.split(':'), option, default=None)
        if option_doc:
            self._fp.write("\n")
            for line in str(option_doc).split("\n"):
                self._fp.write("# " + line + "\n")
        ret = super().before_write(parser, section, option, value)
        return ret


@dataclass
class HardwareDiscovery(ConfigValidatorBase[DefaultScopes]):
    """
    Used to discover the hardware version of the controller.
    """

    version: str = "unknown"


@dataclass
class HwDiscoveryConfig(ConfigValidatorBase[DefaultScopes]):
    """
    Used to discover the hardware version of the controller.
    """

    hardware: HardwareDiscovery = field(default_factory=HardwareDiscovery)


ConfigCls = TypeVar("ConfigCls", bound=ConfigValidatorBase[Any])


class ConfigLoader[ConfigCls: ConfigValidatorBase[Any]]:
    """
    A replacement for ConfigParser, that supports a few additional things:
    * Sub-sections with more than one level of detph using `:` as separator, e.g.:
    [Section1:Subsection1]
    option_1 = ...
    * Loading comments as docstrings, e.g:
    [Section1:Subsection1]
    # This is the documentation for option_1
    option_1 = ...
    * Linking the parsed config to its actual dataclass
    * (Optional) Providing persistency by saving user settings automatically
    """

    def __init__(
        self,
        base_config: type[ConfigCls] | ConfigCls | None = None,
        config_path: str | None = None,
        *,
        autosave: bool = False,
    ) -> None:
        self._config_cls: type[ConfigCls] | None = None
        self._config: ConfigCls | None = None
        if base_config is not None:
            if isinstance(base_config, type):
                if not issubclass(base_config, ConfigValidatorBase):
                    raise TypeError("Config should inherit from ConfigValidator")
                self._config_cls = base_config
            else:
                self._config_cls = type(base_config)
                self._config = base_config

        self._config_path: str | None = config_path
        self._config_parser: ConfigParser | None = None
        self.autosave: bool = autosave

    @property
    def config(self) -> ConfigCls:
        """
        Current instance of the config
        """
        if self._config is not None:
            return self._config
        if self._config_cls is None:
            raise RuntimeError("This ConfigLoader has no config class attached to it yet")
        self._config = self._config_cls()
        return self._config

    @property
    def config_path(self) -> str:
        """
        Returns
        -------
        str
            Path to the file to load/save the config
        """
        if self._config_path is None:
            raise ValueError("No config path has been set yet")
        return self._config_path

    @property
    def config_parser(self) -> ConfigParser:
        """
        The ConfigParser object in which the config has been loaded
        """
        if self._config_parser is None:
            raise RuntimeError("Config is not loaded yet, consider calling load()")

        return self._config_parser

    def load(self, *, raise_if_not_found: bool = False, strict: bool = False) -> ConfigCls:
        """
        Loads the current values for the config from the defined
        config path

        Parameters
        ----------
        raise_if_not_found: bool
            Raises `FileNotFoundError` if the config file is not found.
            Default is to ignore as ConfigValidator provide defaults for every parameter.

        strict: bool
            If enabled, raises an error if getting unknown options in the source file.
            Otherwise, ignores parameters.
        """
        self._config_parser = _CaseSensitiveConfigParser(interpolation=Interpolation())
        if raise_if_not_found and not Path(self.config_path).exists():
            raise FileNotFoundError(f"Config not found at {self.config_path}")
        self._config_parser.read(self.config_path)
        import_from_configparser(self.config, self._config_parser, strict=strict)
        return self.config

    def reset(self) -> ConfigCls:
        """
        Destroys the current config and creates a new defaulted one.
        Commits immediately the changes on disk.
        Note: as stated above, the config is not mutated but destroyed and replaced
        by a new one.

        Returns
        -------
        ConfigCls
            The new config created and now tracked internally.
        """
        if self._config_cls is None:
            raise RuntimeError("This ConfigLoader has no config class attached to it yet")
        new_config = self._config_cls()
        self._config = new_config
        self.save()
        return new_config

    def save(
        self,
        *,
        skip_doc: bool = False,
        max_scope: DefaultScopes = DefaultScopes.INTERNAL,
    ) -> None:
        """
        Saves the config to the config_path
        """
        with Path(self.config_path).open("w+") as cf:
            # using _DocInterpolation as a hook to inject doc strings
            # before the key-value pair gets written
            if skip_doc:
                parser = _CaseSensitiveConfigParser()
            else:
                doc_interpolation = _DocInterpolation(
                    self.config.build_documentation(use_alias=True),
                    cf,
                )
                parser = _CaseSensitiveConfigParser(interpolation=doc_interpolation)
            # loading our values in the ConfigParser
            export_to_configparser(self.config, parser, max_scope=max_scope)
            parser.write(cf)

    def __del__(self) -> None:
        """
        Saves before GC if `autosave` is enabled.
        If this object exists in the global scope this would happen
        when the program exits.
        """
        if self.autosave:
            self.save()

    # Helper to help legacy-compliance - this might get removed eventually
    # We are reproducing a similar getter interface as a config parser here ,
    # so that ConfigLoader can be used as a drop-in replacement
    # without affecting the higer-level code
    # The idea being to set `AppConfig` global var to the ConfigLoader instance
    # instead of ConfigParser

    def get(self, section: str, option: str, *args: Any, **kwargs: Any) -> str:
        """
        Gets a value from the internal config parser
        """
        return self.config_parser.get(section, option, *args, **kwargs)

    def getint(self, section: str, option: str, *args: Any, **kwargs: Any) -> int:
        """
        Gets a integer value from the internal config parser
        """
        return self.config_parser.getint(section, option, *args, **kwargs)

    def getboolean(self, section: str, option: str, *args: Any, **kwargs: Any) -> bool:
        """
        Gets a bool value from the internal config parser
        """
        return self.config_parser.getboolean(section, option, *args, **kwargs)

    def getfloat(self, section: str, option: str, *args: Any, **kwargs: Any) -> float:
        """
        Gets a float value from the internal config parser
        """
        return self.config_parser.getfloat(section, option, *args, **kwargs)


class TomlConfigLoader[ConfigCls: ConfigValidatorBase[Any]]:
    """
    A TOML-backed counterpart to `ConfigLoader`.
    Supports the same nested-section and doc-comment behavior, minus the
    ConfigParser-compliance getters (`get`/`getint`/`getboolean`/`getfloat`)
    and the `diff_only` save option, neither of which has a TOML equivalent
    (there's no `ConfigParser` object underneath to defer to, or to diff against).
    """

    def __init__(
        self,
        base_config: type[ConfigCls] | ConfigCls | None = None,
        config_path: str | None = None,
        *,
        autosave: bool = False,
    ) -> None:
        self._config_cls: type[ConfigCls] | None = None
        self._config: ConfigCls | None = None
        if base_config is not None:
            if isinstance(base_config, type):
                if not issubclass(base_config, ConfigValidatorBase):
                    raise TypeError("Config should inherit from ConfigValidator")
                self._config_cls = base_config
            else:
                self._config_cls = type(base_config)
                self._config = base_config

        self._config_path: str | None = config_path
        self.autosave: bool = autosave

    @property
    def config(self) -> ConfigCls:
        """
        Current instance of the config
        """
        if self._config is not None:
            return self._config
        if self._config_cls is None:
            raise RuntimeError("This TomlConfigLoader has no config class attached to it yet")
        self._config = self._config_cls()
        return self._config

    @property
    def config_path(self) -> str:
        """
        Returns
        -------
        str
            Path to the file to load/save the config
        """
        if self._config_path is None:
            raise ValueError("No config path has been set yet")
        return self._config_path

    def load(self, *, raise_if_not_found: bool = False, strict: bool = False) -> ConfigCls:
        """
        Loads the current values for the config from the defined
        config path

        Parameters
        ----------
        raise_if_not_found: bool
            Raises `FileNotFoundError` if the config file is not found.
            Default is to ignore as ConfigValidator provide defaults for every parameter.

        strict: bool
            If enabled, raises an error if getting unknown options in the source file.
            Otherwise, ignores parameters.
        """
        if not Path(self.config_path).exists():
            if raise_if_not_found:
                raise FileNotFoundError(f"Config not found at {self.config_path}")
            return self.config
        import_from_toml_file(self.config, self.config_path, strict=strict)
        return self.config

    def reset(self) -> ConfigCls:
        """
        Destroys the current config and creates a new defaulted one.
        Commits immediately the changes on disk.
        Note: as stated above, the config is not mutated but destroyed and replaced
        by a new one.

        Returns
        -------
        ConfigCls
            The new config created and now tracked internally.
        """
        if self._config_cls is None:
            raise RuntimeError("This TomlConfigLoader has no config class attached to it yet")
        new_config = self._config_cls()
        self._config = new_config
        self.save()
        return new_config

    def save(
        self,
        *,
        skip_doc: bool = False,
        max_scope: DefaultScopes = DefaultScopes.INTERNAL,
    ) -> None:
        """
        Saves the config to the config_path
        """
        export_to_toml_file(self.config, self.config_path, skip_doc=skip_doc, max_scope=max_scope)

    def __del__(self) -> None:
        """
        Saves before GC if `autosave` is enabled.
        If this object exists in the global scope this would happen
        when the program exits.
        """
        if self.autosave:
            self.save()
