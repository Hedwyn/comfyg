"""
Tests the config loading/saving primitives (ConfigLoader, .cfg import/export).

@date: 07.07.2026
@author: Baptiste Pestourie
"""

from __future__ import annotations

import os
import tempfile
from configparser import ConfigParser
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from test_core import (
    ConfigWithHiddenOptions,
    ConfigWithNestedSections,
    ConfigWithSections,
)

from comfyg import (
    ConfigLoader,
    ConfigValidator,
    DefaultScopes,
    export_to_configparser,
    import_from_configparser,
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
