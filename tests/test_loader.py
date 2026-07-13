"""
Tests the config loading/saving primitives (ConfigLoader, .cfg import/export).

@date: 07.07.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from configparser import ConfigParser
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from test_core import (
    ConfigWithHiddenOptions,
    ConfigWithIterable,
    ConfigWithNestedSections,
    ConfigWithSections,
    DocumentedConfig,
)

from comfyg import (
    ConfigLoader,
    ConfigValidator,
    DefaultScopes,
    TomlConfigLoader,
    export_to_configparser,
    export_to_toml_file,
    import_from_configparser,
    import_from_toml_file,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

CONFIG_FILE_CONTENT = """\
[section_one]

an_int = 42

[section_two]
a_float = 3.14
a_str = FizzBuzz\
"""

CONFIG_FILE_CONTENT_WITH_SUBSECTIONS = """\
[section_one:subsection_one]

an_int = 42

[section_two]
a_float = 3.14
a_str = FizzBuzz\

[section_one:subsection_two]
a_bool = True
"""

TOML_CONFIG_FILE_CONTENT = """\
[section_one]
an_int = 42

[section_two]
a_float = 3.14
a_str = "FizzBuzz"
"""

TOML_CONFIG_FILE_CONTENT_WITH_SUBSECTIONS = """\
[section_one.subsection_one]
an_int = 42

[section_one.subsection_two]
a_bool = true

