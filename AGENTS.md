# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project Overview

Pilgrim builds PyMeasure-style drivers for various instruments and uses them in custom test procedures to automate testing workflows.
Communication requires NI-VISA installed on the host; PyVISA uses it as the backend.

## Project Stack

- Language: Python 3.14+ (pinned in `.python-version`)
- Build system: `uv_build`
- Package manager: uv (lockfile in `uv.lock`)
- Layout: src layout, the package lives in `src/pilgrim/`
- Dependencies: pymeasure (which brings in pyvisa, pint, numpy, pandas, etc.)
- Testing: pytest with pytest-cov, pyvisa-sim (tests live in `tests/`)
- Tooling: ruff (lint and format), pyright (type check)

## Build/Lint/Test Commands

- Install with all extras into `.venv`: `uv sync --all-extras`
- Add a dependency: `uv add <package>` (or `uv add --optional dev <package>` / `uv add --optional tests <package>`); do not edit `uv.lock` by hand
- Run the CLI entry point: `uv run pilgrim`
- Run tests: `uv run pytest`
- Run tests with coverage: `uv run pytest --cov=pilgrim`
- Run single test: `uv run pytest tests/test_file.py::test_function`
- Lint with ruff: `uv run ruff check` (fix what is safe with `uv run ruff check --fix`)
- Format with ruff: `uv run ruff format`
- Type check with pyright: `uv run pyright`
- Before finishing a change, `ruff check`, `ruff format --check`, `pyright`, and `pytest` must all pass

## Code Style Guidelines

- Line length: 100 characters (ruff)
- The enabled ruff rule sets are listed in `pyproject.toml`; fix violations rather than adding `# noqa`, and if a suppression is truly needed, name the specific rule code
- Imports are sorted by ruff (`I` rules); there is no separate isort step
- Use `pathlib` instead of `os.path` (`PTH` rules)
- Physical units are handled with the Pint library
- Comments are only used to describe non-obvious reasons (why)
- Follow PEP8 style guide and PEP257 docstring conventions
- Function and variable names should be lower case with underscores as needed to separate words
- CamelCase should only be used for class names, unless working with Qt, where its use is common
- Add type hints to public functions and methods, adhering to the guidelines set out in the typing package
- Changes must keep `pyright` (standard mode) green; do not silence type errors by removing annotations or casting unsoundly — fix the underlying types or add precise annotations instead
- Use built-in generics and modern syntax (`list[int]`, `dict[str, float]`, `X | None`); ruff's `UP` rules enforce this
- Annotations are evaluated lazily on Python 3.14, so do not add `from __future__ import annotations`
- In Markdown files, start each sentence on a new line (semantic line breaks) so diffs show which sentence is affected; this applies to prose, not code blocks or tables

### Instrument drivers

- Use PyMeasure's property creators (control, measurement, setting) for instrument parameters
- Getter and setter functions are discouraged, since properties provide a more fluid experience
- Given the extensive tools available for defining properties, these types of properties are preferred
- Instrument classes inherit from `pymeasure.instruments.Instrument`
- Adapters handle communication (VISA, Serial, Prologix, etc.)
- Use `ProtocolAdapter` with `expected_protocol` for testing instrument communication
- Before writing or changing a driver, look up the commands in the programmer manuals under `docs/manuals/<manufacturer>/`; the PDFs are large, so read only the relevant pages
- The manuals are gitignored and exist only locally; if the one you need is missing, ask the user for it instead of guessing commands
- Do not invent SCPI commands; if the manual does not confirm a command or its response format, say so instead of guessing
- Check whether PyMeasure already ships a driver for the instrument before writing a new one; prefer subclassing or extending it

#### File Organization

- Place instruments in `src/pilgrim/instruments/<manufacturer>/`
- Use lowercase for all filenames to distinguish packages from CamelCase Python classes
- Update the manufacturer's `__init__.py` file to import instruments
- Add test files in the corresponding `tests/instruments/<manufacturer>/` directory
- Put instrument manuals in `docs/manuals/<manufacturer>/`

