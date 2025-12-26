"""
Script runner actions for Python and Bash.

This module provides actions that run scripts in isolated subprocesses,
enabling workflow steps to run arbitrary code safely.
"""

import asyncio
import os
import tempfile
from typing import Any, Optional, TYPE_CHECKING

from .base import BaseAction
from ..core.types import ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


async def _run_script_subprocess(
    interpreter: str,
    script_path: str,
    env: dict,
    timeout: float,
) -> tuple[int, str, str]:
    """
    Run a script file using the specified interpreter.

    Uses asyncio.create_subprocess_exec (NOT shell) for safety.

    Args:
        interpreter: Path to interpreter (e.g., "python3", "/bin/bash")
        script_path: Path to script file
        env: Environment variables
        timeout: Timeout in seconds

    Returns:
        Tuple of (return_code, stdout, stderr)

    Raises:
        asyncio.TimeoutError: If script exceeds timeout
        FileNotFoundError: If interpreter not found
    """
    # Create subprocess using interpreter + script path (no shell)
    process = await asyncio.create_subprocess_exec(
        interpreter,
        script_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    return process.returncode, stdout, stderr


class PythonScriptAction(BaseAction):
    """
    Run Python code in a subprocess.

    The code is written to a temporary file and run via python3.
    Input variables can be passed via environment variables.

    YAML usage (inline):
        - id: process_data
          uses: script/python
          with:
            code: |
              import json
              import os
              data = json.loads(os.environ.get('INPUT_DATA', '{}'))
              result = {"count": len(data)}
              print(json.dumps(result))
            env:
              INPUT_DATA: "${{ steps.fetch.outputs.response }}"
            timeout: 60

    YAML usage (file-based):
        - id: analyze
          uses: script/python
          with:
            path: scripts/analyze.py
            env:
              TARGET: "${{ inputs.target }}"
            timeout: 120

    Outputs:
        - stdout: Standard output from the script
        - stderr: Standard error from the script
        - exit_code: Process exit code (0 = success)
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Run Python script in subprocess."""
        with_config = step_config.get("with", {})
        code = with_config.get("code", "")
        script_path_config = with_config.get("path", "")
        script_env = with_config.get("env", {})
        timeout = with_config.get("timeout", 30)

        # Load code from file if path is specified
        if script_path_config and not code:
            try:
                script_file = os.path.expanduser(script_path_config)
                if not os.path.isabs(script_file):
                    # Resolve relative to workflow workspace or cwd
                    workspace = context.get("env", {}).get("WORKSPACE", os.getcwd())
                    script_file = os.path.join(workspace, script_file)
                with open(script_file, "r", encoding="utf-8") as f:
                    code = f.read()
            except FileNotFoundError:
                return ActionResult(
                    outcome="failure",
                    outputs={},
                    error=f"Script file not found: {script_path_config}",
                    error_type="FileNotFoundError",
                )
            except PermissionError:
                return ActionResult(
                    outcome="failure",
                    outputs={},
                    error=f"Permission denied reading script: {script_path_config}",
                    error_type="PermissionError",
                )

        if not code:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'code' or 'path' provided for script/python action",
                error_type="ValidationError",
            )

        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid timeout value: {timeout}. Must be positive number.",
                error_type="ValidationError",
            )

        # Build environment: inherit current env + script-specific vars
        env = dict(os.environ)
        for key, value in script_env.items():
            env[key] = str(value) if value is not None else ""

        script_path = None
        try:
            # Write code to temp file
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                encoding='utf-8',
            ) as f:
                f.write(code)
                script_path = f.name

            if exec_context:
                exec_context.log("debug", f"Running Python script: {script_path}")

            returncode, stdout, stderr = await _run_script_subprocess(
                interpreter="python3",
                script_path=script_path,
                env=env,
                timeout=timeout,
            )

            if returncode == 0:
                return ActionResult(
                    outcome="success",
                    outputs={
                        "stdout": stdout,
                        "stderr": stderr,
                        "exit_code": 0,
                    }
                )
            else:
                return ActionResult(
                    outcome="failure",
                    outputs={
                        "stdout": stdout,
                        "stderr": stderr,
                        "exit_code": returncode,
                    },
                    error=f"Python script exited with code {returncode}",
                    error_type="ScriptError",
                )

        except asyncio.TimeoutError:
            return ActionResult(
                outcome="failure",
                outputs={"exit_code": -1, "stdout": "", "stderr": ""},
                error=f"Python script timed out after {timeout}s",
                error_type="TimeoutError",
            )
        except FileNotFoundError:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="python3 interpreter not found",
                error_type="EnvironmentError",
            )
        except Exception as e:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=str(e),
                error_type=type(e).__name__,
            )
        finally:
            # Clean up temp file
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError:
                    pass


