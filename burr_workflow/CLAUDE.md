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
workflow validate workflow.yaml
workflow validate workflow.yaml --strict  # treat warnings as errors
workflow validate workflow.yaml -q        # quiet mode, exit code only

# Analyze execution flow (static analysis)
workflow analyze workflow.yaml
workflow analyze workflow.yaml --inputs '{"target": "example.com"}'

# Export JSON Schema for IDE validation
workflow schema --pretty -o workflow-schema.json

# Create new workflow from template
workflow create my-workflow
workflow create --list-templates

# Pre-download LLM Guard models for guardrails
workflow guard-init                    # All common models
workflow guard-init -s prompt_injection  # Specific scanner

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

The package is designed for standalone use OR integration with llm-assistant. Five protocols in `protocols.py` define integration points:

| Protocol | Purpose |
|----------|---------|
| `ExecutionContext` | Shell execution, user prompts, logging |
| `OutputHandler` | Progress display, step start/end callbacks |
| `LLMClient` | AI completions (extract, decide, generate) |
| `ActionProvider` | Custom action registration |
| `ReportBackend` | Finding storage (pentest integration) |
| `AuditLogger` | Execution audit trail (FileAuditLogger) |

**Note**: State persistence uses Burr's built-in `SQLitePersister` - see "Burr Integration Notes" below.

### Action System

Actions in `actions/` implement `BaseAction` protocol with `execute()` async method returning `ActionResult`:

- **Shell**: `run:` command execution with array syntax for safety
- **HTTP**: `uses: http/request` with httpx
- **LLM**: `uses: llm/extract|decide|generate|instruct` via `LLMClient` protocol (generate supports format parameter: prose/bullets/numbered/json)
- **Human**: `uses: human/input` (free-form: text/multiline/file/editor) or `human/decide` (constrained: confirm/choices)
- **Script**: `uses: script/python|bash` for subprocess script execution with optional `sandbox: true` (bwrap isolation)
- **State**: `uses: state/set` for variable manipulation
- **Control**: `uses: control/exit|fail|wait` for flow control (wait supports duration or polling until condition)
- **File**: `uses: file/read|write` for file I/O (read supports text/binary/auto, write supports create/overwrite/append)
- **Parse**: `uses: parse/json|regex` for structured data extraction (json uses jmespath, regex supports named groups)
- **Notify**: `uses: notify/desktop|webhook` for notifications (desktop auto-detects notify-send/terminal-notifier)
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
- **Whitelisted filters**: `shell_quote`, `safe_path`, `json_parse`, `first`, `last`, `join`, `is_valid_ip`, `is_private_ip`, `in_cidr`, `file_exists`, `in_list`, etc.
- **Dangerous pattern detection**: Blocks `__class__`, `eval`, `import`, `subprocess`, etc.
- **ChainableUndefined**: Graceful handling of missing keys (`${{ steps.missing.outputs }}` → null)

#### GitHub Actions Compatible Functions

The following GHA-style functions are available as filters:

| Function | Usage | Description |
|----------|-------|-------------|
| `contains` | `${{ value \| contains('needle') }}` | Check if string/array contains value |
| `startsWith` | `${{ value \| startsWith('prefix') }}` | Check if string starts with prefix |
| `endsWith` | `${{ value \| endsWith('suffix') }}` | Check if string ends with suffix |
| `format` | `${{ '{0}:{1}' \| format(host, port) }}` | String formatting with positional args |
| `toJSON` | `${{ value \| toJSON }}` | Serialize to JSON string |
| `fromJSON` | `${{ json_str \| fromJSON }}` | Parse JSON string |

#### Network and Validation Filters

| Filter | Usage | Description |
|--------|-------|-------------|
| `is_valid_ip` | `${{ host \| is_valid_ip }}` | Check if string is valid IPv4/IPv6 address |
| `is_private_ip` | `${{ host \| is_private_ip }}` | Check if IP is RFC1918/RFC4193 private |
| `in_cidr` | `${{ host \| in_cidr('10.0.0.0/8') }}` | Check if IP is in CIDR range |
| `file_exists` | `${{ path \| file_exists }}` | Check if file exists on disk |
| `in_list` | `${{ value \| in_list(allowed) }}` | Check if value is in list |

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

### Guardrails (LLM Guard Integration)

