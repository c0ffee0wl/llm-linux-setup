"""
Script runner actions for Python and Bash.

This module provides actions that run scripts in isolated subprocesses,
with optional sandboxing via bubblewrap (bwrap).

Security: Uses asyncio.create_subprocess_exec (not shell) to prevent injection.
"""

import asyncio
import os
import shutil
import tempfile
from typing import Any, ClassVar, Optional, TYPE_CHECKING

from .base import BaseAction
from ..core.types import ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class ScriptAction(BaseAction):
    """
    Base class for script execution actions.

    Provides shared implementation for running scripts in subprocesses
    with optional sandboxing via bubblewrap.

    Subclasses define: interpreter, suffix, code_key
    """

    interpreter: ClassVar[str]
    suffix: ClassVar[str]
    code_key: ClassVar[str]

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Run script in subprocess with optional sandboxing."""
        with_config = step_config.get("with", {})

        code = with_config.get(self.code_key, "")
        script_path_config = with_config.get("path", "")
        script_env = with_config.get("env", {})
        timeout = with_config.get("timeout", 30)
        sandbox = with_config.get("sandbox", False)

        # Load from file if path specified
        if script_path_config and not code:
            try:
                script_file = os.path.expanduser(script_path_config)
                if not os.path.isabs(script_file):
                    workspace = context.get("env", {}).get("WORKSPACE", os.getcwd())
                    script_file = os.path.join(workspace, script_file)
                with open(script_file, "r", encoding="utf-8") as f:
                    code = f.read()
            except FileNotFoundError:
                return ActionResult(
                    outcome="failure", outputs={},
                    error=f"Script file not found: {script_path_config}",
                    error_type="FileNotFoundError",
                )
            except PermissionError:
                return ActionResult(
                    outcome="failure", outputs={},
                    error=f"Permission denied: {script_path_config}",
                    error_type="PermissionError",
                )

        if not code:
            return ActionResult(
                outcome="failure", outputs={},
                error=f"No '{self.code_key}' or 'path' provided",
                error_type="ValidationError",
            )

        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return ActionResult(
                outcome="failure", outputs={},
                error=f"Invalid timeout: {timeout}",
                error_type="ValidationError",
            )

        if sandbox and not shutil.which("bwrap"):
            return ActionResult(
                outcome="failure", outputs={},
                error="Sandbox requires 'bwrap' (bubblewrap)",
                error_type="EnvironmentError",
            )

        env = dict(os.environ)
        for key, value in script_env.items():
            env[key] = str(value) if value is not None else ""

        script_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', suffix=self.suffix, delete=False, encoding='utf-8'
            ) as f:
                f.write(code)
                script_path = f.name

            if sandbox:
                returncode, stdout, stderr = await self._run_sandboxed(script_path, env, timeout)
            else:
                returncode, stdout, stderr = await self._run_normal(script_path, env, timeout)

            if returncode == 0:
                return ActionResult(
                    outcome="success",
                    outputs={"stdout": stdout, "stderr": stderr, "exit_code": 0}
                )
            else:
                return ActionResult(
                    outcome="failure",
                    outputs={"stdout": stdout, "stderr": stderr, "exit_code": returncode},
                    error=f"Script exited with code {returncode}",
                    error_type="ScriptError",
                )

        except asyncio.TimeoutError:
            return ActionResult(
                outcome="failure",
                outputs={"exit_code": -1, "stdout": "", "stderr": ""},
                error=f"Script timed out after {timeout}s",
                error_type="TimeoutError",
            )
        except FileNotFoundError as e:
            return ActionResult(
                outcome="failure", outputs={},
                error=f"Interpreter not found: {e}",
                error_type="EnvironmentError",
            )
        except Exception as e:
            return ActionResult(
                outcome="failure", outputs={},
                error=str(e), error_type=type(e).__name__,
            )
        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError:
                    pass

    async def _run_normal(self, script_path: str, env: dict, timeout: float) -> tuple[int, str, str]:
        """Run script without sandbox using subprocess_exec (safe, no shell)."""
        proc = await asyncio.create_subprocess_exec(
            self.interpreter, script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode or 0, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")

    async def _run_sandboxed(self, script_path: str, env: dict, timeout: float) -> tuple[int, str, str]:
        """Run script in bwrap sandbox (read-only root, no network, PID isolation)."""
        args = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--tmpfs", "/tmp",
            "--dev", "/dev",
            "--proc", "/proc",
            "--unshare-net",
            "--unshare-pid",
            "--die-with-parent",
            "--",
            self.interpreter, script_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode or 0, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


class PythonScriptAction(ScriptAction):
    """
    Run Python code in a subprocess.

    YAML usage:
        - id: process
          uses: script/python
          with:
            code: |
              import json, os
              data = json.loads(os.environ.get('INPUT', '{}'))
              print(json.dumps({"count": len(data)}))
            env:
              INPUT: "${{ steps.fetch.outputs.response }}"
            timeout: 60
            sandbox: false  # optional, use bwrap sandbox

    Outputs: stdout, stderr, exit_code
    """

    interpreter: ClassVar[str] = "python3"
    suffix: ClassVar[str] = ".py"
    code_key: ClassVar[str] = "code"


class BashScriptAction(ScriptAction):
    """
    Run Bash script in a subprocess.

    YAML usage:
        - id: setup
          uses: script/bash
          with:
            script: |
              set -e
              echo "Setting up..."
              mkdir -p /tmp/workspace
            env:
              TARGET: "${{ inputs.target }}"
            timeout: 60
            sandbox: false  # optional, use bwrap sandbox

    Outputs: stdout, stderr, exit_code
    """

    interpreter: ClassVar[str] = "/bin/bash"
    suffix: ClassVar[str] = ".sh"
    code_key: ClassVar[str] = "script"
