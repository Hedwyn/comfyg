"""
Tests the config annotations/validation system.

@date: 25.08.2024
@author: Baptiste Pestourie
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Annotated

import pytest

from comfyg import (
    Alias,
    Choices,
    ConfigValidator,
    DefaultScopes,
    Depends,
    OptionDoc,
    Range,
    dict_flattener,
    get_type_hints_recursive,
)


@dataclass
class ConfigWithFloat(ConfigValidator):
    a_float: float = 0.0


def test_int_is_float_is_valid() -> None:
    """
    Checks that integer values passed for float fields are validated
    """
    config = ConfigWithFloat()
    config.validate()
    config.a_float = 0
    config.validate()


@dataclass
class SimpleConfig(ConfigValidator):
    """Test config class"""

    an_int: int
    a_float: float
    a_str: str


@dataclass
class SimpleConfigWithDefaults(ConfigValidator):
    """Test config class"""

    an_int: int = 42
    a_float: float = 3.14
    a_str: str = "FizzBuzz"


@dataclass
class SectionOne(ConfigValidator):
    an_int: int = 42


@dataclass
class SectionTwo(ConfigValidator):
    a_float: float = 3.14
    a_str: str = "FizzBuzz"


@dataclass
class ConfigWithSections(ConfigValidator):
    section_one: SectionOne = field(default_factory=SectionOne)
    section_two: SectionTwo = field(default_factory=SectionTwo)


@dataclass
class SubSectionOne(ConfigValidator):
    an_int: int = 42


@dataclass
class SubSectionTwo(ConfigValidator):
    a_bool: bool = True


@dataclass
class SectionOneNested(ConfigValidator):
    subsection_one: SubSectionOne = field(default_factory=SubSectionOne)
    subsection_two: SubSectionTwo = field(default_factory=SubSectionTwo)


@dataclass
class ConfigWithNestedSections(ConfigValidator):
    section_one: SectionOneNested = field(default_factory=SectionOneNested)
    section_two: SectionTwo = field(default_factory=SectionTwo)


def test_init_simple_config():
    """
    Sanity check: verifies that the __init__ method has been injected properly
    """
    assert SimpleConfig(42, 3.14, "FizzBuzz")


def test_init_simple_config_with_defaults():
    """
    Checks that default values are properly injected in the __init__
    """
    assert SimpleConfigWithDefaults()


def test_config_detects_invalid_type():
    """
    Checks that passing an invalid type raises a validation error
    """
    with pytest.raises(Exception):
        SimpleConfigWithDefaults(a_str=0)


def test_config_with_section_init():
    """
    Check that when building config out of multiple sections,
    the sections are properly initialized
    """
    config = ConfigWithSections()
    for key, value in asdict(SectionOne()).items():
        assert getattr(config.section_one, key) == value
    for key, value in asdict(SectionTwo()).items():
        assert getattr(config.section_two, key) == value


def test_dict_flattener() -> None:
    """
    Checks that the iterator flattening dictionaries
    works properly
    """
    nested_dict = {
        "a": 0,
        "b": {"c": 1, "d": {"e": 2, "f": 3}},
    }
    it = dict_flattener(nested_dict)
    assert next(it) == (["a"], 0)
    assert next(it) == (["b", "c"], 1)
    assert next(it) == (["b", "d", "e"], 2)
    assert next(it) == (["b", "d", "f"], 3)


def test_get_type_hints_recursive() -> None:
    """
    Checks that `get_type_hints_recursive` iterates through
    sub-sections properly
    """
    it = dict_flattener(get_type_hints_recursive(ConfigWithSections))
    assert next(it) == (["section_one", "an_int"], int)
    assert next(it) == (["section_two", "a_float"], float)
    assert next(it) == (["section_two", "a_str"], str)


def test_config_get():
    """
    Check if the nested get method of config
    returns the correct element
    """
    config = ConfigWithSections()
    assert config.get("section_one") == SectionOne()
    assert config.get("section_one", "an_int") == 42


def test_config_set():
    """
    Check if the nested set method of config
    updates the correct element
    """
    config = ConfigWithSections()
    config.set(0, "section_one", "an_int")
    assert config.get("section_one", "an_int") == 0


@dataclass
class ConfigWithValidators(ConfigValidator):
    an_int: Annotated[int, Range(0, 100)] = 42
    a_float: Annotated[float, Range(0, 100)] = 3.14
    a_str: Annotated[str, Choices("Fizz", "Buzz", "FizzBuzz")] = "FizzBuzz"


@dataclass
class AnnotatedSectionOne(ConfigValidator):
    an_int: Annotated[int, Range(0, 100)] = 42


@dataclass
class AnnotatedSectionTwo(ConfigValidator):
    a_float: Annotated[float, Range(0, 100)] = 3.14
    a_str: Annotated[str, Choices("Fizz", "Buzz", "FizzBuzz")] = "FizzBuzz"


@dataclass
class AnnotatedConfigWithSections(ConfigValidator):
    section_one: AnnotatedSectionOne = field(default_factory=AnnotatedSectionOne)
    section_two: AnnotatedSectionTwo = field(default_factory=AnnotatedSectionTwo)


@dataclass
class SectionWithIterable(ConfigValidator):
    a_list: Annotated[list[str], Choices("a", "b", "c")] = field(default_factory=list)
    a_dict_of_list: Annotated[
        dict[str, list[str]],
        Choices("a", "b", "c"),
    ] = field(default_factory=dict)


@dataclass
class ConfigWithIterable(ConfigValidator):
    section_one: SectionWithIterable = field(default_factory=SectionWithIterable)


def test_range_validator():
    """
    Checks tha values annotated with Range raise a TypError
    when passing a value out of range
    """
    # default values should be okay
    ConfigWithValidators()
    # this is ut of range
    with pytest.raises(Exception):
        ConfigWithValidators(an_int=-1)
    with pytest.raises(Exception):
        ConfigWithValidators(an_int=101)


def test_choices_validator():
    """
    Checks that values annotated with Choices raise a TypeError
    when passing that's not a vlaid choice
    """
    # default values should be okay
    ConfigWithValidators()
    # this is ut of range
    for choice in ["Fizz", "Buzz", "FizzBuzz"]:
        ConfigWithValidators(a_str=choice)

    with pytest.raises(Exception):
        ConfigWithValidators(a_str="BuzzFizz")


def test_config_with_iterable_validators_list() -> None:
    with pytest.raises(Exception):
        # should raise for invalid type `int`
        ConfigWithIterable(section_one=SectionWithIterable(a_list=["a", 2]))

    with pytest.raises(Exception):
        # should raise for invalid type choice d
        ConfigWithIterable(section_one=SectionWithIterable(a_list=["a", "d"]))


def test_config_with_iterable_validators_dict_of_list() -> None:
    with pytest.raises(Exception):
        # should raise for invalid type `int`
        ConfigWithIterable(section_one=SectionWithIterable(a_dict_of_list={"somekey": ["a", 2]}))

    with pytest.raises(Exception):
        # should raise for invalid type choice d
        ConfigWithIterable(section_one=SectionWithIterable(a_dict_of_list={"somekey": ["a", "d"]}))

    # this should pass validation
    ConfigWithIterable(section_one=SectionWithIterable(a_dict_of_list={"somekey": ["a", "c"]}))


@pytest.mark.parametrize(
    "section,param,value",
    [
        ("section_one", "an_int", -1),
        ("section_one", "an_int", 101),
        ("section_two", "a_float", -1.5),
        ("section_two", "a_float", 200.0),
        ("section_two", "a_str", "BuzzFizz"),
    ],
)
def test_config_section_are_validated(section: str, param: str, value: object):
    """
    Checks that all children sections of a config are validated
    when the top-level validation is called
    """
    config = AnnotatedConfigWithSections()
    with pytest.raises(Exception):
        section = getattr(config, section)
        setattr(section, param, value)
        config.validate()


@dataclass
class DocumentedSection(ConfigValidator):
    an_int: Annotated[int, "Some completely arbitrary integer", Range(0, 100)] = 42
    a_str: Annotated[str, "A quirky string", Choices("Fizz", "Buzz", "FizzBuzz")] = "FizzBuzz"


@dataclass
class DocumentedConfig(ConfigValidator):
    section_one: Annotated[DocumentedSection, "A documented section"] = field(
        default_factory=DocumentedSection,
    )


def _check_doc(doc_dict: dict[str, OptionDoc]) -> None:
    """
    Statically check that the documentation for options
    are properly generated for `DocumentedSection`
    """
    an_int_doc = doc_dict["an_int"]
    assert an_int_doc.name == "an_int"
    assert an_int_doc.type_hint == int.__name__
    assert an_int_doc.default == 42
    assert len(an_int_doc.validators) == 1

    a_str_doc = doc_dict["a_str"]
    assert a_str_doc.name == "a_str"
    assert a_str_doc.type_hint == str.__name__
    assert a_str_doc.default == "FizzBuzz"
    assert len(a_str_doc.validators) == 1
    validator = a_str_doc.validators[0]
    assert validator.parameters == ["Fizz", "Buzz", "FizzBuzz"]
    assert validator.parameters == ["Fizz", "Buzz", "FizzBuzz"]
    # checking type
    assert validator.type == "Choices"
    # checking description
    assert "Fizz" in validator.description
    assert "Buzz" in validator.description
    assert "FizzBuzz" in validator.description


def test_documented_config() -> None:
    """
    Checks that `build_documentation` generates the correct doc
    for each parameter
    """
    _check_doc(DocumentedSection.build_documentation())


def test_documented_config_with_sections() -> None:
    """
    Checks that `build_documentation` works with nested sections
    """
    doc = DocumentedConfig.build_documentation()
    section_doc = doc["section_one"]
    assert section_doc.name == "section_one"
    assert section_doc.docstrings == ["A documented section"]

    meta_export = DocumentedConfig().meta_export()
    section_meta_export = meta_export["section_one"]
    section_doc = section_meta_export["doc"]
    assert section_doc["name"] == "section_one"
    assert section_doc["docstrings"] == ["A documented section"]


def test_meta_export() -> None:
    """
    Checks that when suing meta-export, every parameter gets exported
    with both its current value and documentation dictionary
    """
    config = DocumentedConfig()
    metadict = config.meta_export()

    section = metadict["section_one"]["value"]
    option = section["an_int"]
    assert option["value"] == 42
    assert option["scope"] == DefaultScopes.BASIC

    for key in ("validators", "docstrings", "type_hint"):
        assert key in option["doc"]


def test_dict_export() -> None:
    """
    Checks that when suing meta-export, every parameter gets exported
    with both its current value and documentation dictionary
    """
    config = DocumentedConfig()
    metadict = config.dict_export()

    section = metadict["section_one"]
    assert section["an_int"] == 42
    assert section["a_str"] == "FizzBuzz"


def test_update_config() -> None:
    """
    Checks that updating the config from nested dictionary works properly.
    Tries with values having alredy the correct type (cast_first=False),
    and with values in stirng form (cast_first=True)
    """
    config = ConfigWithSections()
    update_dict = {"section_one": {"an_int": 0}, "section_two": {"a_float": 1.0}}
    config.update(update_dict, cast_first=False)
    assert config.section_one.an_int == 0
    assert config.section_two.a_float == 1.0
    assert config.section_two.a_str == "FizzBuzz"

    config = ConfigWithSections()
    config.update(json.loads(json.dumps(update_dict)), cast_first=True)
    assert config.section_one.an_int == 0
    assert config.section_two.a_float == 1.0
    assert config.section_two.a_str == "FizzBuzz"


@dataclass
class AliasedSection(ConfigValidator):
    a_str: Annotated[
        str,
        "A quirky string",
        Choices("Fizz", "Buzz", "FizzBuzz"),
        Alias("A-STR"),
    ] = "Fizz"


@dataclass
class AliasedConfig(ConfigValidator):
    section_one: Annotated[SectionOneNested, Alias("Section_One")] = field(
        default_factory=SectionOneNested,
    )
    section_two: Annotated[AliasedSection, Alias("Section_Two")] = field(
        default_factory=AliasedSection,
    )


def test_aliased_config() -> None:
    """
    Checks that aliases are applied properly where expected when
    using a custom `toggle_alias`
    """
    config = AliasedConfig()
    # testing on meta export as it's using aliased names
    metadata = config.meta_export()
    assert "Section_One" in metadata
    assert "subsection_one" in metadata["Section_One"]["value"]

    assert "Section_Two" in metadata
    assert "A-STR" in metadata["Section_Two"]["value"]


@dataclass
class SectionWithHiddenOptions(ConfigValidator):
    an_int: Annotated[int, "Some completely arbitrary integer", Range(0, 100)] = 42
    a_str: Annotated[
        str,
        "A quirky string",
        DefaultScopes.HIDDEN,
        Choices("Fizz", "Buzz", "FizzBuzz"),
    ] = "FizzBuzz"


@dataclass
class SectionWithAdvancedOptions(ConfigValidator):
    an_int: Annotated[int, "Some completely arbitrary integer", Range(0, 100)] = 42
    a_str: Annotated[
        str,
        "A quirky string",
        DefaultScopes.ADVANCED,
        Choices("Fizz", "Buzz", "FizzBuzz"),
    ] = "FizzBuzz"


@dataclass
class SectionWithNestedHiddenOptions(ConfigValidator):
    a_subsection: SectionWithHiddenOptions = field(default_factory=SectionWithHiddenOptions)


@dataclass
class ConfigWithHiddenOptions(ConfigValidator):
    section_one: SectionWithHiddenOptions = field(default_factory=SectionWithHiddenOptions)


@dataclass
class ConfigWithAdvancedOptions(ConfigValidator):
    section_one: SectionWithAdvancedOptions = field(default_factory=SectionWithAdvancedOptions)


@dataclass
class ConfigWithAdvancedSection(ConfigValidator):
    section_one: Annotated[SectionWithAdvancedOptions, DefaultScopes.ADVANCED] = field(
        default_factory=SectionWithAdvancedOptions,
    )


@dataclass
class ConfigWithNestedHiddenOptions(ConfigValidator):
    section_one: SectionWithNestedHiddenOptions = field(
        default_factory=SectionWithNestedHiddenOptions,
    )


def test_hidden_parameters_are_not_exported() -> None:
    """
    Checks that hidden parameters are not included in the exports
    """
    rich_export = ConfigWithHiddenOptions().meta_export()
    assert "section_one" in rich_export
    assert "an_int" in rich_export["section_one"]["value"]
    assert "a_str" not in rich_export["section_one"]["value"]


def test_advanced_parameters_are_not_exported() -> None:
    """
    Checks that advanced parameters are not included in the exports
    """
    rich_export = ConfigWithHiddenOptions().meta_export()
    assert "section_one" in rich_export
    assert "an_int" in rich_export["section_one"]["value"]
    assert "a_str" not in rich_export["section_one"]["value"]


def test_scope_ordering() -> None:
    """
    Verifies the ordering of scopes
    """

    assert DefaultScopes.ADVANCED > DefaultScopes.BASIC
    assert DefaultScopes.HIDDEN > DefaultScopes.BASIC
    assert DefaultScopes.BASIC >= DefaultScopes.BASIC
    assert DefaultScopes.BASIC < DefaultScopes.ADVANCED
    assert DefaultScopes.HIDDEN > DefaultScopes.ADVANCED


@pytest.mark.parametrize("scope", DefaultScopes)
def test_scope_handling_dict_export(scope: DefaultScopes) -> None:
    """
    Checks that advanced parameters are not included in the exports
    """
    dct_export = ConfigWithAdvancedOptions().dict_export(scope=scope)
    assert "section_one" in dct_export
    assert "an_int" in dct_export["section_one"]
    if scope == DefaultScopes.BASIC:
        assert "a_str" not in dct_export["section_one"]
    else:
        assert "a_str" in dct_export["section_one"]


@pytest.mark.parametrize("scope", DefaultScopes)
def test_scope_handling_meta_export(scope: DefaultScopes) -> None:
    """
    Checks that advanced parameters are not included in the exports
    """
    dct_export = ConfigWithAdvancedOptions().meta_export(scope=scope)
    assert "section_one" in dct_export
    assert "an_int" in dct_export["section_one"]["value"]
    if scope == DefaultScopes.BASIC:
        assert "a_str" not in dct_export["section_one"]["value"]
    else:
        assert "a_str" in dct_export["section_one"]["value"]
        assert dct_export["section_one"]["value"]["a_str"]["scope"] == DefaultScopes.ADVANCED


def test_scope_is_propagated_to_nested_sections() -> None:
    """
    Checks that the scope is propagated to nested sections
    """
    dct_export = ConfigWithAdvancedSection().meta_export(DefaultScopes.ADVANCED)
    assert "section_one" in dct_export
    assert "an_int" in dct_export["section_one"]["value"]
    assert dct_export["section_one"]["value"]["an_int"]["scope"] == DefaultScopes.ADVANCED


def test_hidden_parameters_are_not_exported_recursive() -> None:
    """
    Checks that hidden parameters are not included in the exports
    """
    rich_export = ConfigWithNestedHiddenOptions().meta_export()
    assert "section_one" in rich_export
    assert "an_int" in rich_export["section_one"]["value"]["a_subsection"]["value"]
    assert "a_str" not in rich_export["section_one"]["value"]["a_subsection"]["value"]


@dataclass
class SectionWithDepends(ConfigValidator):
    an_int: int = 0
    a_flag: bool = False
    only_if_flag: Annotated[bool, Depends("a_flag")] = True
    only_if_42: Annotated[bool, Depends(an_int=42)] = True


@pytest.mark.parametrize("apply_deps", [True, False])
def test_dependency_handling(*, apply_deps: bool) -> None:
    """
    Checks that Depends annotations are handled properly.
    """

    scopes = [s for s in DefaultScopes if s != DefaultScopes.HIDDEN]
    section = SectionWithDepends()

    for scope in scopes:
        export = section.dict_export(scope=scope, apply_dependencies=apply_deps)
        if apply_deps:
            assert "only_if_42" not in export
            assert "only_if_flag" not in export
        else:
            assert "only_if_42" in export
            assert "only_if_flag" in export

    # fulfilling one requirement
    section.a_flag = True
    for scope in scopes:
        export = section.dict_export(scope=scope, apply_dependencies=apply_deps)
        assert "only_if_flag" in export
        if apply_deps:
            assert "only_if_42" not in export
        else:
            assert "only_if_42" in export

    # fulfilling both
    section.an_int = 42
    for scope in scopes:
        export = section.dict_export(scope=scope)
        assert "only_if_42" in export
        assert "only_if_flag" in export
