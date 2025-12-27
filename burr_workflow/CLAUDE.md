# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`burr_workflow` is a YAML-based workflow engine built on [Burr](https://github.com/dagworks-inc/burr) (~11K lines of Python). It compiles YAML workflow definitions into executable Burr Applications with state machines, persistence, and human-in-the-loop support.

## Development Commands

```bash
# Install in development mode
cd burr_workflow
uv pip install -e ".[dev,test]"

# Validate a workflow YAML file
workflow-validate workflow.yaml
workflow-validate workflow.yaml --strict  # treat warnings as errors
workflow-validate workflow.yaml -q        # quiet mode, exit code only

# Export JSON Schema for IDE validation
workflow-schema --pretty -o workflow-schema.json

# Run tests
pytest tests/
pytest tests/test_validator.py -v         # single test file
pytest -k "test_shell"                    # run tests matching pattern

# Type checking and linting
mypy burr_workflow/
ruff check burr_workflow/
ruff format burr_workflow/

# Syntax check
python3 -m py_compile burr_workflow/core/validator.py
```

## Architecture

### Execution Pipeline

```
YAML → Parser → Validator → Compiler → Burr Application → Executor → Result
```

1. **Parser** (`core/parser.py`): Uses ruamel.yaml with source location tracking for error messages
2. **Validator** (`core/validator.py`): Static analysis with 7 validation phases (structure, step IDs, references, expressions, shell safety, unused steps)
3. **Compiler** (`core/compiler.py`): Transforms YAML into Burr graph with explicit transitions
4. **Executor** (`core/executor.py`): High-level execution with suspension/resume, progress tracking, Ctrl+C handling

### Protocol-Based Integration

The package is designed for standalone use OR integration with llm-assistant. Six protocols in `protocols.py` define integration points:

| Protocol | Purpose |
|----------|---------|
| `ExecutionContext` | Shell execution, user prompts, logging |
| `OutputHandler` | Progress display, step start/end callbacks |
| `LLMClient` | AI completions (extract, decide, generate) |
| `PersistenceBackend` | State save/restore for resume |
| `ActionProvider` | Custom action registration |
| `ReportBackend` | Finding storage (pentest integration) |

### Action System

Actions in `actions/` implement `BaseAction` protocol with `execute()` async method returning `ActionResult`:

- **Shell**: `run:` command execution with array syntax for safety
- **HTTP**: `uses: http/request` with httpx
- **LLM**: `uses: llm/extract|decide|generate|analyze|instruct` via `LLMClient` protocol
- **Human**: `uses: human/input` with suspension mechanism
- **Script**: `uses: script/python|bash` for subprocess script execution
- **State**: `uses: state/set` for variable manipulation
- **Control**: `uses: control/exit|fail` for flow control
- **Report**: `uses: report/add` for pentest findings via `ReportBackend`

Actions are registered in `actions/registry.py`. Use `register_llm_actions()` or `register_report_actions()` to inject dependencies.

### Skill Documentation

Complete workflow creation documentation is available in `skills/workflow-creator/`:

- `SKILL.md` - Quick reference for workflow syntax, expressions, filters
- `references/actions.md` - Complete action reference with parameters and outputs
- `references/examples.md` - Full workflow examples (OSINT, port scanning, credential testing, etc.)

### Expression Evaluation

`evaluator/context.py` provides secure Jinja2 evaluation with `${{ expr }}` syntax:

- **SecureNativeEnvironment**: Sandbox + NativeEnvironment (preserves Python types)
- **Whitelisted filters**: `shell_quote`, `safe_path`, `json_parse`, `first`, `last`, `join`, etc.
- **Dangerous pattern detection**: Blocks `__class__`, `eval`, `import`, `subprocess`, etc.
- **ChainableUndefined**: Graceful handling of missing keys (`${{ steps.missing.outputs }}` → null)

### Shell Safety

The validator includes shell injection detection (`W_SHELL_INJECTION` warning):

```yaml
# WARNING: Unquoted variable
- run: echo ${{ inputs.user_data }}

# SAFE: Using shell_quote filter
- run: echo ${{ inputs.user_data | shell_quote }}

# SAFE: Array syntax (arguments passed directly, not through shell)
- run: ["echo", "${{ inputs.user_data }}"]
```

### Burr Integration Notes

The compiler creates Burr `SingleStepAction` nodes via `BurrActionAdapter`:

- Burr State is immutable - use `state.update(**kwargs)`
- Transitions are explicit: `(from_node, to_node)` or `(from_node, to_node, Condition)`
- Loop support via iterator nodes (`IteratorInitNode`, `IteratorCheckNode`, etc.)
- Finally blocks via `CleanupAction` that runs on both success and failure

## Key Files

| File | Purpose |
|------|---------|
| `core/compiler.py` | YAML→Burr graph compilation, `CompiledStep` dataclass |
| `core/executor.py` | `WorkflowExecutor`, suspension/resume, progress tracking |
| `core/validator.py` | 7-phase validation, error codes E000-E014, warning codes W001-W008 |
| `evaluator/context.py` | `ContextEvaluator`, secure expression evaluation |
| `evaluator/security.py` | `PathValidator`, path traversal prevention |
| `schemas/models.py` | Pydantic v2 models (`WorkflowDefinition`, `StepDefinition`) |
| `protocols.py` | Integration protocols for loose coupling |
| `actions/registry.py` | Action type→class mapping, `get_default_registry()` |
| `cli.py` | `workflow-validate` and `workflow-schema` CLI entrypoints |

## Workflow YAML Structure

```yaml
name: example-workflow
version: "1.0.0"
schema_version: "1.0"

inputs:
  target:
    type: string
    required: true

jobs:
  main:
    steps:
      - id: scan
        run: nmap -sV ${{ inputs.target | shell_quote }}
        timeout: 300

      - id: analyze
        uses: llm/extract
        with:
          input: ${{ steps.scan.outputs.stdout }}
          schema:
            type: object
            properties:
              open_ports: { type: array }

      - id: report
        uses: report/add
        with:
          note: "Found ${{ steps.analyze.outputs.open_ports | length }} open ports"

finally:
  - run: echo "Workflow complete"
```

## Error Handling

Error codes follow consistent patterns:
- `E0xx`: Structure errors (missing fields, invalid types)
- `E007`: Duplicate step ID
- `E008`: Invalid reference
- `E009`: Invalid expression syntax
- `E010`: Dangerous expression pattern
- `W001-W008`: Warnings (unused steps, missing IDs, shell injection, etc.)

## Schema Updates

When modifying `schemas/models.py` (Pydantic models), regenerate the JSON Schema for IDE validation:

```bash
workflow-schema --pretty -o skills/workflow-creator/assets/workflow-schema.json
```

This ensures VS Code autocomplete and validation stay in sync with the actual schema.
