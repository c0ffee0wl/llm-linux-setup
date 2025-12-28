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

## Scaffolding

Create new workflow files from templates:

```bash
workflow create my-scan --template=osint      # Create from OSINT template
workflow create recon -t scan                 # Port scanning template
workflow create my-workflow                   # Minimal template (default)
workflow create --list-templates              # Show available templates
```

Available templates: `minimal`, `osint`, `scan`, `credential`, `interactive`, `api`

## Validation

Use the `workflow` CLI to validate workflow YAML files:

```bash
workflow validate workflow.yaml              # Validate with warnings
workflow validate workflow.yaml --strict     # Treat warnings as errors
workflow validate workflow.yaml -q           # Quiet mode, exit code only
workflow analyze workflow.yaml               # Show execution flow (static analysis)
```

## IDE Schema Validation

For autocomplete and validation in VS Code/editors with YAML support, add this comment at the top of your workflow file:

```yaml
# yaml-language-server: $schema=./assets/workflow-schema.json
```

The current schema is available in `assets/workflow-schema.json`. To regenerate:

```bash
workflow schema --pretty -o assets/workflow-schema.json
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

### Network and Validation Filters

| Filter | Usage | Purpose |
|--------|-------|---------|
| `is_valid_ip` | `${{ host \| is_valid_ip }}` | Check if valid IPv4/IPv6 |
| `is_private_ip` | `${{ host \| is_private_ip }}` | Check if RFC1918/RFC4193 private |
| `in_cidr` | `${{ host \| in_cidr('10.0.0.0/8') }}` | Check if IP in CIDR range |
| `file_exists` | `${{ path \| file_exists }}` | Check if file exists |
| `in_list` | `${{ value \| in_list(allowed) }}` | Check if value in list |

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

# Generate text with optional formatting (outputs: text, response)
- uses: llm/generate
  with:
    prompt: "Identify security vulnerabilities"
    input: ${{ steps.scan.outputs.stdout }}
    format: bullets  # prose (default), bullets, numbered, json

# Simple instruction (outputs: response)
- uses: llm/instruct
  with:
    instruction: "Summarize in 3 bullet points"
    input: ${{ steps.fetch.outputs.content }}
```

### Human Input
```yaml
# Free-form text input
- uses: human/input
  with:
    prompt: "Enter target IP"
    input_type: text  # text (default), multiline, file, editor

# Binary confirmation
- uses: human/decide
  with:
    prompt: "Proceed with exploitation?"

# Single choice selection
- uses: human/decide
  with:
    prompt: "Select target"
    choices: ["192.168.1.1", "10.0.0.1", "custom"]

# Multi-selection
- uses: human/decide
  with:
    prompt: "Select targets to scan"
    choices: ["host1", "host2", "host3"]
    multi: true
```

### Control Flow
```yaml
# Early exit
- uses: control/exit
  with:
    message: "Completed early"

# Wait for duration
- uses: control/wait
  with:
    duration: 30  # seconds

# Wait until condition
- uses: control/wait
  with:
    until: ${{ steps.check.outputs.ready == true }}
    interval: 5    # poll every 5s
    timeout: 300   # fail after 5min

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

# Python script with sandbox (bwrap isolation)
- uses: script/python
  with:
    path: scripts/analyze.py
    sandbox: true  # read-only root, no network, PID isolation
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

### File Operations
```yaml
# Read file
- uses: file/read
  with:
    path: config.yaml
    encoding: utf-8  # utf-8 (default), binary, auto

# Write file
- uses: file/write
  with:
    path: results/${{ inputs.target }}.txt
    content: ${{ steps.scan.outputs.stdout }}
    mode: overwrite  # create, overwrite (default), append
    mkdir: true      # create parent directories
```

### Parse Operations
```yaml
# Parse JSON with queries
- uses: parse/json
  with:
    input: ${{ steps.api.outputs.body }}
    queries:
      hosts: ".results[].hostname"
      count: ".results | length"
    defaults:
      hosts: []

# Extract with regex
- uses: parse/regex
  with:
    input: ${{ steps.scan.outputs.stdout }}
    pattern: '(?P<user>\w+):(?P<hash>[a-f0-9]{32})'
    mode: all  # first, all (default)
```

### Notifications
```yaml
# Desktop notification
- uses: notify/desktop
  with:
    title: "Scan Complete"
    message: "Found ${{ count }} vulnerabilities"
    urgency: normal  # low, normal, critical
    icon: security   # optional xdg icon name

# Webhook notification
- uses: notify/webhook
  with:
    url: ${{ env.SLACK_WEBHOOK_URL }}
    method: POST
    body:
      text: "Workflow complete"
    headers:
      Authorization: "Bearer ${{ env.TOKEN }}"
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
