---
name: workflow-creator
description: |
  Create YAML workflow definitions for the burr_workflow engine. Use when the user asks to:
  (1) Create a new workflow or automation script
  (2) Define multi-step procedures with loops and conditionals
  (3) Build security reconnaissance, pentest, or OSINT workflows
  (4) Create workflows that combine shell commands, HTTP requests, and LLM analysis
  (5) Design interactive workflows with human input prompts
  Triggers: "create workflow", "define workflow", "workflow for", "automate", "multi-step process"
---

# Workflow Creator

Create workflows using GitHub Actions-like YAML syntax. Workflows compile to Burr state machine graphs for execution with persistence, resume, and observability.

## Validation

Use the `workflow-validate` CLI to validate workflow YAML files:

```bash
workflow-validate workflow.yaml              # Validate with warnings
workflow-validate workflow.yaml --strict     # Treat warnings as errors
workflow-validate workflow.yaml -q           # Quiet mode, exit code only
workflow-validate workflow.yaml --dry-run    # Show execution flow without running
```

## IDE Schema Validation

For autocomplete and validation in VS Code/editors with YAML support, add this comment at the top of your workflow file:

```yaml
# yaml-language-server: $schema=./assets/workflow-schema.json
```

The current schema is available in `assets/workflow-schema.json`. To regenerate:

```bash
workflow-schema --pretty -o assets/workflow-schema.json
```

## Workflow Structure

```yaml
# yaml-language-server: $schema=./assets/workflow-schema.json
name: "workflow-name"
version: "1.0.0"  # Workflow version (semver recommended)
author: "your-name"  # Optional maintainer
schema_version: "1.0"
description: "What this workflow does"

inputs:
  target:
    description: "Target to scan"
    type: string
    required: true

env:
  SCAN_DIR: "./scans/${{ inputs.target }}"

jobs:
  main:
    steps:
      - name: "Step Name"
        id: step_id
        run: echo "Hello"
```

## Expression Syntax

Use `${{ ... }}` for variable interpolation:

| Expression | Description |
|------------|-------------|
| `${{ inputs.target }}` | Input value |
| `${{ env.VAR }}` | Environment variable |
| `${{ steps.prev.outputs.stdout }}` | Previous step output |
| `${{ steps.prev.outcome }}` | Step outcome (success/failure/skipped) |
| `${{ loop.item }}` | Current loop item |
| `${{ loop.index }}` | 1-based loop index |

## Input Types

```yaml
inputs:
  target:
    type: string
    required: true
    pattern: "^[a-z0-9.-]+$"
  port:
    type: integer
    default: 443
    min: 1
    max: 65535
  aggressive:
    type: boolean
    default: false
  wordlist:
    type: file
    required: true
```

## Conditionals

```yaml
- name: "Deep Scan"
  id: deep
  if: ${{ steps.recon.outputs.count > 100 }}
  run: nmap -sC -sV ${{ inputs.target | shell_quote }}
```

## Loops

```yaml
- name: "Scan Each"
  id: scans
  run: nmap -F ${{ loop.item | shell_quote }}
  loop: ${{ steps.filter.outputs.subdomains }}
  continue_on_error: true
  break_if: ${{ loop.output.found }}
```

**Loop variables:** `loop.item`, `loop.index` (1-based), `loop.index0` (0-based), `loop.first`, `loop.last`, `loop.total`, `loop.output`

**After loop:** `steps.scans.outputs.results` (list), `steps.scans.outputs.iterations`, `steps.scans.outputs.succeeded`

## Shell Commands

**CRITICAL - Always use `shell_quote` for variables:**

```yaml
# Safe - shell_quote prevents injection
- run: nmap ${{ inputs.target | shell_quote }}

# Safer - array syntax bypasses shell
- run: ["nmap", "-F", "${{ inputs.target }}"]
```

## Error Handling

```yaml
- name: "Flaky Step"
  run: might_fail
  timeout: 300
  on_failure: handle_error

- name: "Handle Error"
  id: handle_error
  uses: human/input
  with:
    prompt: "Step failed. Retry?"
    options: ["retry", "skip", "abort"]
```

## Filters