class BashScriptAction(BaseAction):
    """
    Run Bash script in a subprocess.

    The script is written to a temporary file and run via /bin/bash.
    This is safer than shell=True as it avoids shell injection.

    YAML usage (inline):
        - id: setup_env
          uses: script/bash
          with:
            script: |
              set -e
              echo "Setting up..."
              mkdir -p /tmp/workspace
              echo "Done: $(date)"
            env:
              CUSTOM_VAR: "value"
            timeout: 60

    YAML usage (file-based):
        - id: run_scanner
          uses: script/bash
          with:
            path: scripts/scan.sh
            env:
              TARGET: "${{ inputs.target }}"
            timeout: 300

    Outputs:
        - stdout: Standard output from the script
        - stderr: Standard error from the script
        - exit_code: Process exit code (0 = success)
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Run Bash script in subprocess."""
        with_config = step_config.get("with", {})
        script = with_config.get("script", "")
        script_path_config = with_config.get("path", "")
        script_env = with_config.get("env", {})
        timeout = with_config.get("timeout", 30)

        # Load script from file if path is specified
        if script_path_config and not script:
            try:
                script_file = os.path.expanduser(script_path_config)
                if not os.path.isabs(script_file):
                    # Resolve relative to workflow workspace or cwd
                    workspace = context.get("env", {}).get("WORKSPACE", os.getcwd())
                    script_file = os.path.join(workspace, script_file)
                with open(script_file, "r", encoding="utf-8") as f:
                    script = f.read()
            except FileNotFoundError:
                return ActionResult(
                    outcome="failure",
                    outputs={},
                    error=f"Script file not found: {script_path_config}",
                    error_type="FileNotFoundError",
                )
            except PermissionError:
                return ActionResult(
                    outcome="failure",
                    outputs={},
                    error=f"Permission denied reading script: {script_path_config}",
                    error_type="PermissionError",
                )

        if not script:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'script' or 'path' provided for script/bash action",
                error_type="ValidationError",
            )

        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid timeout value: {timeout}. Must be positive number.",
                error_type="ValidationError",
            )

        # Build environment: inherit current env + script-specific vars
        env = dict(os.environ)
        for key, value in script_env.items():
            env[key] = str(value) if value is not None else ""

        script_path = None
        try:
            # Write script to temp file
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.sh',
                delete=False,
                encoding='utf-8',
            ) as f:
                f.write(script)
                script_path = f.name

            if exec_context:
                exec_context.log("debug", f"Running Bash script: {script_path}")

            returncode, stdout, stderr = await _run_script_subprocess(
                interpreter="/bin/bash",
                script_path=script_path,
                env=env,
                timeout=timeout,
            )

            if returncode == 0:
                return ActionResult(
                    outcome="success",
                    outputs={
                        "stdout": stdout,
                        "stderr": stderr,
                        "exit_code": 0,
                    }
                )
            else:
                return ActionResult(
                    outcome="failure",
                    outputs={
                        "stdout": stdout,
                        "stderr": stderr,
                        "exit_code": returncode,
                    },
                    error=f"Bash script exited with code {returncode}",
                    error_type="ScriptError",
                )

        except asyncio.TimeoutError:
            return ActionResult(
                outcome="failure",
                outputs={"exit_code": -1, "stdout": "", "stderr": ""},
                error=f"Bash script timed out after {timeout}s",
                error_type="TimeoutError",
            )
        except FileNotFoundError:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="/bin/bash not found",
                error_type="EnvironmentError",
            )
        except Exception as e:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=str(e),
                error_type=type(e).__name__,
            )
        finally:
            # Clean up temp file
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError:
                    pass
