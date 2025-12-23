"""Report/Findings mixin for llm-assistant.

This module provides pentest finding management:
- Project creation with language selection
- LLM-assisted finding analysis
- Evidence capture
- Export to Word via pandoc
- /report command handling
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import llm
import yaml

from .schemas import FindingSchema
from .templates import render
from .utils import get_config_dir, validate_language_code, md_table_escape, yaml_escape, parse_command, ConsoleHelper

if TYPE_CHECKING:
    from rich.console import Console


class ReportMixin:
    """Mixin providing pentest findings functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - findings_base_dir: Path to findings directory
    - findings_project: Optional[str] current project name
    - model_name: str for LLM model
    - conversation: llm.Conversation for context
    - pending_summary: Optional[str] from /squash
    - chat_terminal_uuid: str for excluding chat terminal
    - _get_all_terminals_with_content: method to get terminal content
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    findings_base_dir: Path
    findings_project: Optional[str]
    model_name: str
    pending_summary: Optional[str]
    chat_terminal_uuid: str

    def _handle_report_command(self, args: str) -> bool:
        """Route /report subcommands to appropriate handlers."""
        subcmd, subargs = parse_command(args)
        subcmd = subcmd.lower()

        if not subcmd:
            self.console.print("[yellow]Usage: /report <note> or /report <subcommand>[/]")
            self.console.print("[dim]Subcommands: init, list, edit, delete, export, severity, projects, open[/]")
            return True

        # Dispatch subcommands
        if subcmd == "init":
            return self._report_init(subargs)
        elif subcmd == "list":
            return self._report_list()
        elif subcmd == "edit":
            return self._report_edit(subargs)
        elif subcmd == "delete":
            return self._report_delete(subargs)
        elif subcmd == "export":
            return self._report_export(subargs)
        elif subcmd == "severity":
            return self._report_set_severity(subargs)
        elif subcmd == "projects":
            return self._report_projects()
        elif subcmd == "open":
            return self._report_open(subargs)
        else:
            # Not a subcommand - treat entire args as a quick note
            return self._report_add(args)

    def _report_init(self, args: str) -> bool:
        """Initialize a new pentest project with language selection."""
        project_name, lang_code = parse_command(args)

        if not project_name or not lang_code:
            ConsoleHelper.error(self.console, "Usage: /report init <project-name> <language-code>")
            self.console.print("[dim]Example: /report init acme-webapp-2025 en[/]")
            self.console.print("[dim]Language codes: en (English), de (German), es (Spanish), fr (French), ...[/]")
            return True

        lang_code = lang_code.lower().strip()

        # Validate language code using iso639-lang library
        language_name = validate_language_code(lang_code)
        if not language_name:
            ConsoleHelper.error(self.console, f"Invalid language code: {lang_code}")
            self.console.print("[dim]Use ISO 639-1 codes: en, de, es, fr, it, nl, pt, ru, ja, ko, zh, etc.[/]")
            return True

        # Sanitize project name (alphanumeric, hyphens, underscores)
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', project_name)
        project_dir = self.findings_base_dir / safe_name

        if project_dir.exists():
            # Project exists - switch to it
            self.findings_project = safe_name
            ConsoleHelper.warning(self.console, f"Project already exists, switched to: {safe_name}")
            return True

        # Create project directory and initial findings.md
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "evidence").mkdir(exist_ok=True)

        # Create initial findings.md with project YAML frontmatter including language
        findings_file = project_dir / "findings.md"
        initial_content = f"""---
project: {safe_name}
created: {datetime.now().strftime('%Y-%m-%d')}
assessor: Pentest Team
language: {lang_code}
language_name: {language_name}
---

# Penetration Test Findings: {safe_name}

| ID | Severity | Title |
|----|----------|-------|

"""
        findings_file.write_text(initial_content)

        self.findings_project = safe_name
        ConsoleHelper.success(self.console, f"Created project: {project_dir} ({language_name})")
        self.console.print("[dim]Add findings with: /report \"<quick note>\"[/]")
        return True

    def _report_add(self, quick_note: str) -> bool:
        """Add a new finding with LLM-assisted analysis."""
        if not self.findings_project:
            ConsoleHelper.error(self.console, "No project initialized. Use /report init <project> <lang>")
            return True

        quick_note = quick_note.strip().strip('"\'')
        if not quick_note:
            ConsoleHelper.error(self.console, "Usage: /report \"<quick note about vulnerability>\"")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            ConsoleHelper.error(self.console, f"Project file not found: {findings_file}")
            return True

        # Parse existing findings
        project_meta, findings = self._parse_findings_file(findings_file)

        # Safety check: if parsing returned empty project_meta, file may be corrupted
        if not project_meta:
            ConsoleHelper.error(self.console, "Could not parse project file (missing or invalid YAML frontmatter)")
            self.console.print(f"[dim]Check file manually: {findings_file}[/]")
            return True

        # Get language from project metadata (default to English for legacy projects)
        language_name = project_meta.get('language_name', 'English')

        # Generate next finding ID
        finding_id = self._get_next_finding_id(findings)

        # Capture evidence (terminal context)
        evidence = self._capture_report_evidence(finding_id, project_dir)

        # Get terminal context for LLM analysis
        context = None
        try:
            terminals = self._get_all_terminals_with_content()
            if terminals:
                context_parts = []
                for t in terminals:
                    if t.get('uuid') != self.chat_terminal_uuid:
                        context_parts.append(f"=== Terminal {t.get('title', 'unknown')} ===\n{t.get('content', '')[:2000]}")
                context = "\n\n".join(context_parts)[:5000]
        except Exception:
            pass

        # LLM analysis with schema enforcement
        ConsoleHelper.info(self.console, f"Analyzing finding ({language_name})...")
        try:
            analysis = self._analyze_finding(quick_note, context, language=language_name)
        except Exception as e:
            ConsoleHelper.error(self.console, f"LLM analysis failed: {e}")
            # Fallback to manual entry
            analysis = {
                "suggested_title": quick_note[:60],
                "severity": 5,
                "severity_rationale": "Manual entry - please review",
                "description": quick_note,
                "remediation": "To be determined"
            }

        # Build finding metadata
        finding_meta = {
            "id": finding_id,
            "title": analysis.get("suggested_title", quick_note[:60]),
            "severity": analysis.get("severity", 5),
            "severity_rationale": analysis.get("severity_rationale", ""),
            "created": datetime.now().isoformat(),
            "evidence": evidence
        }

        # Build finding markdown body (Description -> Remediation -> Evidence)
        finding_body = f"""
### Description

{analysis.get('description', quick_note)}

### Remediation

{analysis.get('remediation', 'To be determined')}

### Evidence

"""
        if evidence:
            for ev in evidence:
                ev_path = ev.get('path', '')
                finding_body += f"- {ev.get('type', 'file').title()}: [{Path(ev_path).name}]({ev_path})\n"
        else:
            finding_body += "(none captured)\n"

        # Append to findings list
        findings.append((finding_meta, finding_body))

        # Write updated file
        self._write_findings_file(findings_file, project_meta, findings)

        # Display confirmation with severity color
        severity = finding_meta['severity']
        sev_color = "red" if severity >= 7 else "yellow" if severity >= 4 else "green"
        sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"

        ConsoleHelper.success(self.console, f"Added [{sev_color}]{finding_id}[/] ({severity} {sev_label}): {finding_meta['title']}")
        return True

    def _analyze_finding(self, quick_note: str, context: Optional[str] = None,
                         language: str = "English") -> dict:
        """Use LLM to analyze finding with template and conversation context.

        Creates an isolated LLM call (not added to main conversation) that has
        access to terminal context and conversation history for better assessment.
        """
        model = llm.get_model(self.model_name)

        # Render the report analysis prompt with language using Jinja2 template
        system_prompt = render('prompts/report_analysis.j2', language=language)

        # Build prompt with quick note
        prompt = f"Quick note from penetration tester: {quick_note}"

        # Add terminal context if available
        if context:
            prompt += f"\n\n## Terminal Context (recent commands/output):\n{context}"

        # Add entire conversation history for context (excluding only internal tool markers and thinking)
        # This gives the LLM full awareness of what the tester has been working on
        # IMPORTANT: Works with /squash - includes pending_summary and <conversation_summary> tags
        history_parts = []
        conversation_attachments = []

        # Include pending summary from /squash if finding is created immediately after squash
        if self.pending_summary:
            history_parts.append(f"## Previous Conversation Summary:\n{self.pending_summary}")

        if self.conversation.responses:
            for resp in self.conversation.responses:  # ALL responses, not limited
                # Get user prompt (if available)
                if hasattr(resp, 'prompt') and resp.prompt:
                    user_text = resp.prompt.prompt if hasattr(resp.prompt, 'prompt') else str(resp.prompt)

                    # Extract <conversation_summary> content if present (post-squash)
                    # This contains the compressed history from earlier in the session
                    summary_match = re.search(r'<conversation_summary>(.*?)</conversation_summary>', user_text, re.DOTALL)
                    if summary_match:
                        # Insert at beginning since it's older context
                        if history_parts and history_parts[0].startswith("## Previous Conversation Summary:"):
                            # Already have pending_summary, add this after
                            history_parts.insert(1, f"## Earlier Conversation Summary:\n{summary_match.group(1).strip()}")
                        else:
                            history_parts.insert(0, f"## Previous Conversation Summary:\n{summary_match.group(1).strip()}")
                        # Remove summary tag from user_text for cleaner processing
                        user_text = re.sub(r'<conversation_summary>.*?</conversation_summary>\s*', '', user_text, flags=re.DOTALL)

                    # Skip only internal tool-related messages (keep everything else including terminal context)
                    if user_text and user_text.strip() and not user_text.startswith('<tool'):
                        history_parts.append(f"User: {user_text.strip()}")

                    # Collect attachments (screenshots, images) for evidence context
                    if hasattr(resp.prompt, 'attachments') and resp.prompt.attachments:
                        conversation_attachments.extend(resp.prompt.attachments)

                # Get assistant response text
                if hasattr(resp, 'text'):
                    text = resp.text()
                    # Include all assistant responses (JSON, code blocks are valuable context)
                    # Only strip <thinking> traces (keep everything else)
                    if text and text.strip():
                        # Remove thinking traces but keep the rest
                        cleaned = re.sub(r'<thinking>.*?</thinking>\s*', '', text, flags=re.DOTALL)
                        if cleaned.strip():
                            history_parts.append(f"Assistant: {cleaned.strip()}")

        if history_parts:
            prompt += f"\n\n## Conversation Context:\n" + "\n\n".join(history_parts)

        # Limit total attachments to avoid token bloat (most recent 5)
        conversation_attachments = conversation_attachments[-5:] if conversation_attachments else []

        # Only pass attachments if model supports them
        attachments_to_pass = None
        if conversation_attachments and hasattr(model, 'attachment_types') and model.attachment_types:
            # Filter to only supported attachment types
            attachments_to_pass = [
                att for att in conversation_attachments
                if hasattr(att, 'type') and att.type in model.attachment_types
            ][:3]  # Max 3 attachments

        # Use schema enforcement if model supports it
        # This is an ISOLATED call - does NOT add to self.conversation
        if hasattr(model, 'supports_schema') and model.supports_schema:
            response = model.prompt(
                prompt,
                system=system_prompt,
                schema=FindingSchema,
                attachments=attachments_to_pass  # Include screenshots/images if supported
            )
            return json.loads(response.text())
        else:
            # Fallback with JSON instructions
            json_prompt = f"""{prompt}

Respond with a JSON object containing these fields:
- suggested_title: concise vulnerability title (max 60 chars) in {language}
- severity: integer 1-9 per OWASP Risk Matrix
- severity_rationale: brief explanation in {language}
- description: expanded technical description in {language}
- remediation: step-by-step recommendations in {language}"""
            response = model.prompt(
                json_prompt,
                system=system_prompt,
                attachments=attachments_to_pass
            )
            # Try to extract JSON from response
            text = response.text()
            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            result = json.loads(text.strip())
            # Validate and clamp severity to 1-9 range (no schema enforcement)
            if 'severity' in result:
                try:
                    result['severity'] = max(1, min(9, int(result['severity'])))
                except (ValueError, TypeError):
                    result['severity'] = 5  # Default to medium if invalid
            return result

    def _capture_report_evidence(self, finding_id: str, project_dir: Path) -> List[Dict]:
        """Capture evidence (terminal context) for a finding."""
        evidence = []
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        evidence_dir = project_dir / "evidence"
        evidence_dir.mkdir(exist_ok=True)

        # Capture terminal context
        try:
            terminals = self._get_all_terminals_with_content()
            context_parts = []
            for t in terminals:
                if t.get('uuid') != self.chat_terminal_uuid:
                    content = t.get('content', '')[:5000]
                    if content.strip():
                        context_parts.append(f"=== Terminal: {t.get('title', 'unknown')} ===\n{content}")

            if context_parts:
                context_file = evidence_dir / f"{finding_id}_context_{timestamp}.txt"
                context_file.write_text("\n\n".join(context_parts))
                evidence.append({
                    "type": "context",
                    "path": f"evidence/{context_file.name}"
                })
        except Exception:
            pass

        return evidence

    def _parse_findings_file(self, path: Path) -> Tuple[Dict, List[Tuple[Dict, str]]]:
        """Parse findings.md: return (project_frontmatter, list of (finding_yaml, finding_body) tuples).

        Note: The file format uses --- as YAML block delimiters. Avoid using bare ---
        lines in finding descriptions/remediation as they would be incorrectly parsed.
        """
        content = path.read_text()

        # Split on YAML frontmatter delimiter
        parts = content.split('---')
        if len(parts) < 3:
            # No valid frontmatter
            return {}, []

        # First block is project metadata
        project_meta = yaml.safe_load(parts[1]) or {}

        # Remaining content contains findings
        remaining = '---'.join(parts[2:])

        # Parse per-finding YAML blocks
        # Pattern: ---\n<yaml>\n---\n<markdown>
        findings = []

        # Split by --- delimiters
        blocks = remaining.split('---')

        # First block is the summary table (skip it)
        i = 1
        while i < len(blocks):
            yaml_block = blocks[i].strip()
            if yaml_block:
                # Try to parse as YAML and check for 'id' key (finding metadata)
                try:
                    finding_meta = yaml.safe_load(yaml_block)
                    if isinstance(finding_meta, dict) and 'id' in finding_meta:
                        # This is a finding YAML block
                        # Next block is the markdown body
                        body = blocks[i + 1] if i + 1 < len(blocks) else ""
                        findings.append((finding_meta, body))
                        i += 2
                        continue
                except yaml.YAMLError:
                    pass
            # Not a finding block, skip
            i += 1

        return project_meta, findings

    def _get_next_finding_id(self, findings: List[Tuple[Dict, str]]) -> str:
        """Generate next finding ID (F001, F002, ...)."""
        if not findings:
            return "F001"

        max_num = 0
        for meta, _ in findings:
            fid = meta.get('id', '')
            if fid.startswith('F') and fid[1:].isdigit():
                num = int(fid[1:])
                max_num = max(max_num, num)

        return f"F{max_num + 1:03d}"

    def _write_findings_file(self, path: Path, project_meta: Dict, findings: List[Tuple[Dict, str]]):
        """Write findings.md with project YAML frontmatter + per-finding YAML blocks."""
        lines = []

        # Project frontmatter
        lines.append("---")
        for key, value in project_meta.items():
            lines.append(f"{key}: {yaml_escape(value)}")
        lines.append("---")
        lines.append("")

        # Header and summary table
        project_name = project_meta.get('project', 'Unknown Project')
        lines.append(f"# Penetration Test Findings: {project_name}")
        lines.append("")
        lines.append("| ID | Severity | Title |")
        lines.append("|----|----------|-------|")

        for meta, _ in findings:
            fid = meta.get('id', '?')
            severity = meta.get('severity', 0)
            title = md_table_escape(meta.get('title', 'Untitled'))
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"
            lines.append(f"| {fid} | {severity} ({sev_label}) | {title} |")

        lines.append("")

        # Per-finding YAML blocks + bodies
        for meta, body in findings:
            lines.append("---")
            # Write finding metadata as YAML
            for key, value in meta.items():
                if key == 'evidence':
                    if value:
                        lines.append("evidence:")
                        for ev in value:
                            lines.append(f"  - type: {yaml_escape(ev.get('type', 'file'))}")
                            lines.append(f"    path: {yaml_escape(ev.get('path', ''))}")
                    else:
                        lines.append("evidence: []")
                else:
                    lines.append(f"{key}: {yaml_escape(value)}")
            lines.append("---")
            lines.append(body.rstrip())
            lines.append("")

        path.write_text("\n".join(lines))

    def _report_list(self) -> bool:
        """List all findings in current project."""
        if not self.findings_project:
            ConsoleHelper.error(self.console, "No project initialized. Use /report init <project> <lang>")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            ConsoleHelper.error(self.console, f"Project file not found: {findings_file}")
            return True

        project_meta, findings = self._parse_findings_file(findings_file)

        if not findings:
            self.console.print(f"[dim]No findings in project: {self.findings_project}[/]")
            return True

        self.console.print(f"[bold]Findings for {self.findings_project}:[/]")
        for meta, _ in findings:
            fid = meta.get('id', '?')
            severity = meta.get('severity', 0)
            title = meta.get('title', 'Untitled')
            sev_color = "red" if severity >= 7 else "yellow" if severity >= 4 else "green"
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"
            self.console.print(f"  [{sev_color}]{fid}[/]  {severity} ({sev_label})  {title}")

        return True

    def _report_edit(self, args: str) -> bool:
        """Open finding for editing."""
        if not self.findings_project:
            ConsoleHelper.error(self.console, "No project initialized.")
            return True

        finding_id = args.strip().upper()
        if not finding_id:
            ConsoleHelper.error(self.console, "Usage: /report edit <id> (e.g., /report edit F001)")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        self.console.print(f"[dim]Edit the findings file directly:[/]")
        self.console.print(f"  {findings_file}")
        return True

    def _report_delete(self, args: str) -> bool:
        """Delete a finding by ID."""
        if not self.findings_project:
            ConsoleHelper.error(self.console, "No project initialized.")
            return True

        finding_id = args.strip().upper()
        if not finding_id:
            ConsoleHelper.error(self.console, "Usage: /report delete <id> (e.g., /report delete F001)")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            ConsoleHelper.error(self.console, "Project file not found")
            return True

        project_meta, findings = self._parse_findings_file(findings_file)

        # Safety check: refuse to modify corrupted files
        if not project_meta:
            ConsoleHelper.error(self.console, "Could not parse project file (invalid format)")
            return True

        # Find the finding to delete (to get evidence paths)
        deleted_finding = None
        new_findings = []
        for m, b in findings:
            if m.get('id', '').upper() == finding_id:
                deleted_finding = m
            else:
                new_findings.append((m, b))

        if deleted_finding is None:
            ConsoleHelper.warning(self.console, f"Finding {finding_id} not found")
            return True

        # Delete associated evidence files
        evidence_dir = project_dir / "evidence"
        if evidence_dir.exists():
            for ev in deleted_finding.get('evidence', []):
                ev_path = project_dir / ev.get('path', '')
                if ev_path.exists():
                    try:
                        ev_path.unlink()
                    except Exception:
                        pass  # Best effort deletion

        self._write_findings_file(findings_file, project_meta, new_findings)
        ConsoleHelper.success(self.console, f"Deleted {finding_id}")
        return True

    def _report_set_severity(self, args: str) -> bool:
        """Override severity for a finding."""
        if not self.findings_project:
            ConsoleHelper.error(self.console, "No project initialized.")
            return True

        parts = args.strip().split()
        if len(parts) != 2:
            ConsoleHelper.error(self.console, "Usage: /report severity <id> <1-9> (e.g., /report severity F001 8)")
            return True

        finding_id = parts[0].upper()
        try:
            new_severity = int(parts[1])
            if not 1 <= new_severity <= 9:
                raise ValueError()
        except ValueError:
            ConsoleHelper.error(self.console, "Severity must be 1-9")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            ConsoleHelper.error(self.console, "Project file not found")
            return True

        project_meta, findings = self._parse_findings_file(findings_file)

        # Safety check: refuse to modify corrupted files
        if not project_meta:
            ConsoleHelper.error(self.console, "Could not parse project file (invalid format)")
            return True

        # Find and update the finding
        found = False
        for meta, body in findings:
            if meta.get('id', '').upper() == finding_id:
                meta['severity'] = new_severity
                meta['severity_rationale'] = f"Manually set to {new_severity}"
                found = True
                break

        if not found:
            ConsoleHelper.warning(self.console, f"Finding {finding_id} not found")
            return True

        self._write_findings_file(findings_file, project_meta, findings)
        sev_color = "red" if new_severity >= 7 else "yellow" if new_severity >= 4 else "green"
        ConsoleHelper.success(self.console, f"Updated {finding_id} severity to [{sev_color}]{new_severity}[/]")
        return True

    def _report_projects(self) -> bool:
        """List all finding projects."""
        if not self.findings_base_dir.exists():
            self.console.print("[dim]No projects found[/]")
            return True

        projects = [d for d in self.findings_base_dir.iterdir() if d.is_dir()]
        if not projects:
            self.console.print("[dim]No projects found. Use /report init <name> <lang> to create one.[/]")
            return True

        self.console.print("[bold]Finding Projects:[/]")
        for project_dir in sorted(projects):
            name = project_dir.name
            findings_file = project_dir / "findings.md"
            count = 0
            if findings_file.exists():
                _, findings = self._parse_findings_file(findings_file)
                count = len(findings)

            active = " [green](active)[/]" if name == self.findings_project else ""
            self.console.print(f"  {name}{active} - {count} findings")

        return True

    def _report_open(self, project_name: str) -> bool:
        """Switch to an existing project."""
        project_name = project_name.strip()
        if not project_name:
            ConsoleHelper.error(self.console, "Usage: /report open <project-name>")
            return True

        project_dir = self.findings_base_dir / project_name

        if not project_dir.exists():
            ConsoleHelper.error(self.console, f"Project not found: {project_name}")
            self.console.print("[dim]Use /report projects to list available projects[/]")
            return True

        self.findings_project = project_name
        ConsoleHelper.success(self.console, f"Switched to project: {project_name}")
        return True

    def _report_export(self, args: str) -> bool:
        """Export findings to Word document via pandoc."""
        if not self.findings_project:
            ConsoleHelper.error(self.console, "No project initialized.")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            ConsoleHelper.error(self.console, "Project file not found")
            return True

        # Check pandoc is available
        try:
            subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            ConsoleHelper.error(self.console, "pandoc not found. Install with: apt install pandoc")
            return True

        # Create export file (strip per-finding YAML for clean Word output)
        project_meta, findings = self._parse_findings_file(findings_file)

        # Build clean markdown for export (no per-finding YAML)
        export_lines = []
        export_lines.append(f"# Penetration Test Findings: {project_meta.get('project', 'Unknown')}")
        export_lines.append("")
        export_lines.append(f"**Date:** {project_meta.get('created', 'Unknown')}")
        export_lines.append(f"**Assessor:** {project_meta.get('assessor', 'Unknown')}")
        export_lines.append("")

        # Summary table
        export_lines.append("## Summary")
        export_lines.append("")
        export_lines.append("| ID | Severity | Title |")
        export_lines.append("|----|----------|-------|")
        for meta, _ in findings:
            fid = meta.get('id', '?')
            severity = meta.get('severity', 0)
            title = md_table_escape(meta.get('title', 'Untitled'))
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"
            export_lines.append(f"| {fid} | {severity} ({sev_label}) | {title} |")
        export_lines.append("")

        # Each finding
        for meta, body in findings:
            fid = meta.get('id', '?')
            title = meta.get('title', 'Untitled')
            severity = meta.get('severity', 0)
            rationale = meta.get('severity_rationale', '')
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"

            export_lines.append(f"## {fid}: {title}")
            export_lines.append("")
            export_lines.append(f"**Severity:** {severity} ({sev_label}) - {rationale}")
            export_lines.append(body)
            export_lines.append("")

        # Write temp file and convert
        export_md = project_dir / "findings_export.md"
        export_md.write_text("\n".join(export_lines))

        output_file = project_dir / "findings.docx"

        # Check for custom template
        template_file = get_config_dir() / "pentest-template.docx"
        cmd = ["pandoc", str(export_md), "-o", str(output_file)]
        if template_file.exists():
            cmd.extend(["--reference-doc", str(template_file)])

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            ConsoleHelper.success(self.console, f"Exported to: {output_file}")
        except subprocess.CalledProcessError as e:
            ConsoleHelper.error(self.console, f"Export failed: {e.stderr.decode() if e.stderr else str(e)}")
        finally:
            # Clean up temp file
            if export_md.exists():
                export_md.unlink()

        return True