#### Property Creator Details

- Use validators (`strict_range`, `strict_discrete_set`, etc.) to restrict property values; prefer strict validators by default, as truncated validators silently clip out-of-range values without feedback (only use a truncated validator when silent clipping is genuinely desired and documented in the property docstring)
- Use the `map_values` parameter when instrument commands require non-physical values
- Implement boolean properties using maps with True/False values
- Use `set_process` and `get_process` functions for value transformations (e.g., unit conversions)
- Use `preprocess_reply` for string processing of device responses
- Enable `check_get_errors`/`check_set_errors` for instruments that report errors

#### Channel Implementation

- For instruments with fewer than 16 channels, use `Instrument.ChannelCreator` for explicit channel declaration
- For instruments with more than 16 channels, use `Instrument.MultiChannelCreator`
- Channels should inherit from the `Channel` class and use Channel-specific property creators
- Use the `{ch}` placeholder in command strings for automatic channel ID insertion
- Override the `insert_id` method for channels with fixed command prefixes

#### Testing Instruments

- Write protocol tests using the `expected_protocol` context manager for communication verification
- Create both protocol tests (without hardware) and device tests (with hardware)
- Use pytest fixtures for device tests to enable easy configuration and skipping
- Name device test files with a `_with_device` suffix to distinguish them from protocol tests
- `uv run pytest` must pass without any hardware attached

### Measurement and GUI

- Procedures inherit from `pymeasure.experiment.Procedure`

## Documentation Standards

- All functions, classes, and (public) methods should be documented in the code using a docstring
- Private methods may omit a docstring when their name and signature (including type hints) make their behavior self-evident
- Test methods (`test_*`) do not need a docstring; their name and body make their intent clear
- Descriptive and specific docstrings are important for users to quickly understand properties and methods
- Use triple-quoted strings (`"""`) to delimit docstrings
- Start with one short summary line in imperative voice, with a period at the end
- Optionally, after a blank line, include more detailed information
- Do not add a docstring to `__init__` methods; document the class instead (per PEP257, the class docstring covers constructor behavior, including parameters)
- Property setters do not need a docstring; only the getter's docstring is exposed as `__doc__` on the property
- For functions and methods, document parameters using the reStructuredText field list format (`:param name:`), matching PyMeasure
- For properties, start them with "Control", "Get", "Measure", or "Set" to indicate the kind of property
- Add type and information about validators (if applicable) at the end of the summary line
- Example property docstring: `"""Control the voltage in Volts (float strictly from -1 to 1)."""`
- Docstrings should only contain information relevant for using a property/method, not internal details
- Code examples in docstrings are formatted by `ruff format` (`docstring-code-format`), so keep them valid Python

## Core Architecture Patterns

These are PyMeasure patterns that Pilgrim builds on:

- Instruments use the Adapter pattern for communication abstraction
- Procedures define experimental workflows with startup/execute/shutdown
- Workers run procedures in separate threads
- Results are stored in CSV format with automatic unit parsing
- GUI uses Qt (via QtPy) with the Manager/Experiment pattern
- Parameters and Metadata system for procedure configuration

## Non-Obvious Conventions

- The CLI entry point is `pilgrim:main` in `src/pilgrim/__init__.py` (see `[project.scripts]` in `pyproject.toml`)
- Instrument `control()` method strips command formatting and uses format specifiers
- Dynamic properties allow runtime parameter changes using a naming convention
- Channel system uses the `ChannelCreator` class for hierarchical instrument interfaces
- `ProtocolAdapter` allows testing without hardware
- Validators raise `ValueError` for invalid inputs
- Instrument properties can have error checking (`check_set_errors`, `check_get_errors`)
- `DATA_COLUMNS` parsing automatically detects Pint units in parentheses
