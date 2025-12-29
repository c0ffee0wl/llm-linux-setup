# Workflow Actions Reference

## Table of Contents

1. [Shell Actions](#shell-actions)
2. [HTTP Actions](#http-actions)
3. [LLM Actions](#llm-actions)
4. [Human Actions](#human-actions)
5. [State Actions](#state-actions)
6. [Control Actions](#control-actions)
7. [Report Actions](#report-actions)
8. [Script Actions](#script-actions)
9. [Guardrails](#guardrails-llm-guard-integration)

---

## Shell Actions

### `run:` - Execute Shell Command

Execute a shell command with optional timeout and capture modes.

```yaml
- name: "Port Scan"
  id: scan
  run: nmap -F ${{ inputs.target | shell_quote }}
  timeout: 300
  capture_mode: file
  on_failure: handle_error
```

**Attributes:**
| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `run` | string/list | required | Command to execute |
| `timeout` | integer | 300 | Timeout in seconds |
| `capture_mode` | string | "memory" | Output capture: "memory", "file", "none" |
| `on_failure` | string | - | Step ID to jump to on failure |

**Array syntax (safer):**
```yaml
- run: ["nmap", "-F", "-sV", "${{ inputs.target }}"]
```

**Capture modes:**
- `memory` - Capture stdout/stderr in state (default)
- `file` - Save to temp file, store path in outputs
- `none` - Discard output (fire-and-forget)

**Interactive mode:**
Use `interactive: true` for TTY commands that require terminal control.

**Outputs:**
- `stdout` - Command output (or file path if capture_mode=file)
- `stderr` - Error output
- `exit_code` - Process exit code
- `file` - Output file path (if capture_mode=file)

---

## HTTP Actions

### `http/request` - HTTP Request

Make HTTP requests with authentication and response handling.

```yaml
- name: "API Call"
  id: api
  uses: http/request
  with:
    url: "https://api.example.com/v1/scan/${{ inputs.id }}"
    method: POST
    headers:
      Authorization: "Bearer ${{ env.API_KEY }}"
      Content-Type: "application/json"
    body:
      target: ${{ inputs.target }}
      options: ["fast", "verbose"]
    timeout: 30
```

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | Request URL |
| `method` | string | "GET" | HTTP method |
| `headers` | dict | - | Request headers |
| `body` | any | - | Request body (auto-serialized) |
| `timeout` | integer | 30 | Timeout in seconds |
| `secret_headers` | dict | - | Headers from secrets (never logged) |

**Secret headers (from secrets/ directory):**
```yaml
secret_headers:
  x-api-key: virustotal_key.txt  # Reads from secrets/virustotal_key.txt
```

**Outputs:**
- `status_code` - HTTP status code
- `body` - Response body (parsed as JSON if applicable)
- `headers` - Response headers

---

## LLM Actions

### `llm/extract` - Extract Structured Data

Use LLM to extract structured data matching a JSON schema.

```yaml
- name: "Parse Scan Results"
  id: parse
  uses: llm/extract
  with:
    input: ${{ steps.scan.outputs.stdout }}
    prompt: "Extract all open ports with their services"
    schema:
      type: object
      properties:
        ports:
          type: array
          items:
            type: object
            properties:
              port: { type: integer }
              service: { type: string }
              version: { type: string }
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `input` | string | Content to analyze |
| `prompt` | string | What to extract |
| `schema` | dict | JSON Schema for output structure |

**Schema syntax (JSON Schema format):**
```yaml
schema:
  type: object
  properties:
    count: { type: integer }
    name: { type: string }
    valid: { type: boolean }
    items: { type: array, items: { type: string } }
    hosts:
      type: array
      items:
        type: object
        properties:
          ip: { type: string }
          hostname: { type: string }
  required: [count, name]
```

**Outputs:**
- Fields matching the schema structure

---

### `llm/decide` - LLM-Powered Decision

Use LLM to select from predefined choices.

```yaml
- name: "Assess Risk"
  id: assess
  uses: llm/decide
  with:
    input: |
      Scan results: ${{ steps.scan.outputs.stdout }}
      Known vulnerabilities: ${{ steps.cve.outputs }}
    prompt: "Based on the findings, what action should be taken?"
    choices:
      - proceed_exploitation
      - manual_review
      - abort_too_risky
      - skip_not_interesting
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `input` | string | Context for decision |
| `prompt` | string | Question to answer |
| `choices` | list | Valid choice strings |

**Outputs:**
- `decision` - Selected choice string
- `choices` - List of available choices

---

### `llm/analyze` - Free-Form Analysis

> **Note:** `llm/analyze` is an alias for `llm/generate`. They are functionally identical.

Get LLM analysis with optional format control.

```yaml
- name: "Summarize Findings"
  id: summary
  uses: llm/analyze
  with:
    input: ${{ steps.all_scans.outputs.results }}
    prompt: "Summarize the security findings and prioritize by risk"
    format: bullets
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `input` | any | Content to analyze |
| `prompt` | string | Analysis prompt |
| `format` | string | Format: "prose", "bullets", "numbered", "json" |

**Outputs:**
- `analysis` - Analysis text (alias: `text`, `response`)
- `parsed` - Parsed JSON (if format=json and valid JSON returned)

---

### `llm/instruct` - Instruction with Optional Feedback

Two modes: simple instruction OR airgapped with feedback collection.

**Simple mode:**
```yaml
- name: "Summarize Report"
  id: summary
  uses: llm/instruct
  with:
    instruction: "Summarize the following text in 3 bullet points"
    input: ${{ steps.fetch.outputs.content }}
    model: "gpt-4"  # optional
```

**Airgapped mode (with feedback):**
```yaml
- name: "Run Mimikatz"
  id: mimikatz
  uses: llm/instruct
  with:
    prompt: |
      Generate instructions for running Mimikatz on target.
      Target: ${{ inputs.target_host }}
      Access: ${{ inputs.access_method }}
    await_feedback: true
    feedback_type: multiline  # text, multiline, file_path, json
    analyze_feedback: true    # Parse feedback with LLM
```

**Parameters (simple mode):**
| Parameter | Type | Description |
|-----------|------|-------------|
| `instruction` | string | What to do with the input |
| `input` | string | Content to process (optional) |
| `model` | string | Override default model (optional) |

**Parameters (airgapped mode):**
| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string | Prompt for generating instructions |
| `await_feedback` | boolean | Enable airgapped mode (default: false) |
| `feedback_type` | string | "text", "multiline", "file_path", "json" |
| `analyze_feedback` | boolean | Parse feedback with LLM (default: false) |
| `model` | string | Override default model (optional) |

**Outputs (simple mode):**
- `response` - LLM's response text
- `model` - Model used

**Outputs (airgapped mode):**
- `instructions` - Generated instructions
- `feedback` - User-provided feedback (after resume)
- `feedback_analysis` - LLM analysis (if analyze_feedback=true)
- `parsed_json` - Parsed JSON (if feedback_type=json)

---

## Human Actions

### `human/input` - Free-Form User Input

Prompt user for free-form text input.

```yaml
- name: "Get Target"
  id: target_input
  uses: human/input
  with:
    prompt: "Enter target IP address"
    input_type: text
    default: "192.168.1.1"
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string | Prompt message |
| `input_type` | string | Input mode: "text" (default), "multiline", "file", "editor" |
| `default` | string | Default value |
| `timeout` | integer | Optional timeout in seconds |
| `initial_content` | string | Initial content for editor mode |

**Outputs:**
- `response` - User's response
- `is_default` - True if user accepted default

---

### `human/decide` - Choice Selection

Prompt user for confirmation or selection from predefined choices.

```yaml
# Binary confirmation (yes/no)
- name: "Confirm Action"
  id: confirm
  uses: human/decide
  with:
    prompt: "Proceed with exploitation?"

# Single choice selection
- name: "Select Target"
  id: select_target
  uses: human/decide
  with:
    prompt: "Select target IP"
    choices:
      - "192.168.1.1"
      - "10.0.0.1"
      - "custom"
    default: "192.168.1.1"

# Multi-selection
- name: "Select Targets"
  id: multi_select
  uses: human/decide
  with:
    prompt: "Select targets to scan"
    choices: ["host1", "host2", "host3"]
    multi: true
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string | Prompt message |
| `choices` | list | Predefined choices (omit for yes/no confirmation) |
| `multi` | boolean | Allow multiple selections (default: false) |
| `default` | string | Default value |
| `timeout` | integer | Optional timeout in seconds |

**Outputs:**
- `value` - Selected choice(s) (string or list if multi=true)
- `confirmed` - True if user confirmed (yes/no mode)

---

## State Actions

### `state/set` - Set State Variables

Set or update workflow state variables.

```yaml
- name: "Track Retry"
  id: track_retry
  uses: state/set
  with:
    variables:
      retry_count: ${{ (steps.track_retry.outputs.retry_count | default(0)) + 1 }}
      last_error: ${{ steps.flaky.outputs.error }}
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `variables` | dict | Key-value pairs to set |

---

### `state/append` - Append to List Variable

Append a value to a list variable.

```yaml
- name: "Track Finding"
  id: track_finding
  uses: state/append
  with:
    target: findings
    value:
      host: ${{ loop.item }}
      status: vulnerable
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `target` | string | Name of the list variable to append to |
| `value` | any | Value to append to the list |

**Outputs:**
- `{target}` - The updated list with appended value

---

## Control Actions

### `control/exit` - Early Exit

Exit workflow early with success status.

```yaml
- name: "Complete Early"
  uses: control/exit
  if: ${{ steps.check.outputs.found }}
  with:
    status: success
    message: "Target found, completing early"
    outputs:
      result: ${{ steps.check.outputs.target }}
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | "success" or "completed" |
| `message` | string | Exit message |
| `outputs` | dict | Final outputs to expose |

---

### `control/fail` - Explicit Failure

Trigger workflow failure with error message.

```yaml
- name: "Abort"
  uses: control/fail
  if: ${{ steps.validate.outputs.critical_error }}
  with:
    message: "Critical error: ${{ steps.validate.outputs.error }}"
    error_code: "VALIDATION_FAILED"
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `message` | string | Error message |
| `error_code` | string | Error classification |

---

### `control/wait` - Delay or Poll

Wait for a duration or until a condition is met.

```yaml
# Wait for duration
- uses: control/wait
  with:
    duration: 30  # seconds

# Poll until condition
- uses: control/wait
  with:
    until: ${{ steps.check.outputs.ready == true }}
    interval: 5    # poll every 5s
    timeout: 300   # fail after 5min
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `duration` | integer | Wait duration in seconds |
| `until` | string | Condition expression to poll |
| `interval` | integer | Polling interval in seconds (default: 5) |
| `timeout` | integer | Max wait time before failure (default: 300) |

---

### `control/break` - Break Loop (Advanced)

Explicitly break out of the current loop.

> **Note:** The `break_if` step property is the preferred way to conditionally break loops. Use `control/break` only when you need to break from a different location than the loop body.

```yaml
- uses: control/break
  if: ${{ steps.check.outputs.should_stop }}
```

**Outputs:**
- `break_requested` - Always `true`

---

### `control/continue` - Skip Iteration (Advanced)

Skip to the next loop iteration.

> **Note:** The `continue_on_error: true` step property is preferred for skipping failed iterations. Use `control/continue` only when you need explicit skip logic.

```yaml
- uses: control/continue
  if: ${{ loop.item.skip }}
```

**Outputs:**
- `continue_requested` - Always `true`

---

## Report Actions

### `report/add` - Add Pentest Finding

Add a finding to the pentest report (requires ReportBackend).

```yaml
- name: "Log Finding"
  uses: report/add
  with:
    note: "SQL injection in login form - ${{ steps.test.outputs.payload }}"
    severity: 8
    context: ${{ steps.test.outputs.response }}
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `note` | string | Finding description (required) |
| `severity` | integer | OWASP severity 1-9 (optional, LLM auto-assigns if omitted) |
| `context` | string | Additional context/evidence (optional) |

**Outputs:**
- `finding_id` - Unique finding identifier (e.g., "F001")
- `title` - Finding title (LLM-generated from note)
- `severity` - Assigned severity 1-9
- `success` - Whether finding was added successfully

---

## Script Actions

### `script/python` - Run Python Script

Execute Python code in a subprocess.

```yaml
# Inline code
- name: "Process Data"
  id: process
  uses: script/python
  with:
    code: |
      import json, os
      data = json.loads(os.environ.get('INPUT_DATA', '{}'))
      result = {"count": len(data), "keys": list(data.keys())}
      print(json.dumps(result))
    env:
      INPUT_DATA: "${{ steps.fetch.outputs.response }}"
    timeout: 60

# File-based
- name: "Analyze BloodHound"
  id: analyze
  uses: script/python
  with:
    path: scripts/analyze_bloodhound.py
    env:
      NEO4J_PASSWORD: ${{ env.NEO4J_PASSWORD }}
    timeout: 120
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | string | Inline Python code |
| `path` | string | Path to Python script file |
| `env` | dict | Environment variables |
| `timeout` | integer | Execution timeout (default: 30) |

**Note:** Either `code` or `path` must be specified, not both.

**Outputs:**
- `stdout` - Script standard output
- `stderr` - Script standard error
- `exit_code` - Process exit code (0 = success)

---

### `script/bash` - Run Bash Script

Execute Bash script in a subprocess.

```yaml
# Inline script
- name: "Setup Environment"
  id: setup
  uses: script/bash
  with:
    script: |
      set -e
      echo "Setting up workspace..."
      mkdir -p /tmp/workspace
      echo "Done: $(date)"
    env:
      WORKSPACE: /tmp/workspace
    timeout: 60

# File-based
- name: "Run Scanner"
  id: scanner
  uses: script/bash
  with:
    path: scripts/run_scan.sh
    env:
      TARGET: "${{ inputs.target }}"
    timeout: 300
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `script` | string | Inline Bash script |
| `path` | string | Path to Bash script file |
| `env` | dict | Environment variables |
| `timeout` | integer | Execution timeout (default: 30) |

**Note:** Either `script` or `path` must be specified, not both.

**Outputs:**
- `stdout` - Script standard output
- `stderr` - Script standard error
- `exit_code` - Process exit code (0 = success)

---

## Guardrails (LLM Guard Integration)

Guardrails validate step inputs and outputs using [llm-guard](https://llm-guard.com/) scanners. Requires: `pip install burr_workflow[guard]`

### Workflow-Level Defaults

Apply guardrails to all steps:

```yaml
name: secure-workflow
version: "1.0"

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
      - id: analyze
        uses: llm/generate
        with:
          prompt: ${{ inputs.query }}
        # Inherits workflow guardrails
```

### Step-Level Overrides

Override or extend workflow defaults per step:

```yaml
steps:
  # Adds anonymize scanner, keeps workflow defaults
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

  # Disable guardrails for trusted step
  - id: trusted_internal
    run: internal_tool.sh
    guardrails: false
```

### Merge Behavior

| Step guardrails | Behavior |
|-----------------|----------|
| Not specified | Inherit workflow defaults |
| `guardrails: {...}` | Merge with workflow (step adds to/overrides defaults) |
| `guardrails: false` | Disable all guardrails for this step |

### Input Scanners (12)

| Scanner | Parameters | Description |
|---------|------------|-------------|
| `anonymize` | `entities: [PERSON, EMAIL, ...]` | Replace PII with placeholders |
| `prompt_injection` | `threshold: 0.9` | Detect jailbreak attempts |
| `secrets` | `redact: true` | Detect API keys, passwords |
| `invisible_text` | - | Strip zero-width chars |
| `token_limit` | `limit: 4096` | Prevent context overflow |
| `ban_topics` | `topics: [...]` | Block specific subjects |
| `ban_substrings` | `substrings: [...]` | Block text patterns |
| `ban_code` | - | Block code in prompts |
| `code` | `languages: [...], blocked: true` | Detect/allow languages |
| `gibberish` | `threshold: 0.7` | Detect nonsense |
| `language` | `languages: [en]` | Restrict languages |
| `regex` | `patterns: [...], blocked: true` | Custom regex validation |

### Output Scanners (17)

| Scanner | Parameters | Description |
|---------|------------|-------------|
| `deanonymize` | - | Restore anonymized entities |
| `sensitive` | `redact: true, entities: [...]` | Detect/redact sensitive data |
| `no_refusal` | - | Detect model refusals |
| `factual_consistency` | `threshold: 0.7` | Detect hallucinations |
| `relevance` | `threshold: 0.5` | Check output relevance |
| `json` | `required_fields: [...]` | Validate JSON structure |
| `malicious_urls` | - | Detect dangerous URLs |
| `url_reachability` | `timeout: 5` | Verify URLs accessible |
| `language_same` | - | Same language as input |
| `language` | `languages: [en]` | Restrict output language |
| `reading_time` | `max_time: 5` | Limit response length |
| `gibberish` | `threshold: 0.7` | Detect nonsense |
| `ban_topics` | `topics: [...]` | Block subjects |
| `ban_substrings` | `substrings: [...]` | Block text |
| `ban_code` | - | Block code in responses |
| `code` | `languages: [...], blocked: true` | Detect/allow languages |
| `regex` | `patterns: [...], blocked: true` | Custom regex |

### Vault Pattern (PII Handling)

Use `anonymize` + `deanonymize` together for PII-safe LLM processing:

```yaml
guardrails:
  input:
    anonymize:
      entities: [PERSON, EMAIL, PHONE, CREDIT_CARD]
  output:
    deanonymize: {}

steps:
  - id: process
    uses: llm/generate
    with:
      prompt: "Summarize: ${{ inputs.user_data }}"
    # Input: "Contact John at john@example.com"
    # → Anonymized: "Contact [PERSON_1] at [EMAIL_1]"
    # → LLM processes anonymized text
    # → Output restored with original values
```

### Failure Handling

| `on_fail` value | Behavior |
|-----------------|----------|
| `abort` | Stop workflow with error (default) |
| `retry` | Re-run step up to `max_retries` times |
| `continue` | Log warning, continue execution |
| `step_id` | Route to specified step |

```yaml
guardrails:
  input:
    prompt_injection: { threshold: 0.9 }
  output:
    sensitive: { redact: true }
  on_fail: retry
  max_retries: 3
```

### Pre-downloading Models

Some scanners use ML models (~1.5GB total). Pre-download for offline use:

```bash
workflow guard-init                      # All common models
workflow guard-init -s prompt_injection  # Specific scanner
```
