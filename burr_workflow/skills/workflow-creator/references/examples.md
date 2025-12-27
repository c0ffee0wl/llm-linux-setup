# Workflow Examples

## Table of Contents

1. [OSINT Reconnaissance](#osint-reconnaissance)
2. [Port Scanning with Analysis](#port-scanning-with-analysis)
3. [Credential Testing](#credential-testing)
4. [Interactive Workflow](#interactive-workflow)
5. [API Integration](#api-integration)

---

## OSINT Reconnaissance

Full reconnaissance workflow with subdomain enumeration and scanning.

```yaml
name: "osint-recon"
schema_version: "1.0"
description: "Comprehensive OSINT reconnaissance workflow"

inputs:
  target:
    description: "Target domain"
    type: string
    required: true
    pattern: "^[a-z0-9.-]+$"
  aggressive:
    description: "Enable aggressive scanning"
    type: boolean
    default: false

env:
  SCAN_DIR: "./scans/${{ inputs.target }}"
  TIMESTAMP: "${{ now().strftime('%Y%m%d_%H%M%S') }}"

jobs:
  main:
    steps:
      - name: "Setup"
        id: setup
        run: mkdir -p ${{ env.SCAN_DIR | shell_quote }}

      - name: "Find Subdomains"
        id: recon
        run: subfinder -d ${{ inputs.target | shell_quote }} -o -
        timeout: 300

      - name: "Filter Live Hosts"
        id: filter
        uses: llm/extract
        with:
          input: ${{ steps.recon.outputs.stdout }}
          prompt: "Extract all subdomains as a list"
          schema:
            type: object
            properties:
              subdomains: { type: array, items: { type: string } }

      - name: "Scan Each Subdomain"
        id: scans
        run: |
          nmap -F ${{ loop.item | shell_quote }} -oN -
        loop: ${{ steps.filter.outputs.subdomains }}
        continue_on_error: true
        capture_mode: file

      - name: "Analyze Results"
        id: analysis
        uses: llm/analyze
        with:
          content: ${{ steps.scans.outputs.results }}
          prompt: |
            Analyze the scan results across all subdomains.
            Identify:
            1. Interesting open ports
            2. Potential attack vectors
            3. Priority targets for further investigation

      - name: "Generate Report"
        id: report
        run: |
          echo "# Reconnaissance Report: ${{ inputs.target }}" > ${{ env.SCAN_DIR }}/report.md
          echo "" >> ${{ env.SCAN_DIR }}/report.md
          echo "## Summary" >> ${{ env.SCAN_DIR }}/report.md
          echo "${{ steps.analysis.outputs.analysis }}" >> ${{ env.SCAN_DIR }}/report.md

    finally:
      - run: echo "Scan completed at $(date)" >> ${{ env.SCAN_DIR }}/log.txt

finally:
  - run: echo "Workflow finished"
```

---

## Port Scanning with Analysis

Targeted port scanning with LLM-powered vulnerability assessment.

```yaml
name: "port-scan-analysis"
schema_version: "1.0"
description: "Port scan with AI-powered analysis"

inputs:
  target:
    description: "Target IP or hostname"
    type: string
    required: true
  ports:
    description: "Port range"
    type: string
    default: "1-1000"

jobs:
  main:
    steps:
      - name: "Quick Scan"
        id: quick
        run: ["nmap", "-p", "${{ inputs.ports }}", "-sV", "--version-light", "${{ inputs.target }}"]
        timeout: 300

      - name: "Parse Results"
        id: parse
        uses: llm/extract
        with:
          input: ${{ steps.quick.outputs.stdout }}
          prompt: "Extract open ports with service info"
          schema:
            type: object
            properties:
              ports:
                type: array
                items:
                  type: object
                  properties:
                    port: { type: integer }
                    state: { type: string }
                    service: { type: string }
                    version: { type: string }

      - name: "Assess Risk"
        id: assess
        if: ${{ steps.parse.outputs.ports | length > 0 }}
        uses: llm/decide
        with:
          context: |
            Open ports found: ${{ steps.parse.outputs.ports }}
            Target: ${{ inputs.target }}
          prompt: "What is the recommended next action?"
          choices:
            - deep_scan
            - vulnerability_check
            - manual_review
            - no_action

      - name: "Deep Scan"
        id: deep
        if: ${{ steps.assess.outputs.decision == 'deep_scan' }}
        run: |
          nmap -sC -sV -p ${{ steps.parse.outputs.ports | map(attribute='port') | join(',') }} ${{ inputs.target | shell_quote }}
        timeout: 600

      - name: "Check Vulnerabilities"
        id: vuln
        if: ${{ steps.assess.outputs.decision == 'vulnerability_check' }}
        run: |
          nmap --script vuln -p ${{ steps.parse.outputs.ports | map(attribute='port') | join(',') }} ${{ inputs.target | shell_quote }}
        timeout: 600
        on_failure: vuln_failed

      - name: "Handle Vuln Check Failure"
        id: vuln_failed
        uses: human/input
        with:
          prompt: "Vulnerability scan failed. How to proceed?"
          options: ["retry", "skip", "manual"]

on_complete:
  - name: "Summary"
    uses: llm/analyze
    with:
      content: |
        Quick scan: ${{ steps.quick.outputs }}
        Parsed ports: ${{ steps.parse.outputs }}
        Assessment: ${{ steps.assess.outputs }}
        Deep scan: ${{ steps.deep.outputs | default('N/A') }}
        Vuln check: ${{ steps.vuln.outputs | default('N/A') }}
      prompt: "Provide executive summary of findings"
```

---

## Credential Testing

Password testing workflow with early break on success.

```yaml
name: "credential-test"
schema_version: "1.0"
description: "Test credentials against target service"

inputs:
  target:
    description: "Target URL or IP"
    type: string
    required: true
  username:
    description: "Username to test"
    type: string
    required: true
  wordlist:
    description: "Path to password wordlist"
    type: file
    required: true

jobs:
  main:
    steps:
      - name: "Load Wordlist"
        id: load
        run: cat ${{ inputs.wordlist | shell_quote }}

      - name: "Parse Passwords"
        id: passwords
        uses: llm/extract
        with:
          input: ${{ steps.load.outputs.stdout }}
          prompt: "Split into list of passwords (one per line)"
          schema:
            type: object
            properties:
              passwords: { type: array, items: { type: string } }

      - name: "Test Credentials"
        id: test
        run: |
          # Test credential - exit 0 if valid
          curl -s -o /dev/null -w "%{http_code}" \
            -u "${{ inputs.username }}:${{ loop.item | shell_quote }}" \
            ${{ inputs.target | shell_quote }} | grep -q "200"
        loop: ${{ steps.passwords.outputs.passwords }}
        continue_on_error: true
        break_if: ${{ loop.output.exit_code == 0 }}

      - name: "Report Success"
        id: success
        if: ${{ steps.test.outputs.break_early }}
        run: |
          echo "Password found: ${{ steps.test.outputs.break_item }}"
          echo "Attempts: ${{ steps.test.outputs.break_index }}"

      - name: "Report Failure"
        id: failure
        if: ${{ not steps.test.outputs.break_early }}
        run: echo "No valid password found after ${{ steps.test.outputs.iterations }} attempts"
```

---

## Interactive Workflow

Workflow with multiple user decision points.

```yaml
name: "interactive-pentest"
schema_version: "1.0"
description: "Interactive penetration testing workflow"

inputs:
  scope:
    description: "Target scope definition"
    type: string
    required: true

jobs:
  main:
    steps:
      - name: "Confirm Scope"
        id: confirm
        uses: human/input
        with:
          prompt: |
            Confirm testing scope:
            ${{ inputs.scope }}

            Proceed with testing?
          options:
            - "Yes, proceed"
            - "No, modify scope"
            - "Cancel"
          default: "Yes, proceed"

      - name: "Exit if cancelled"
        if: ${{ steps.confirm.outputs.response == 'Cancel' }}
        uses: control/exit
        with:
          message: "Testing cancelled by user"

      - name: "Reconnaissance"
        id: recon
        if: ${{ steps.confirm.outputs.response == 'Yes, proceed' }}
        run: nmap -sn ${{ inputs.scope | shell_quote }}

      - name: "Review Findings"
        id: review
        uses: human/input
        with:
          prompt: |
            Hosts discovered:
            ${{ steps.recon.outputs.stdout }}

            Select action:
          options:
            - "Deep scan all"
            - "Select specific hosts"
            - "Skip to manual testing"

      - name: "Get Host Selection"
        id: selection
        if: ${{ steps.review.outputs.response == 'Select specific hosts' }}
        uses: human/input
        with:
          prompt: "Enter comma-separated list of hosts to scan"

      - name: "Deep Scan"
        id: deep_scan
        if: ${{ steps.review.outputs.response != 'Skip to manual testing' }}
        run: |
          targets="${{ steps.selection.outputs.response | default(steps.recon.outputs.stdout) }}"
          nmap -sC -sV $targets
        timeout: 600

      - name: "Log Finding"
        id: log_finding
        uses: human/input
        with:
          prompt: "Enter finding description (or 'skip' to continue)"

      - name: "Add to Report"
        if: ${{ steps.log_finding.outputs.response != 'skip' }}
        uses: report/add
        with:
          note: ${{ steps.log_finding.outputs.response }}
          severity: 5
```

---

## API Integration

Workflow integrating external APIs with error handling.

```yaml
name: "api-integration"
schema_version: "1.0"
description: "Check file hash against VirusTotal and analyze"

inputs:
  file_path:
    description: "Path to file to check"
    type: file
    required: true

env:
  VT_API_KEY: "${{ secrets.virustotal_key }}"

jobs:
  main:
    steps:
      - name: "Calculate Hash"
        id: hash
        run: sha256sum ${{ inputs.file_path | shell_quote }} | cut -d' ' -f1

      - name: "Query VirusTotal"
        id: vt
        uses: http/request
        with:
          url: "https://www.virustotal.com/api/v3/files/${{ steps.hash.outputs.stdout }}"
          method: GET
          secret_headers:
            x-apikey: virustotal_key.txt
          timeout: 30
        on_failure: vt_unavailable

      - name: "Analyze Results"
        id: analyze
        if: ${{ steps.vt.outputs.status_code == 200 }}
        uses: llm/extract
        with:
          input: ${{ steps.vt.outputs.body }}
          prompt: "Extract detection statistics and notable findings"
          schema:
            type: object
            properties:
              malicious_count: { type: integer }
              suspicious_count: { type: integer }
              harmless_count: { type: integer }
              notable_detections: { type: array, items: { type: string } }

      - name: "Risk Assessment"
        id: risk
        uses: llm/decide
        with:
          context: |
            File: ${{ inputs.file_path }}
            Hash: ${{ steps.hash.outputs.stdout }}
            VT Results: ${{ steps.analyze.outputs }}
          prompt: "What is the risk level of this file?"
          choices:
            - "critical"
            - "high"
            - "medium"
            - "low"
            - "clean"

      - name: "Handle VT Unavailable"
        id: vt_unavailable
        uses: human/input
        with:
          prompt: "VirusTotal API unavailable. How to proceed?"
          options:
            - "Retry"
            - "Skip VT check"
            - "Abort"

on_failure:
  - name: "Log Error"
    run: echo "Workflow failed: ${{ error.message }}" >> errors.log
```
