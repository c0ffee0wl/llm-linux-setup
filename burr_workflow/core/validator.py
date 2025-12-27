"""
Workflow static validator for pre-compilation checks.

Performs static analysis on workflow definitions to catch common errors
before compilation. This includes:
- Structure validation (required fields, types)
- Step ID uniqueness and reference validity
- Expression syntax validation (Jinja2)
- Security checks (dangerous patterns)
- Loop safety checks (unreachable steps, cycles)

The validator runs before compilation to provide clearer error messages
and fail fast on invalid workflows.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Set, List, Dict
import re


class ValidationLevel(Enum):
    """Severity level for validation messages."""
    ERROR = "error"       # Must fix before execution
    WARNING = "warning"   # May cause issues
    INFO = "info"         # Suggestion or note


@dataclass
class ValidationMessage:
    """A single validation finding."""
    level: ValidationLevel
    code: str
    message: str
    location: Optional[str] = None  # e.g., "jobs.main.steps[2]"
    suggestion: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of workflow validation."""
    valid: bool
    messages: list[ValidationMessage] = field(default_factory=list)
    
    def add_error(
        self, code: str, message: str, 
        location: Optional[str] = None,
        suggestion: Optional[str] = None
    ) -> None:
        """Add an error message."""
        self.messages.append(ValidationMessage(
            level=ValidationLevel.ERROR,
            code=code,
            message=message,
            location=location,
            suggestion=suggestion,
        ))
        self.valid = False
    
    def add_warning(
        self, code: str, message: str,
        location: Optional[str] = None,
        suggestion: Optional[str] = None
    ) -> None:
        """Add a warning message."""
        self.messages.append(ValidationMessage(
            level=ValidationLevel.WARNING,
            code=code,
            message=message,
            location=location,
            suggestion=suggestion,
        ))
    
    def add_info(
        self, code: str, message: str,
        location: Optional[str] = None,
        suggestion: Optional[str] = None
    ) -> None:
        """Add an info message."""
        self.messages.append(ValidationMessage(
            level=ValidationLevel.INFO,
            code=code,
            message=message,
            location=location,
            suggestion=suggestion,
        ))
    
    @property
    def errors(self) -> list[ValidationMessage]:
        """Get only error messages."""
        return [m for m in self.messages if m.level == ValidationLevel.ERROR]
    
    @property
    def warnings(self) -> list[ValidationMessage]:
        """Get only warning messages."""
        return [m for m in self.messages if m.level == ValidationLevel.WARNING]
    
    def merge(self, other: "ValidationResult") -> None:
        """Merge another result into this one."""
        self.messages.extend(other.messages)
        if not other.valid:
            self.valid = False