The workflow engine supports input/output validation using [llm-guard](https://llm-guard.com/). Guardrails can be defined at the workflow level (applied to all steps) or overridden per step.

#### Installation

```bash
pip install burr_workflow[guard]
# or
uv pip install burr_workflow[guard]
```

#### Workflow-Level Defaults

```yaml
name: secure-workflow
version: "1.0"

# Applied to ALL steps by default
guardrails:
  input:
    prompt_injection: { threshold: 0.92 }
    secrets: { redact: true }
  output:
    sensitive: { redact: true }
  on_fail: abort  # abort | retry | continue | step_id
  max_retries: 2

jobs:
  main:
    steps:
      # Inherits workflow guardrails automatically
      - id: analyze
        uses: llm/generate
        with:
          prompt: ${{ inputs.query }}
```

#### Step-Level Overrides

```yaml
steps:
  # Override: adds anonymize, keeps other workflow defaults
  - id: pii_sensitive
    uses: llm/generate
    with:
      prompt: ${{ inputs.user_data }}
    guardrails:
      input:
        anonymize:
          entities: [PERSON, EMAIL, PHONE]
      output:
        deanonymize: {}

  # Override: disable ALL guardrails for this step
  - id: trusted_internal
    run: internal_tool.sh
    guardrails: false
```

#### Merge Behavior

| Step guardrails | Behavior |
|-----------------|----------|
| Not specified | Inherit workflow defaults |
| `guardrails: {...}` | Merge with workflow (step scanners ADD to defaults) |
| `guardrails: false` | Disable all guardrails for this step |
| Same scanner in both | Step config overrides workflow config for that scanner |

#### Supported Scanners

**Input Scanners (12)**:

| Scanner | Purpose |
|---------|---------|
| `anonymize` | Replace PII with placeholders (uses Vault) |
| `prompt_injection` | Detect jailbreak attempts (DeBERTa) |
| `secrets` | Detect API keys, passwords, tokens |
| `invisible_text` | Strip zero-width/invisible chars |
| `token_limit` | Prevent context overflow |
| `ban_topics` | Block specific subjects |
| `ban_substrings` | Block specific text patterns |
| `ban_code` | Block prompts containing code |
| `code` | Detect/allow specific languages |
| `gibberish` | Detect nonsense input |
| `language` | Restrict to specific languages |
| `regex` | Custom regex validation |

**Output Scanners (17)**:

| Scanner | Purpose |
|---------|---------|
| `deanonymize` | Restore anonymized entities (uses Vault) |
| `sensitive` | Detect/redact sensitive data |
| `no_refusal` | Detect model refusals |
| `factual_consistency` | Detect hallucinations (NLI model) |
| `relevance` | Check output relevance to prompt |
| `json` | Validate JSON structure |
| `malicious_urls` | Detect dangerous URLs |
| `url_reachability` | Verify URLs are accessible |
| `language_same` | Ensure same language as input |
| `language` | Restrict output language |
| `reading_time` | Limit response length |
| `gibberish` | Detect nonsense output |
| `ban_topics` | Block specific subjects |
| `ban_substrings` | Block specific text |
| `ban_code` | Block responses with code |
| `code` | Detect/allow code languages |
| `regex` | Custom regex validation |

#### Vault Pattern (Anonymize → Deanonymize)

The `anonymize` and `deanonymize` scanners work together across workflow steps:

```yaml
guardrails:
  input:
    anonymize:
      entities: [PERSON, EMAIL, PHONE, CREDIT_CARD]
  output:
    deanonymize: {}

steps:
  - id: process_pii
    uses: llm/generate
    with:
      prompt: "Summarize this: ${{ inputs.user_data }}"
    # Input: "Contact John at john@example.com"
    # → Anonymized: "Contact [PERSON_1] at [EMAIL_1]"
    # → LLM processes anonymized text
    # → Output restored: "Summary mentions John and john@example.com"
```

The vault state is persisted in `__guard_vault` and survives workflow suspension/resume.

#### Pre-downloading ML Models

Some scanners use ML models that are downloaded on first use. Pre-download them for offline use:

```bash
# Download all common models (~1.5GB)
workflow guard-init

# Download specific scanners only
workflow guard-init -s prompt_injection -s anonymize

# Quiet mode
workflow guard-init -q
```

**Model-using scanners**:
- `prompt_injection` - DeBERTa v2 (~500MB)
- `anonymize`/`sensitive` - Presidio + spaCy (~200MB)
- `factual_consistency` - NLI model (~400MB)

#### Graceful Degradation

When llm-guard is not installed:
- Guardrails configuration is accepted and validated
- At runtime, guardrails are skipped with a warning
- Workflow execution continues normally

```python
# Check if guard is available
from burr_workflow.guard import LLM_GUARD_AVAILABLE
if not LLM_GUARD_AVAILABLE:
    print("llm-guard not installed, guardrails disabled")
```

#### Step Type Support

Guardrails work on all step types:
- **Shell** (`run:`): Scans the command string
- **HTTP** (`http/request`): Scans URL + body/json
- **LLM** (`llm/*`): Scans prompt field
- **Script** (`script/*`): Scans script content
- **Outputs**: Scans stdout, response text, or JSON output

### Burr Integration Notes

The compiler creates Burr `SingleStepAction` nodes via `BurrActionAdapter`:

- Burr State is immutable - use `state.update(**kwargs)`
- Transitions are explicit: `(from_node, to_node)` or `(from_node, to_node, Condition)`
- Loop support via iterator nodes (`IteratorInitNode`, `IteratorCheckNode`, etc.)
- Finally blocks via `CleanupAction` that runs on both success and failure

#### Persistence & Tracking

Burr's built-in persistence and tracking are available via `compile()` parameters:

- **db_path**: SQLite database for state checkpointing (enables resume after interruption)
- **enable_tracking**: Write execution data to `~/.burr/` for Burr web UI

```python
from pathlib import Path
from burr_workflow import WorkflowCompiler, WorkflowExecutor

compiler = WorkflowCompiler()
app = compiler.compile(
    workflow_dict,
    db_path=Path("./workflow.db"),
    enable_tracking=True,
    tracking_project="my-project",
)

executor = WorkflowExecutor()
result = await executor.run(app, inputs={"target": "example.com"})
```

View execution in Burr UI: `burr --open` (opens http://localhost:7241)

#### Lifecycle Hooks

The executor uses Burr's lifecycle hooks for accurate step timing via `StepTimingHook`:

```python
from burr_workflow import StepTimingHook, WorkflowExecutor

# Executor creates hook automatically when capture_timing=True (default)
executor = WorkflowExecutor(capture_timing=True)
result = await executor.run(app, inputs={...})

# Access timing data via the hook
hook = executor.timing_hook
for step_id, timing in hook.timings.items():
    print(f"{step_id}: {timing.duration_ms:.2f}ms")
```

**Why hooks?**: Burr's `aiterate()` yields AFTER action execution completes, making timing calculations inaccurate. The `StepTimingHook` captures precise pre/post timestamps using `PreRunStepHookAsync` and `PostRunStepHookAsync`.

#### Nested Event Loop Warning

When running workflows in an existing async context (e.g., Jupyter, async web frameworks), burr_workflow uses `nest_asyncio.apply()` which patches **global** asyncio state. This may conflict with:
- Tornado-based applications
- Twisted-based applications
- Other async frameworks that manage their own event loops

If you experience async-related issues, consider running workflows in a separate process or subprocess.

#### Graph Visualization

Generate workflow diagrams via CLI with dual engine support:

```bash
# Mermaid to stdout (default, no dependencies)
workflow validate workflow.yaml --visualize
workflow validate workflow.yaml -v

# Mermaid to file (embeddable in README)
workflow validate workflow.yaml -v workflow.md

# Graphviz PNG (requires graphviz)
workflow validate workflow.yaml -v diagram.png --engine graphviz

# SVG with transition conditions
workflow validate workflow.yaml -v diagram.svg -e graphviz --show-conditions
```

**Engines**:
- **Mermaid** (default): Text-based flowcharts, embeds in markdown, no dependencies
- **Graphviz**: High-quality images, requires `pip install burr_workflow[viz]` and `apt install graphviz`

**Programmatic usage**:
```python
from burr_workflow import WorkflowCompiler, visualize, to_mermaid

compiler = WorkflowCompiler()
app = compiler.compile(workflow_dict)

# Generate mermaid syntax
mermaid_text = to_mermaid(app, include_conditions=True)

# Generate visualization to file
visualize(app, output_path=Path("diagram.png"), engine="graphviz")
```

## Key Files

| File | Purpose |
|------|---------|
| `core/compiler.py` | YAML→Burr graph compilation, runs validator before compile |
| `core/executor.py` | `WorkflowExecutor`, suspension/resume, progress tracking |
| `core/validator.py` | 7-phase validation, error codes E000-E015, warning codes W001-W008 |
| `core/hooks.py` | `StepTimingHook` for accurate step timing via Burr lifecycle hooks |
| `core/visualize.py` | `visualize()`, `to_mermaid()` for workflow graph rendering |
| `evaluator/context.py` | `ContextEvaluator`, secure expression evaluation |
| `evaluator/security.py` | `PathValidator`, path traversal prevention |
| `schemas/models.py` | Pydantic v2 models (`WorkflowDefinition`, `StepDefinition`) |
| `protocols.py` | Integration protocols for loose coupling |
| `actions/registry.py` | Action type→class mapping, `get_default_registry()` |
| `cli.py` | Unified `workflow` CLI with subcommands (validate, analyze, schema, create, guard-init) |
| `guard/` | LLM Guard integration package (scanner.py, vault.py) |
| `templates/` | Workflow scaffolding templates |

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
- `E009`: Invalid expression syntax (Jinja2 parse errors)
- `E010`: Dangerous expression pattern
- `E015`: Unknown Jinja2 filter
- `W001-W008`: Warnings (unused steps, missing IDs, shell injection, etc.)

**Note**: The compiler automatically runs the full validator before compilation, so expression syntax errors and unknown filters are caught before execution starts.

## Schema Updates

When modifying `schemas/models.py` (Pydantic models), regenerate the JSON Schema for IDE validation:

```bash
workflow schema --pretty -o skills/workflow-creator/assets/workflow-schema.json
```

This ensures VS Code autocomplete and validation stay in sync with the actual schema.