[section_two]
a_float = 3.14
a_str = "FizzBuzz"
"""


def test_parse_config() -> None:
    """
    Checks that when building config from a configfile-like object,
    all string values are automatically casted to the correct type
    """
    cf = ConfigParser()
    cf.read_string(CONFIG_FILE_CONTENT)
    built_config = import_from_configparser(ConfigWithSections, cf)
    assert built_config == ConfigWithSections()


def test_parse_config_nested_sections() -> None:
    """
    Checks the parsing when using `:` delimiters to nest sub-sections.
    """
    cf = ConfigParser()
    cf.read_string(CONFIG_FILE_CONTENT_WITH_SUBSECTIONS)
    built_config = import_from_configparser(ConfigWithNestedSections, cf)
    assert built_config == ConfigWithNestedSections()


def test_export_config() -> None:
    """
    Checks that when building config from a configfile-like object,
    all string values are automatically casted to the correct type
    """
    cf = ConfigParser()
    cf.read_string(CONFIG_FILE_CONTENT)
    built_config = import_from_configparser(ConfigWithSections, cf)

    built_config.section_two.a_float = 1.67
    output_parser = export_to_configparser(built_config)
    output_str = StringIO()
    output_parser.write(output_str)
    output_str.seek(0)

    obtained_lines = list([l for l in output_str.read().split("\n") if l])
    original_lines = list([l for l in CONFIG_FILE_CONTENT.split("\n") if l])

    assert len(obtained_lines) == len(original_lines)
    for original_line, obtained_line in zip(original_lines, obtained_lines):
        if original_line.startswith("a_float"):
            assert "1.67" in obtained_line
        else:
            assert original_line == obtained_line


@pytest.mark.parametrize(
    "config,expects",
    [
        (
            ConfigWithNestedSections(),
            [l for l in CONFIG_FILE_CONTENT_WITH_SUBSECTIONS.split("\n") if l],
        ),
    ],
)
def test_config_loader(config: ConfigValidator, expects: Sequence[str]) -> None:
    """
    Checks that config loader is able to save .cfg files properly
    """
    config = ConfigWithNestedSections()
    # config = AnnotatedConfigWithSections()
    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "config.cfg")
        loader = ConfigLoader(config, conf_path)
        loader.save(skip_doc=True)
        with open(conf_path) as f:
            lines = [l for l in f if l != "\n"]
            assert len(lines) == len(expects)

            # Check that it loads properly# Note: loosely testing here
            # as order might not be preserved and that's okay
            for obtained in lines:
                assert obtained.strip() in expects


def test_export_config_hidden_parameters_excluded() -> None:
    """
    Checks that when building config from a configfile-like object,
    all string values are automatically casted to the correct type
    """
    built_config = ConfigWithHiddenOptions()
    output_parser = export_to_configparser(built_config)
    assert "section_one" in output_parser.sections()
    # this should be included
    assert "an_int" in output_parser["section_one"]
    # this should not
    assert "a_str" not in output_parser["section_one"]

    # now testing with higher scope
    output_parser = export_to_configparser(built_config, max_scope=DefaultScopes.HIDDEN)
    assert "an_int" in output_parser["section_one"]
    assert "a_str" in output_parser["section_one"]


def test_hidden_parameters_are_imported() -> None:
    """
    Checks that hidden parameters are still imported when defined.
    """
    cf = ConfigParser()
    cf.read_dict(
        {
            "section_one": {
                "an_int": 0,
                "a_str": "Fizz",
            },
        },
    )
    config = ConfigWithHiddenOptions()
    with tempfile.NamedTemporaryFile("w+") as f:
        loader = ConfigLoader(config, f.name)
        cf.write(f)
        f.flush()
        loader.load()
    assert config.section_one.an_int == 0
    assert config.section_one.a_str == "Fizz"


def test_parse_toml_config() -> None:
    """
    Checks that a config is properly built from a .toml file's content.
    """
    with tempfile.NamedTemporaryFile("w+", suffix=".toml") as f:
        f.write(TOML_CONFIG_FILE_CONTENT)
        f.flush()
        built_config = import_from_toml_file(ConfigWithSections, f.name)
    assert built_config == ConfigWithSections()


def test_parse_toml_config_nested_sections() -> None:
    """
    Checks parsing of native TOML nested tables (dotted table headers),
    as opposed to the `:`-separated hack required for .ini files.
    """
    with tempfile.NamedTemporaryFile("w+", suffix=".toml") as f:
        f.write(TOML_CONFIG_FILE_CONTENT_WITH_SUBSECTIONS)
        f.flush()
        built_config = import_from_toml_file(ConfigWithNestedSections, f.name)
    assert built_config == ConfigWithNestedSections()


def test_export_toml_config_roundtrip() -> None:
    """
    Checks that exporting then re-importing a .toml file preserves the config,
    including a value that differs from the class defaults.
    """
    built_config = ConfigWithNestedSections()
    built_config.section_two.a_float = 1.67

    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "config.toml")
        export_to_toml_file(built_config, conf_path)

        # the file produced must be valid, parseable TOML
        with open(conf_path, "rb") as f:
            raw = tomllib.load(f)
        assert raw["section_two"]["a_float"] == 1.67

        reloaded_config = import_from_toml_file(ConfigWithNestedSections, conf_path)
    assert reloaded_config == built_config


def test_export_toml_config_hidden_parameters_excluded() -> None:
    """
    Checks that hidden options are excluded from the .toml export by default,
    and included when raising `max_scope`.
    """
    built_config = ConfigWithHiddenOptions()

    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "config.toml")

        export_to_toml_file(built_config, conf_path)
        with open(conf_path, "rb") as f:
            raw = tomllib.load(f)
        assert "an_int" in raw["section_one"]
        assert "a_str" not in raw["section_one"]

        export_to_toml_file(built_config, conf_path, max_scope=DefaultScopes.HIDDEN)
        with open(conf_path, "rb") as f:
            raw = tomllib.load(f)
        assert "an_int" in raw["section_one"]
        assert "a_str" in raw["section_one"]


def test_toml_hidden_parameters_are_imported() -> None:
    """
    Checks that hidden parameters are still imported when defined in the source file,
    even though they are excluded from exports by default.
    """
    config = ConfigWithHiddenOptions()
    with tempfile.NamedTemporaryFile("w+", suffix=".toml") as f:
        f.write('[section_one]\nan_int = 0\na_str = "Fizz"\n')
        f.flush()
        loader = TomlConfigLoader(config, f.name)
        loader.load()
    assert config.section_one.an_int == 0
    assert config.section_one.a_str == "Fizz"


def test_export_toml_doc_comments() -> None:
    """
    Checks that option docstrings are rendered as `#` comments above their key,
    and can be omitted altogether with `skip_doc`.
    """
    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "config.toml")

        export_to_toml_file(DocumentedConfig(), conf_path)
        content = open(conf_path).read()
        assert "# Some completely arbitrary integer\nan_int = 42" in content
        assert "# A quirky string\na_str = " in content

        export_to_toml_file(DocumentedConfig(), conf_path, skip_doc=True)
        content = open(conf_path).read()
        assert "#" not in content


def test_export_toml_none_values_are_skipped() -> None:
    """
    Checks that `None`-valued options are omitted from the .toml export,
    since TOML has no null literal, and that reloading falls back to the default.
    """

    @dataclass
    class ConfigWithOptional(ConfigValidator):
        maybe_str: str | None = None

    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "config.toml")
        export_to_toml_file(ConfigWithOptional(), conf_path)

        with open(conf_path, "rb") as f:
            raw = tomllib.load(f)
        assert "maybe_str" not in raw

        reloaded_config = import_from_toml_file(ConfigWithOptional, conf_path)
    assert reloaded_config == ConfigWithOptional()


def test_export_toml_list_and_dict_values() -> None:
    """
    Checks that list-typed and dict-typed option values (as opposed to actual
    config sub-sections, also rendered as nested dicts) round-trip through
    a .toml export/import: lists as inline arrays, dicts as inline tables.
    """
    config = ConfigWithIterable()
    config.section_one.a_list = ["a", "b"]
    config.section_one.a_dict_of_list = {"x": ["a", "b"], "y": ["c"]}

    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "config.toml")
        export_to_toml_file(config, conf_path)

        with open(conf_path, "rb") as f:
            raw = tomllib.load(f)
        assert raw["section_one"]["a_list"] == ["a", "b"]
        assert raw["section_one"]["a_dict_of_list"] == {"x": ["a", "b"], "y": ["c"]}

        reloaded_config = import_from_toml_file(ConfigWithIterable, conf_path)
    assert reloaded_config == config


_TOML_NESTED_LINES = [l for l in TOML_CONFIG_FILE_CONTENT_WITH_SUBSECTIONS.split("\n") if l]


@pytest.mark.parametrize(
    "config,expects",
    [
        (
            ConfigWithNestedSections(),
            # `section_one` itself carries no direct option, only sub-sections,
            # but still gets its own (empty) `[section_one]` table header.
            ["[section_one]", *_TOML_NESTED_LINES],
        ),
    ],
)
def test_toml_config_loader(config: ConfigValidator, expects: Sequence[str]) -> None:
    """
    Checks that `TomlConfigLoader` is able to save .toml files properly.
    """
    config = ConfigWithNestedSections()
    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "config.toml")
        loader = TomlConfigLoader(config, conf_path)
        loader.save(skip_doc=True)
        with open(conf_path) as f:
            lines = [l for l in f if l != "\n"]
            assert len(lines) == len(expects)

            # Note: loosely testing here as order might not be preserved
            for obtained in lines:
                assert obtained.strip() in expects


def test_toml_config_loader_load_missing_file_defaults() -> None:
    """
    Checks that loading from a non-existent .toml path falls back to defaults,
    unless `raise_if_not_found` is set (mirrors `ConfigLoader.load` behavior --
    tomllib has no ConfigParser-like graceful handling of a missing file).
    """
    with tempfile.TemporaryDirectory() as confdir:
        conf_path = os.path.join(confdir, "does_not_exist.toml")
        loader = TomlConfigLoader(ConfigWithSections, conf_path)
        assert loader.load() == ConfigWithSections()

        with pytest.raises(FileNotFoundError):
            loader.load(raise_if_not_found=True)
