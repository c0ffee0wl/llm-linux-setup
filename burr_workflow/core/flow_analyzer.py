"""
Static flow analyzer for workflow definitions.

Provides dry-run analysis showing execution flow, conditionals,
loops, and data dependencies without actually executing anything.
"""

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepAnalysis:
    """Analysis result for a single step."""

    step_id: str
    step_name: str | None
    step_type: str  # "run", "uses", "loop", "human", "script"
    action: str  # command or action type
    condition: str | None  # if: expression
    loop_expr: str | None  # loop: expression
    max_iterations: int | None  # loop max_iterations
    dependencies: set[str] = field(default_factory=set)  # step IDs this depends on
    next_steps: list[str] = field(default_factory=list)  # possible next step IDs
    is_conditional: bool = False
    is_loop: bool = False
    on_failure: str | None = None  # on_failure target


@dataclass
class FlowAnalysisResult:
    """Complete workflow flow analysis."""

    workflow_name: str
    workflow_version: str | None
    steps: list[StepAnalysis]
    data_dependencies: dict[str, set[str]]  # step_id → set of step_ids it reads from
    total_steps: int
    conditional_count: int
    loop_count: int
    has_finally: bool = False


class FlowAnalyzer:
    """Static workflow flow analyzer.

    Analyzes a workflow definition to extract:
    - Step execution order
    - Conditional branches
    - Loop structures
    - Data dependencies between steps

    Example:
        analyzer = FlowAnalyzer(workflow_dict)
        result = analyzer.analyze()
        print(f"Total steps: {result.total_steps}")
        print(f"Conditionals: {result.conditional_count}")
    """

    # Regex to extract step references: steps.X.outputs.Y or steps.X.outputs
    STEP_REF_PATTERN = re.compile(r"steps\.(\w+)(?:\.outputs)?")

    def __init__(self, workflow: dict[str, Any]):
        """Initialize analyzer with workflow definition.

        Args:
            workflow: Parsed workflow YAML as dictionary
        """
        self.workflow = workflow
        self.step_ids: list[str] = []
        self.step_map: dict[str, dict] = {}

    def analyze(self) -> FlowAnalysisResult:
        """Perform static flow analysis.

        Returns:
            FlowAnalysisResult with complete analysis
        """
        # Extract workflow metadata
        name = self.workflow.get("name", "unnamed")
        version = self.workflow.get("version")

        # Get steps from main job (support both 'jobs' and flat 'steps')
        steps_list = self._get_steps()

        # Build step ID list and map
        for i, step in enumerate(steps_list):
            step_id = step.get("id", f"step_{i + 1}")
            self.step_ids.append(step_id)
            self.step_map[step_id] = step

        # Analyze each step
        analyzed_steps: list[StepAnalysis] = []
        for i, step_id in enumerate(self.step_ids):
            step = self.step_map[step_id]
            analysis = self._analyze_step(step, i, len(self.step_ids))
            analyzed_steps.append(analysis)

        # Build data dependencies map
        data_deps: dict[str, set[str]] = {}
        for step_analysis in analyzed_steps:
            if step_analysis.dependencies:
                data_deps[step_analysis.step_id] = step_analysis.dependencies

        # Check for finally block
        has_finally = bool(self.workflow.get("finally"))

        # Count conditionals and loops
        conditional_count = sum(1 for s in analyzed_steps if s.is_conditional)
        loop_count = sum(1 for s in analyzed_steps if s.is_loop)

        return FlowAnalysisResult(
            workflow_name=name,
            workflow_version=version,
            steps=analyzed_steps,
            data_dependencies=data_deps,
            total_steps=len(analyzed_steps),
            conditional_count=conditional_count,
            loop_count=loop_count,
            has_finally=has_finally,
        )

    def _get_steps(self) -> list[dict]:
        """Get steps from workflow definition.

        Supports both:
        - jobs.main.steps format
        - flat steps format (deprecated but supported)
        """
        jobs = self.workflow.get("jobs", {})
        if jobs:
            # Use main job by default
            main_job = jobs.get("main", {})
            return main_job.get("steps", [])

        # Fallback to flat steps
        return self.workflow.get("steps", [])

    def _analyze_step(self, step: dict, index: int, total: int) -> StepAnalysis:
        """Analyze a single step.

        Args:
            step: Step definition dictionary
            index: Step index (0-based)
            total: Total number of steps

        Returns:
            StepAnalysis with extracted information
        """
        step_id = step.get("id", f"step_{index + 1}")
        step_name = step.get("name")

        # Determine step type and action
        step_type, action = self._get_step_type_and_action(step)

        # Check for condition
        condition = step.get("if")
        is_conditional = condition is not None

        # Check for loop
        loop_expr = step.get("loop")
        is_loop = loop_expr is not None
        max_iterations = step.get("max_iterations", 10000) if is_loop else None

        # Extract dependencies
        dependencies = self._extract_dependencies(step)

        # Determine next steps
        next_steps = self._get_next_steps(step, index, total, is_conditional)

        # Check for on_failure handler
        on_failure = step.get("on_failure")

        return StepAnalysis(
            step_id=step_id,
            step_name=step_name,
            step_type=step_type,
            action=action,
            condition=condition,
            loop_expr=loop_expr,
            max_iterations=max_iterations,
            dependencies=dependencies,
            next_steps=next_steps,
            is_conditional=is_conditional,
            is_loop=is_loop,
            on_failure=on_failure,
        )

    def _get_step_type_and_action(self, step: dict) -> tuple[str, str]:
        """Extract step type and action from step definition.

        Returns:
            Tuple of (step_type, action_description)
        """
        # Check for run: shell command
        if "run" in step:
            run_cmd = step["run"]
            if isinstance(run_cmd, list):
                action = " ".join(str(a) for a in run_cmd)
            else:
                action = str(run_cmd)
            return ("run", action)

        # Check for uses: action type
        if "uses" in step:
            uses = step["uses"]
            with_args = step.get("with", {})

            # Format action with key parameters
            if uses.startswith("llm/"):
                # Show schema type for llm actions
                schema = with_args.get("schema", {})
                if schema:
                    action = f"{uses} → {schema.get('type', 'object')}"
                else:
                    action = uses
            elif uses.startswith("http/"):
                # Show method and URL pattern
                method = with_args.get("method", "GET")
                url = with_args.get("url", "")
                if len(url) > 40:
                    url = url[:40] + "..."
                action = f"{uses} {method} {url}"
            elif uses == "human/input":
                prompt = with_args.get("prompt", "")
                if len(prompt) > 40:
                    prompt = prompt[:40] + "..."
                action = f"{uses}: {prompt}"
            elif uses.startswith("script/"):
                action = uses
            else:
                action = uses

            return ("uses", action)

        # Fallback for unknown step types
        return ("unknown", "???")

    def _extract_dependencies(self, step: dict) -> set[str]:
        """Extract step IDs referenced in expressions.

        Scans all string values in step for patterns like:
        - steps.scan.outputs.stdout
        - steps.analyze.outputs

        Args:
            step: Step definition dictionary

        Returns:
            Set of step IDs this step depends on
        """
        deps: set[str] = set()

        for value in self._iter_strings(step):
            for match in self.STEP_REF_PATTERN.finditer(value):
                ref_step_id = match.group(1)
                # Only add if it's a valid step ID (not 'loop' or other keywords)
                if ref_step_id in self.step_ids:
                    deps.add(ref_step_id)

        return deps

    def _iter_strings(self, obj: Any, max_depth: int = 10) -> list[str]:
        """Recursively extract all strings from a nested structure.

        Args:
            obj: Object to scan (dict, list, or primitive)
            max_depth: Maximum recursion depth

        Returns:
            List of all string values found
        """
        if max_depth <= 0:
            return []

        strings: list[str] = []

        if isinstance(obj, str):
            strings.append(obj)
        elif isinstance(obj, dict):
            for value in obj.values():
                strings.extend(self._iter_strings(value, max_depth - 1))
        elif isinstance(obj, list):
            for item in obj:
                strings.extend(self._iter_strings(item, max_depth - 1))

        return strings

    def _get_next_steps(
        self, step: dict, index: int, total: int, is_conditional: bool
    ) -> list[str]:
        """Determine possible next steps.

        Args:
            step: Step definition
            index: Current step index
            total: Total number of steps
            is_conditional: Whether this step has a condition

        Returns:
            List of possible next step IDs
        """
        next_steps: list[str] = []

        # Check for explicit 'next' field (if supported)
        explicit_next = step.get("next")
        if explicit_next:
            if isinstance(explicit_next, list):
                next_steps.extend(explicit_next)
            else:
                next_steps.append(explicit_next)
            return next_steps

        # Default: next step in sequence, or __cleanup__ if last
        if index + 1 < total:
            next_step_id = self.step_ids[index + 1]
            next_steps.append(next_step_id)
        else:
            # Last step goes to cleanup/end
            if self.workflow.get("finally"):
                next_steps.append("__finally__")
            else:
                next_steps.append("__end__")

        return next_steps
