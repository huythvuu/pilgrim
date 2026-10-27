# Pilgrim

Pilgrim is a collection of [PyMeasure](https://pymeasure.readthedocs.io/)-style instrument drivers and the custom test procedures built on them.
The goal is to automate lab testing workflows: drivers expose each instrument through Pythonic properties, and procedures combine several instruments into repeatable tests.

## Requirements

- [NI-VISA](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html) (required).
  PyVISA uses it as the backend to talk to instruments over USB, Ethernet (VXI-11/HiSLIP), GPIB, and serial.
- Python 3.14 or newer
- [uv](https://docs.astral.sh/uv/) for managing the environment and dependencies

## Installation

Install NI-VISA first, then from the repository root:

```sh
uv sync --all-extras
```

This creates `.venv/` with Pilgrim installed in editable mode, plus the `dev` (ruff, pyright) and `tests` (pytest, pyvisa-sim) extras.
Use `uv sync` without `--all-extras` if you only need to run procedures.

### Check the VISA setup

Confirm that PyVISA finds the NI-VISA library:

```sh
uv run pyvisa-info
```

Under `ivi`, the output should list `visa64.dll` (or `visa32.dll`) with `Vendor: National Instruments`.
With instruments connected, list the resources VISA can see:

```sh
uv run python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"
```

Use the resource strings it prints (for example `USB0::0x0699::...::INSTR` or `TCPIP0::192.168.1.10::inst0::INSTR`) to connect drivers to instruments.
If an instrument is missing, check it in NI MAX (NI Measurement & Automation Explorer) before debugging Python code.

## Project layout

```text
src/pilgrim/
    instruments/<manufacturer>/   Instrument drivers, one module per instrument family
    procedure/                    Test procedures and their parameter types
    gui/<procedure>/              GUI of a procedure: its window and, in components/, its widgets
tests/
    instruments/<manufacturer>/   Protocol tests (no hardware) and *_with_device tests
    procedure/                    Procedure tests against mock instruments
    gui/<procedure>/              GUI tests, run offscreen with pytest-qt
docs/manuals/<manufacturer>/      Programmer manuals (not committed, download locally)
```

## Running the ripple GUI

```sh
uv run pilgrim-ripple
```

The window edits the parameters of the ripple procedure, queues runs, and shows their results, captures and log.
The procedure pauses whenever the load must be set; the window then prompts for it and beeps, and Continue resumes the procedure once the load is set.
Abort ends a paused procedure as well.

## Development

| Task | Command |
| --- | --- |
| Run tests | `uv run pytest` |
| Run tests with coverage | `uv run pytest --cov=pilgrim` |
| Lint | `uv run ruff check` |
| Format | `uv run ruff format` |
| Type check | `uv run pyright` |

Protocol tests use PyMeasure's `expected_protocol` and run without hardware, so `uv run pytest` must pass on any machine.
Device tests (files ending in `_with_device`) need a connected instrument.

Driver conventions follow PyMeasure: use the `control`, `measurement`, and `setting` property creators with strict validators, and channel creators for multi-channel instruments.
See [AGENTS.md](AGENTS.md) for the full coding, testing, and docstring conventions.