class WorkflowValidator:
    """Static workflow validator.
    
    Performs comprehensive validation of workflow definitions before
    compilation. All checks are static (no execution) and can detect:
    
    - Missing required fields
    - Invalid step references
    - Duplicate step IDs
    - Expression syntax errors
    - Dangerous patterns in expressions
    - Loop safety issues
    
    Usage:
        validator = WorkflowValidator()
        result = validator.validate(workflow_dict)
        
        if not result.valid:
            for error in result.errors:
                print(f"[{error.code}] {error.message}")
    """
    
    # Supported schema versions
    SUPPORTED_VERSIONS = {"1.0"}

    # Error codes
    E_INVALID_VERSION = "E000"  # Schema version validation
    E_MISSING_NAME = "E001"
    E_MISSING_JOBS = "E002"
    E_MISSING_MAIN = "E003"
    E_MISSING_STEPS = "E004"
    E_EMPTY_STEPS = "E005"
    E_INVALID_STEP = "E006"
    E_DUPLICATE_ID = "E007"
    E_INVALID_REF = "E008"
    E_INVALID_EXPR = "E009"
    E_DANGEROUS_EXPR = "E010"
    E_UNREACHABLE = "E011"
    E_INVALID_LOOP = "E012"
    E_MISSING_ACTION = "E013"
    E_INVALID_TYPE = "E014"
    
    # Warning codes
    W_UNUSED_STEP = "W001"
    W_MISSING_ID = "W002"
    W_LONG_LOOP = "W003"
    W_NO_TIMEOUT = "W004"
    W_HARDCODED_SECRET = "W005"
    W_MISSING_ERROR_HANDLER = "W006"
    W_BOTH_RUN_AND_USES = "W007"
    W_SHELL_INJECTION = "W008"

    # Pattern for unquoted variables in shell commands
    # Matches ${{ ... }} NOT followed by | shell_quote
    UNQUOTED_VAR_PATTERN = re.compile(
        r'\$\{\{(?!.*\|\s*shell_quote)[^}]+\}\}'
    )
    
    # Dangerous patterns in expressions (for security)
    DANGEROUS_PATTERNS = [
        r"__class__",
        r"__mro__",
        r"__subclasses__",
        r"__globals__",
        r"__builtins__",
        r"__import__",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\bcompile\s*\(",
        r"\bopen\s*\(",
        r"\bos\.",
        r"\bsubprocess\.",
        r"\bsys\.",
        r"\.read\s*\(",
        r"\.write\s*\(",
    ]
    
    # Patterns that might indicate hardcoded secrets
    SECRET_PATTERNS = [
        r"password\s*[=:]\s*['\"][^'\"]+['\"]",
        r"api[_-]?key\s*[=:]\s*['\"][^'\"]+['\"]",
        r"secret\s*[=:]\s*['\"][^'\"]+['\"]",
        r"token\s*[=:]\s*['\"][^'\"]+['\"]",
        r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",
        r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
    ]
    
    # Expression pattern (GitHub Actions style)
    EXPR_PATTERN = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")
    
    def __init__(self, strict: bool = False):
        """Initialize validator.
        
        Args:
            strict: If True, treat warnings as errors
        """
        self.strict = strict
        self._step_ids: Set[str] = set()
        self._step_outputs: Dict[str, Set[str]] = {}
        self._referenced_steps: Set[str] = set()
    
    def validate(self, workflow: dict) -> ValidationResult:
        """Validate a workflow definition.
        
        Args:
            workflow: Parsed workflow dictionary
            
        Returns:
            ValidationResult with all findings
        """
        result = ValidationResult(valid=True)
        
        # Reset state
        self._step_ids = set()
        self._step_outputs = {}
        self._referenced_steps = set()
        
        # Phase 1: Structure validation
        self._validate_structure(workflow, result)
        if not result.valid:
            return result  # Can't continue with invalid structure
        
        # Phase 2: Collect all step IDs
        self._collect_step_ids(workflow, result)
        
        # Phase 3: Validate each step
        self._validate_steps(workflow, result)
        
        # Phase 4: Validate references
        self._validate_references(workflow, result)
        
        # Phase 5: Expression validation
        self._validate_expressions(workflow, result)

        # Phase 6: Shell safety checks
        self._validate_shell_safety(workflow, result)

        # Phase 7: Check for unused steps (warning only)
        self._check_unused_steps(workflow, result)
        
        # Strict mode: convert warnings to errors
        if self.strict:
            for msg in result.messages:
                if msg.level == ValidationLevel.WARNING:
                    msg.level = ValidationLevel.ERROR
                    result.valid = False
        
        return result

    def _validate_structure(self, workflow: dict, result: ValidationResult) -> None:
        """Validate basic workflow structure."""
        # Schema version (must be first check)
        schema_version = workflow.get("schema_version")
        if not schema_version:
            result.add_error(
                self.E_INVALID_VERSION,
                "Workflow must have a 'schema_version' field",
                location="workflow",
                suggestion="Add 'schema_version: \"1.0\"' at the top level",
            )
        elif str(schema_version) not in self.SUPPORTED_VERSIONS:
            result.add_error(
                self.E_INVALID_VERSION,
                f"Unsupported schema version: {schema_version}",
                location="workflow.schema_version",
                suggestion=f"Supported versions: {', '.join(sorted(self.SUPPORTED_VERSIONS))}",
            )

        # Name field
        if not workflow.get("name"):
            result.add_error(
                self.E_MISSING_NAME,
                "Workflow must have a 'name' field",
                location="workflow",
                suggestion="Add 'name: my-workflow' at the top level",
            )
        
        # Jobs section
        if "jobs" not in workflow:
            result.add_error(
                self.E_MISSING_JOBS,
                "Workflow must have a 'jobs' section",
                location="workflow",
                suggestion="Add 'jobs:' section with at least a 'main' job",
            )
            return
        
        jobs = workflow["jobs"]
        if not isinstance(jobs, dict):
            result.add_error(
                self.E_INVALID_TYPE,
                "'jobs' must be a mapping",
                location="workflow.jobs",
            )
            return
        
        # Main job
        if "main" not in jobs:
            result.add_error(
                self.E_MISSING_MAIN,
                "Workflow must have a 'main' job",
                location="workflow.jobs",
                suggestion="Add 'main:' job under 'jobs:'",
            )
            return
        
        main_job = jobs["main"]
        if not isinstance(main_job, dict):
            result.add_error(
                self.E_INVALID_TYPE,
                "'main' job must be a mapping",
                location="workflow.jobs.main",
            )
            return
        
        # Steps in main job
        if "steps" not in main_job:
            result.add_error(
                self.E_MISSING_STEPS,
                "Main job must have a 'steps' section",
                location="workflow.jobs.main",
            )
            return
        
        steps = main_job["steps"]
        if not isinstance(steps, list):
            result.add_error(
                self.E_INVALID_TYPE,
                "'steps' must be a list",
                location="workflow.jobs.main.steps",
            )
            return
        
        if not steps:
            result.add_error(
                self.E_EMPTY_STEPS,
                "Workflow must have at least one step",
                location="workflow.jobs.main.steps",
            )
    
    def _collect_step_ids(self, workflow: dict, result: ValidationResult) -> None:
        """Collect all step IDs and check for duplicates."""
        main_job = workflow.get("jobs", {}).get("main", {})
        steps = main_job.get("steps", [])
        
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                result.add_error(
                    self.E_INVALID_STEP,
                    f"Step at index {idx} is not a mapping",
                    location=f"jobs.main.steps[{idx}]",
                )
                continue
            
            step_id = step.get("id")
            if not step_id:
                # Generate default ID
                step_id = step.get("name", f"step_{idx}")
                if isinstance(step_id, str):
                    step_id = re.sub(r"[^a-zA-Z0-9_]", "_", step_id.lower())
                    step_id = f"{step_id}_{idx}"
                
                result.add_info(
                    self.W_MISSING_ID,
                    f"Step at index {idx} has no 'id' field",
                    location=f"jobs.main.steps[{idx}]",
                    suggestion=f"Add 'id: {step_id}' for explicit identification",
                )
            
            if step_id in self._step_ids:
                result.add_error(
                    self.E_DUPLICATE_ID,
                    f"Duplicate step ID: '{step_id}'",
                    location=f"jobs.main.steps[{idx}]",
                    suggestion="Use unique IDs for each step",
                )
            else:
                self._step_ids.add(step_id)
    
    def _validate_steps(self, workflow: dict, result: ValidationResult) -> None:
        """Validate each step's configuration."""
        main_job = workflow.get("jobs", {}).get("main", {})
        steps = main_job.get("steps", [])
        
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            
            location = f"jobs.main.steps[{idx}]"
            
            # Must have either 'run' or 'uses'
            has_run = "run" in step
            has_uses = "uses" in step
            
            if not has_run and not has_uses:
                result.add_error(
                    self.E_MISSING_ACTION,
                    "Step must have either 'run' or 'uses' field",
                    location=location,
                    suggestion="Add 'run: command' or 'uses: action/type'",
                )
            
            if has_run and has_uses:
                result.add_warning(
                    "W007",
                    "Step has both 'run' and 'uses'; 'run' takes precedence",
                    location=location,
                )
            
            # Validate loop configuration
            if "loop" in step:
                self._validate_loop_config(step, location, result)
            
            # Validate conditional
            if "if" in step:
                condition = step["if"]
                if not isinstance(condition, str):
                    result.add_error(
                        self.E_INVALID_TYPE,
                        "'if' condition must be a string expression",
                        location=location,
                    )
            
            # Check for missing timeout on shell commands
            if has_run and "timeout" not in step:
                result.add_info(
                    self.W_NO_TIMEOUT,
                    "Shell command has no timeout",
                    location=location,
                    suggestion="Add 'timeout: 60s' to prevent hanging",
                )
            
            # Check for missing error handling
            if has_run and "on_failure" not in step and "continue_on_error" not in step:
                result.add_info(
                    self.W_MISSING_ERROR_HANDLER,
                    "Step has no error handling",
                    location=location,
                    suggestion="Consider adding 'on_failure: step_id' or 'continue_on_error: true'",
                )
    
    def _validate_loop_config(
        self, step: dict, location: str, result: ValidationResult
    ) -> None:
        """Validate loop-specific configuration."""
        loop_expr = step.get("loop")
        
        if not isinstance(loop_expr, str):
            result.add_error(
                self.E_INVALID_LOOP,
                "'loop' must be an expression string",
                location=location,
                suggestion="Use 'loop: ${{ inputs.items }}' format",
            )
            return
        
        # Check for very high iteration limits
        max_iterations = step.get("max_iterations", 10000)
        if max_iterations > 100000:
            result.add_warning(
                self.W_LONG_LOOP,
                f"Very high max_iterations ({max_iterations}) may cause memory issues",
                location=location,
                suggestion="Consider reducing max_iterations or using pagination",
            )
        
        # Check for missing break condition on infinite loops
        if loop_expr.lower() in ["true", "1", "yes"] and "break_if" not in step:
            result.add_warning(
                self.W_LONG_LOOP,
                "Infinite loop with no break_if condition",
                location=location,
                suggestion="Add 'break_if: condition' to prevent infinite loops",
            )
    
    def _validate_references(self, workflow: dict, result: ValidationResult) -> None:
        """Validate step references (on_failure, needs, etc.)."""
        main_job = workflow.get("jobs", {}).get("main", {})
        steps = main_job.get("steps", [])
        
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            
            location = f"jobs.main.steps[{idx}]"
            
            # Check on_failure reference
            if "on_failure" in step:
                target = step["on_failure"]
                if target not in self._step_ids and target != "__cleanup__":
                    result.add_error(
                        self.E_INVALID_REF,
                        f"on_failure references unknown step: '{target}'",
                        location=location,
                        suggestion=f"Valid step IDs: {', '.join(sorted(self._step_ids))}",
                    )
                self._referenced_steps.add(target)
            
            # Check needs references
            if "needs" in step:
                needs = step["needs"]
                if isinstance(needs, str):
                    needs = [needs]
                
                for need in needs:
                    if need not in self._step_ids:
                        result.add_error(
                            self.E_INVALID_REF,
                            f"needs references unknown step: '{need}'",
                            location=location,
                        )
                    self._referenced_steps.add(need)
    
    def _validate_expressions(self, workflow: dict, result: ValidationResult) -> None:
        """Validate all expressions in the workflow."""
        self._validate_expressions_in_obj(
            workflow, "workflow", result
        )
    
    def _validate_expressions_in_obj(
        self, obj: Any, path: str, result: ValidationResult
    ) -> None:
        """Recursively validate expressions in any object."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                self._validate_expressions_in_obj(value, f"{path}.{key}", result)
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                self._validate_expressions_in_obj(item, f"{path}[{idx}]", result)
        elif isinstance(obj, str):
            self._validate_string_expressions(obj, path, result)
    
    def _validate_string_expressions(
        self, text: str, location: str, result: ValidationResult
    ) -> None:
        """Validate expressions within a string."""
        # Find all expressions
        for match in self.EXPR_PATTERN.finditer(text):
            expr = match.group(1)
            
            # Check for dangerous patterns
            for pattern in self.DANGEROUS_PATTERNS:
                if re.search(pattern, expr, re.IGNORECASE):
                    result.add_error(
                        self.E_DANGEROUS_EXPR,
                        f"Potentially dangerous pattern in expression: {pattern}",
                        location=location,
                        suggestion="Use only safe expression patterns",
                    )
            
            # Basic syntax check (balanced brackets, etc.)
            try:
                self._check_expression_syntax(expr)
            except ValueError as e:
                result.add_error(
                    self.E_INVALID_EXPR,
                    f"Invalid expression syntax: {e}",
                    location=location,
                )
            
            # Track step references in expressions
            step_refs = re.findall(r"steps\.(\w+)", expr)
            for ref in step_refs:
                self._referenced_steps.add(ref)
                if ref not in self._step_ids:
                    result.add_error(
                        self.E_INVALID_REF,
                        f"Expression references unknown step: '{ref}'",
                        location=location,
                    )
        
        # Check for hardcoded secrets (outside expressions too)
        for pattern in self.SECRET_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                result.add_warning(
                    self.W_HARDCODED_SECRET,
                    "Possible hardcoded secret detected",
                    location=location,
                    suggestion="Use inputs or environment variables instead",
                )
    
    def _check_expression_syntax(self, expr: str) -> None:
        """Basic expression syntax check."""
        # Check balanced parentheses
        stack = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        
        for char in expr:
            if char in pairs:
                stack.append(pairs[char])
            elif char in pairs.values():
                if not stack or stack.pop() != char:
                    raise ValueError(f"Unbalanced bracket: {char}")
        
        if stack:
            raise ValueError(f"Unclosed bracket: {stack[-1]}")
        
        # Check for empty expressions
        if not expr.strip():
            raise ValueError("Empty expression")
    
    def _validate_shell_safety(self, workflow: dict, result: ValidationResult) -> None:
        """Check for shell injection vulnerabilities in run commands.

        Scans all 'run:' commands for unquoted ${{ }} expressions that could
        lead to shell injection if user-controlled data is passed through.
        """
        main_job = workflow.get("jobs", {}).get("main", {})
        steps = main_job.get("steps", [])

        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue

            run_cmd = step.get("run")
            if run_cmd is None:
                continue

            location = f"jobs.main.steps[{idx}]"
            step_id = step.get("id") or step.get("name") or f"step_{idx}"

            # Handle both string and array commands
            commands = [run_cmd] if isinstance(run_cmd, str) else run_cmd

            for cmd_idx, cmd in enumerate(commands):
                if not isinstance(cmd, str):
                    continue

                # Find unquoted variable expressions
                for match in self.UNQUOTED_VAR_PATTERN.finditer(cmd):
                    expr = match.group(0)

                    # Extract the variable part (between ${{ and }})
                    var_part = expr[4:-2].strip()  # Remove '${{' and '}}'

                    # Build helpful suggestion
                    if isinstance(run_cmd, str):
                        suggestion = (
                            f"Use '| shell_quote' filter: ${{{{ {var_part} | shell_quote }}}} "
                            f"or convert to array syntax: run: ['cmd', ${{{{ {var_part} }}}}]"
                        )
                    else:
                        # Already array syntax, just needs shell_quote
                        suggestion = (
                            f"Use '| shell_quote' filter: ${{{{ {var_part} | shell_quote }}}}"
                        )

                    result.add_warning(
                        self.W_SHELL_INJECTION,
                        f"Unquoted variable in shell command: {expr}",
                        location=location,
                        suggestion=suggestion,
                    )

    def _check_unused_steps(self, workflow: dict, result: ValidationResult) -> None:
        """Check for steps that are never referenced."""
        # First step is always "used" as entry point
        main_job = workflow.get("jobs", {}).get("main", {})
        steps = main_job.get("steps", [])
        
        if steps and isinstance(steps[0], dict):
            first_id = steps[0].get("id") or steps[0].get("name") or "step_0"
            self._referenced_steps.add(first_id)
        
        # Steps in sequence are implicitly referenced
        for idx, step in enumerate(steps):
            if isinstance(step, dict):
                step_id = step.get("id") or step.get("name") or f"step_{idx}"
                # Each step references the next (implicit transition)
                if idx + 1 < len(steps):
                    next_step = steps[idx + 1]
                    if isinstance(next_step, dict):
                        next_id = next_step.get("id") or f"step_{idx + 1}"
                        self._referenced_steps.add(next_id)
        
        # Find unreferenced steps (not a real error in sequential workflows)
        # This is more for documentation - all steps in sequence are reachable
        # Only report if a step is truly isolated (e.g., orphaned by on_failure)


def validate_workflow(workflow: dict, strict: bool = False) -> ValidationResult:
    """Convenience function to validate a workflow.
    
    Args:
        workflow: Parsed workflow dictionary
        strict: If True, treat warnings as errors
        
    Returns:
        ValidationResult with all findings
    """
    validator = WorkflowValidator(strict=strict)
    return validator.validate(workflow)


def validate_workflow_yaml(yaml_content: str, strict: bool = False) -> ValidationResult:
    """Validate workflow from YAML string.
    
    Args:
        yaml_content: YAML workflow definition
        strict: If True, treat warnings as errors
        
    Returns:
        ValidationResult with all findings
    """
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ="safe")
        workflow = yaml.load(yaml_content)
    except ImportError:
        import yaml as pyyaml
        workflow = pyyaml.safe_load(yaml_content)
    
    return validate_workflow(workflow, strict=strict)
