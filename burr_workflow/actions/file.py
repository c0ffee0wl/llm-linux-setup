"""
File operations actions for reading and writing files.

This module provides actions for file I/O within workflows.
"""

import base64
import os
from typing import TYPE_CHECKING, Any, Optional

from .base import ActionResult, BaseAction

# Maximum file size to read into memory (100 MB default)
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class FileReadAction(BaseAction):
    """
    Read file contents.

    YAML usage:
        # Read text file
        - id: load_config
          uses: file/read
          with:
            path: config.yaml
            encoding: utf-8  # default

        # Read binary file (returns base64)
        - id: load_image
          uses: file/read
          with:
            path: image.png
            encoding: binary

    Parameters:
        - path: File path (required)
        - encoding: utf-8 (default), binary (base64), or auto

    Outputs:
        - content: File content (string or base64 for binary)
        - size: File size in bytes
        - path: Resolved absolute path
        - encoding: The encoding used
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Read file contents."""
        with_config = step_config.get("with", {})

        path = with_config.get("path")
        encoding = with_config.get("encoding", "utf-8")

        if not path:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'path' provided for file/read",
                error_type="ValidationError",
            )

        # Validate encoding
        valid_encodings = {"utf-8", "binary", "auto"}
        if encoding not in valid_encodings:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid encoding '{encoding}'. Must be one of: {valid_encodings}",
                error_type="ValidationError",
            )

        # Resolve path
        resolved_path = os.path.expanduser(str(path))
        if not os.path.isabs(resolved_path):
            workspace = context.get("env", {}).get("WORKSPACE", os.getcwd())
            resolved_path = os.path.join(workspace, resolved_path)
        resolved_path = os.path.abspath(resolved_path)

        try:
            # Get file size
            file_size = os.path.getsize(resolved_path)

            # Check file size limit to prevent OOM
            max_size = step_config.get("max_size", DEFAULT_MAX_FILE_SIZE)
            if file_size > max_size:
                return ActionResult(
                    outcome="failure",
                    outputs={"path": resolved_path, "size": file_size, "max_size": max_size},
                    error=f"File too large: {file_size} bytes (limit: {max_size} bytes)",
                    error_type="FileSizeError",
                )

            # Read file
            if encoding == "binary":
                with open(resolved_path, "rb") as f:
                    data = f.read()
                content = base64.b64encode(data).decode("ascii")
                actual_encoding = "binary"
            elif encoding == "auto":
                # Try text first, fall back to binary
                try:
                    with open(resolved_path, encoding="utf-8") as f:
                        content = f.read()
                    actual_encoding = "utf-8"
                except UnicodeDecodeError:
                    with open(resolved_path, "rb") as f:
                        data = f.read()
                    content = base64.b64encode(data).decode("ascii")
                    actual_encoding = "binary"
            else:
                with open(resolved_path, encoding=encoding) as f:
                    content = f.read()
                actual_encoding = encoding

            if exec_context:
                exec_context.log("debug", f"Read {file_size} bytes from {resolved_path}")

            return ActionResult(
                outcome="success",
                outputs={
                    "content": content,
                    "size": file_size,
                    "path": resolved_path,
                    "encoding": actual_encoding,
                }
            )

        except FileNotFoundError:
            return ActionResult(
                outcome="failure",
                outputs={"path": resolved_path},
                error=f"File not found: {resolved_path}",
                error_type="FileNotFoundError",
            )
        except PermissionError:
            return ActionResult(
                outcome="failure",
                outputs={"path": resolved_path},
                error=f"Permission denied: {resolved_path}",
                error_type="PermissionError",
            )
        except Exception as e:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=str(e),
                error_type=type(e).__name__,
            )


class FileWriteAction(BaseAction):
    """
    Write content to file.

    YAML usage:
        # Write text file
        - id: save_results
          uses: file/write
          with:
            path: results/${{ inputs.target }}.txt
            content: ${{ steps.scan.outputs.stdout }}
            mode: overwrite  # create | overwrite | append
            mkdir: true      # create parent directories

        # Append to log
        - id: log_entry
          uses: file/write
          with:
            path: workflow.log
            content: "${{ now() }}: Step completed\n"
            mode: append

    Parameters:
        - path: File path (required)
        - content: Content to write (required)
        - mode: create (fail if exists), overwrite (default), append
        - mkdir: Create parent directories if needed (default: false)

    Outputs:
        - path: Resolved absolute path
        - size: Bytes written
        - existed: True if file existed before write
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Write content to file."""
        with_config = step_config.get("with", {})

        path = with_config.get("path")
        content = with_config.get("content", "")
        mode = with_config.get("mode", "overwrite")
        mkdir = with_config.get("mkdir", False)

        if not path:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'path' provided for file/write",
                error_type="ValidationError",
            )

        # Validate mode
        valid_modes = {"create", "overwrite", "append"}
        if mode not in valid_modes:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid mode '{mode}'. Must be one of: {valid_modes}",
                error_type="ValidationError",
            )

        # Resolve path
        resolved_path = os.path.expanduser(str(path))
        if not os.path.isabs(resolved_path):
            workspace = context.get("env", {}).get("WORKSPACE", os.getcwd())
            resolved_path = os.path.join(workspace, resolved_path)
        resolved_path = os.path.abspath(resolved_path)

        # Check if file exists
        existed = os.path.exists(resolved_path)

        # Handle mode=create (fail if exists)
        if mode == "create" and existed:
            return ActionResult(
                outcome="failure",
                outputs={"path": resolved_path, "existed": True},
                error=f"File already exists: {resolved_path}",
                error_type="FileExistsError",
            )

        try:
            # Create parent directories if requested
            if mkdir:
                parent_dir = os.path.dirname(resolved_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)

            # Determine write mode
            if mode == "append":
                file_mode = "a"
            else:
                file_mode = "w"

            # Ensure content is a string
            if not isinstance(content, str):
                content = str(content)

            # Write file
            with open(resolved_path, file_mode, encoding="utf-8") as f:
                f.write(content)

            if exec_context:
                action = "appended to" if mode == "append" else "wrote"
                exec_context.log("debug", f"{action} {len(content)} bytes to {resolved_path}")

            return ActionResult(
                outcome="success",
                outputs={
                    "path": resolved_path,
                    "size": len(content),
                    "existed": existed,
                }
            )

        except PermissionError:
            return ActionResult(
                outcome="failure",
                outputs={"path": resolved_path},
                error=f"Permission denied: {resolved_path}",
                error_type="PermissionError",
            )
        except Exception as e:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=str(e),
                error_type=type(e).__name__,
            )
