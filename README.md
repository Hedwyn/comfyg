# comfyg

A Python utility to define configuration easily from dataclasses, with support for rich type annotation, automatic type validation, casting from string, and per-option documentation.

Also provides import/export features from and to INI files.

## Features

- **Type validation**: Strict type validation on instantiation (no silent casting)
- **Rich annotations**: Support for `Annotated` types with validators and documentation
- **Automatic casting**: Convert string values to their target types from config files
- **INI file support**: Seamless import/export to/from INI configuration files
- **Recursive validation**: Nest ConfigValidator objects as much as needed
- **Scoped parameters**: Mark options with visibility levels (BASIC, ADVANCED, INTERNAL, HIDDEN)
- **Dependencies**: Express conditional visibility based on other parameter values
- **Zero dependencies**: Implements its own type validation and casting logic

## Quick Start

### Basic Configuration

```python
from dataclasses import dataclass
from comfyg import ConfigValidator

@dataclass
class MyConfig(ConfigValidator):
    my_option: int = 42
    name: str = "default"

config = MyConfig()
```

### Rich Type Annotations

Use `Annotated` to add documentation and validators:

```python
from typing import Annotated
from comfyg import ConfigValidator, Range, Choices

@dataclass
class ASection(ConfigValidator):
    an_int: Annotated[int, "This is the doc string", Range(0, 100)] = 42
    a_choice: Annotated[str, "Another doc string", Choices("Fizz", "Buzz", "FizzBuzz")] = "Fizz"
```

### Casting from Strings

Use `import_from_configparser` or `import_from_ini_file` to automatically cast string values:

```python
from comfyg import import_from_ini_file

config = import_from_ini_file(MyConfig, "config.ini")
```

### Validation

Validation runs automatically on instantiation via `__post_init__`. For batch changes, use a context manager:

```python
with config.check():
    config.section_one.an_int = 42
    config.section_one.a_float = 3.14
# Validation is performed on context manager exit
```

### Nested Sections

ConfigValidators can contain other ConfigValidators for multi-level organization:

```python
@dataclass
class NestedConfig(ConfigValidator):
    section_one: ASection = field(default_factory=ASection)
    section_two: AnotherSection = field(default_factory=AnotherSection)
```

## Scopes

Use the `Scope` annotation to define how parameters should be exposed:

```python
from comfyg import Scope

@dataclass
class Config(ConfigValidator):
    basic_param: Annotated[int, Scope.BASIC] = 1
    advanced_param: Annotated[int, Scope.ADVANCED] = 2
    internal_param: Annotated[int, Scope.INTERNAL] = 3
    hidden_param: Annotated[int, Scope.HIDDEN] = 4
```

- **BASIC**: Always shown to users
- **ADVANCED**: Advanced/developer level features
- **INTERNAL**: For internal use, exported to config files
- **HIDDEN**: Never exported, reserved for internal state

## Dependencies

Use the `Depends` annotation to make parameter visibility conditional:

```python
@dataclass
class Config(ConfigValidator):
    is_bidirectional: bool
    max_discharge_current: Annotated[float, Depends("is_bidirectional")] = 0
```

## Export & Import

### To INI File

```python
from comfyg import export_to_ini_file

export_to_ini_file(config, "config.ini")
```

### To Dictionary

```python
# With all parameters
config_dict = config.dict_export(scope=Scope.HIDDEN)

# Flattened (dot-separated keys)
flat_dict = config.flat_dict_export(scope=Scope.HIDDEN)
```

### With Documentation

Export configuration with metadata:

```python
meta = config.meta_export(scope=Scope.BASIC)
# Includes type hints, defaults, validators, and documentation
```

## ConfigLoader

For convenient config management with optional persistence:

```python
from comfyg import ConfigLoader

loader = ConfigLoader(MyConfig, config_path="config.ini", autosave=True)
config = loader.load()

# Make changes...
loader.save()
```

## Design Philosophy

This module implements its own type validation and casting logic without external dependencies. While similar libraries exist (like attrs and pydantic), this approach prioritizes:

- **Lightweight**: Only one dependency (exhausterr, to provide a railway-programming style API).
- **Standard**: Works with plain, stdlib Python dataclasses (`@dataclass`)  does not introduce a 3rd-party dataclass wrapper. 
- **Declarative**: Focus on type annotations as the source of truth
- **Explicit**: Validation and casting are two separated pipelines, which you can individually control. 