| Filter | Usage | Purpose |
|--------|-------|---------|
| `shell_quote` | `${{ var \| shell_quote }}` | **Required** for shell injection prevention |
| `length` | `${{ list \| length }}` | List/string length |
| `default` | `${{ var \| default('N/A') }}` | Default for undefined |
| `keys` / `values` | `${{ dict \| keys }}` | Dict keys/values |
| `first` / `last` | `${{ list \| first }}` | First/last element |
| `join` | `${{ list \| join(',') }}` | Join list elements |

### GHA-Compatible Functions

These filters provide GitHub Actions expression compatibility:

| Function | Usage | Purpose |
|----------|-------|---------|
| `contains` | `${{ array \| contains('value') }}` | Check if string/array contains value |
| `startsWith` | `${{ string \| startsWith('http') }}` | Check if string starts with prefix |
| `endsWith` | `${{ filename \| endsWith('.yaml') }}` | Check if string ends with suffix |
| `format` | `${{ '{0}@{1}' \| format(user, host) }}` | String formatting with positional args |
| `toJSON` | `${{ value \| toJSON }}` | Serialize to JSON string |
| `fromJSON` | `${{ json_str \| fromJSON }}` | Parse JSON string |

## Available Actions

See `references/actions.md` for complete reference.

### Shell Execution
```yaml
- run: nmap ${{ inputs.target | shell_quote }}
  timeout: 300
  capture_mode: file  # "memory" (default) or "file" for large outputs
```

### HTTP Requests
```yaml
- uses: http/request
  with:
    url: "https://api.example.com/data"
    method: GET
    headers:
      Authorization: "Bearer ${{ env.TOKEN }}"
```

### LLM Actions
```yaml
# Extract structured data
- uses: llm/extract
  with:
    input: ${{ steps.scan.outputs.stdout }}
    prompt: "Extract open ports as JSON array"
    schema:
      type: object
      properties:
        ports: { type: array, items: { type: integer } }

# Make decisions
- uses: llm/decide
  with:
    context: ${{ steps.analysis.outputs }}
    prompt: "Should we proceed to exploitation?"
    choices: ["proceed", "skip", "manual_review"]

# Analyze content (outputs: analysis)
- uses: llm/analyze
  with:
    content: ${{ steps.scan.outputs.stdout }}
    prompt: "Identify security vulnerabilities"
    output_format: bullet_points  # prose, bullet_points, numbered, json

# Simple instruction (outputs: response)
- uses: llm/instruct
  with:
    instruction: "Summarize in 3 bullet points"
    input: ${{ steps.fetch.outputs.content }}
```

### Human Input
```yaml
- uses: human/input
  with:
    prompt: "Enter target IP"
    options: ["192.168.1.1", "10.0.0.1", "custom"]
    default: "192.168.1.1"
```

### Control Flow
```yaml
# Early exit
- uses: control/exit
  with:
    message: "Completed early"

# Set state
- uses: state/set
  with:
    variables:
      retry_count: ${{ (steps.handler.outputs.retry_count | default(0)) + 1 }}
```

### Script Execution
```yaml
# Python script (inline)
- uses: script/python
  with:
    code: |
      import json, os
      data = json.loads(os.environ.get('INPUT', '{}'))
      print(json.dumps({"count": len(data)}))
    env:
      INPUT: "${{ steps.fetch.outputs.response }}"
    timeout: 60

# Python script (file)
- uses: script/python
  with:
    path: scripts/analyze.py
    env:
      TARGET: "${{ inputs.target }}"

# Bash script
- uses: script/bash
  with:
    script: |
      set -e
      echo "Processing..."
      mkdir -p /tmp/workspace
    timeout: 30
```

## Cleanup Blocks

```yaml
jobs:
  main:
    finally:
      - run: echo "Job completed" >> log.txt
    steps: [...]

finally:  # Workflow-level cleanup
  - run: rm -rf ${{ env.SCAN_DIR }}/tmp
```

## Complete Examples

See `references/examples.md` for full workflow examples.

## Best Practices

1. **Always use `shell_quote`** for user inputs in shell commands
2. **Use meaningful step IDs** for referencing outputs
3. **Set appropriate timeouts** (default: 300s)
4. **Use `continue_on_error`** in loops for resilience
5. **Add `on_failure` handlers** for critical steps
6. **Use `capture_mode: file`** for large outputs
7. **Validate inputs** with `type`, `pattern`, `min`/`max`
