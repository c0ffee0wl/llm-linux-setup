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
| `capture_mode` | string | "memory" | Output capture: "memory", "file", "stream", "tty" |
| `on_failure` | string | - | Step ID to jump to on failure |

**Array syntax (safer):**
```yaml
- run: ["nmap", "-F", "-sV", "${{ inputs.target }}"]
```

**Capture modes:**
- `memory` - Capture stdout/stderr in state (default)
- `file` - Save to temp file, store path in outputs

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
| `input` | string | Content to analyze (alias: `content`) |
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
    context: |
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
| `context` | string | Context for decision |
| `prompt` | string | Question to answer |
| `choices` | list | Valid choice strings |

**Outputs:**
- `decision` - Selected choice string
- `choices` - List of available choices

---

### `llm/analyze` - Free-Form Analysis

Get LLM analysis with optional format control.

```yaml
- name: "Summarize Findings"
  id: summary
  uses: llm/analyze
  with:
    content: ${{ steps.all_scans.outputs.results }}
    prompt: "Summarize the security findings and prioritize by risk"
    output_format: bullet_points
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `content` | any | Content to analyze (alias: `input`) |
| `prompt` | string | Analysis prompt |
| `output_format` | string | Format: "prose", "bullet_points", "numbered", "json" |

**Outputs:**
- `analysis` - Analysis text
- `parsed` - Parsed JSON (if output_format=json and valid JSON returned)

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

### `human/input` - User Prompt

Prompt user for input with optional choices.

```yaml
- name: "Get Target"
  id: target_input
  uses: human/input
  with:
    prompt: "Enter target IP or select preset"
    options:
      - "192.168.1.1"
      - "10.0.0.1"
      - "custom"
    default: "192.168.1.1"
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `prompt` | string | Prompt message |
| `options` | list | Optional list of choices |
| `default` | string | Default value |

**Outputs:**
- `response` - User's response
- `is_default` - True if user accepted default

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

## Guardrails

Guardrails validate step outputs and can route to different steps based on validation results.

### Step-Level Guardrails

```yaml
- name: "Analyze Findings"
  id: analyze
  uses: llm/analyze
  with:
    content: ${{ steps.scan.outputs.stdout }}
    prompt: "Summarize security findings"
  guardrails:
    - type: secrets_present
      on_fail: handle_secret_leak
    - type: pii
      on_fail: redact_step
    - type: regex
      pattern: "CRITICAL|HIGH"
      on_match: escalate_critical
```

### Guardrail Types

| Type | Description |
|------|-------------|
| `regex` | Match output against regex pattern |
| `contains_string` | Check if output contains a string |
| `json_schema` | Validate output against JSON schema |
| `llm_judge` | Use LLM to evaluate output quality |
| `secrets_present` | Detect potential secrets/credentials |
| `pii` | Detect personally identifiable information |

### Guardrail Actions

| Action | Description |
|--------|-------------|
| `on_fail: step_id` | Route to step when validation fails |
| `on_pass: step_id` | Route to step when validation passes |
| `on_match: step_id` | For pattern guardrails, route when matched |
| `on_fail: retry` | Re-run the step |
| `on_fail: abort` | Abort workflow (default) |
| `on_fail: continue` | Log warning and continue |

### Retry Configuration

```yaml
guardrails:
  - type: llm_judge
    prompt: "Is this output safe and well-formatted?"
    on_fail: retry
    max_retries: 3
    on_retry_exhausted: abort  # or "skip" or step_id
```

### LLM Judge Example

```yaml
- name: "Generate Summary"
  id: summary
  uses: llm/generate
  with:
    prompt: "Summarize the findings professionally"
    context: ${{ steps.scan.outputs }}
  guardrails:
    - type: llm_judge
      prompt: |
        Check if the output:
        1. Is professional in tone
        2. Contains no sensitive data
        3. Is factually accurate based on the context
      on_fail: retry
      max_retries: 2
```
