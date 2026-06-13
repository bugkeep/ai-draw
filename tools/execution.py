import os
import re
import fnmatch
import subprocess
from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_OUTPUT_CAP = 65536  # 64 KB


def _safe_path(path: str) -> str:
    """Resolve a relative path against PROJECT_ROOT and prevent traversal."""
    joined = os.path.join(PROJECT_ROOT, path)
    abs_path = os.path.normpath(os.path.abspath(joined))
    root = os.path.normpath(os.path.abspath(PROJECT_ROOT))
    if not abs_path.startswith(root):
        raise ValueError(f"Path traversal detected: {path}")
    return abs_path


class ReadFileTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read the contents of a file",
            parameters=[
                ToolParameter(name="file_path", type="string",
                              description="Path relative to project root",
                              required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        file_path = kwargs.get("file_path", "")
        try:
            abs_path = _safe_path(file_path)
        except ValueError as e:
            return ToolResult(is_error=True, error=str(e))
        if not os.path.isfile(abs_path):
            return ToolResult(is_error=True, error=f"File not found: {file_path}")
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return ToolResult(is_error=True, error=f"Read failed: {e}")
        return ToolResult(
            data={"file_path": file_path, "content": content},
            description=f"Read {file_path} ({len(content)} chars)",
        )


class WriteFileTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write_file",
            description="Write content to a file (creates parent dirs automatically)",
            parameters=[
                ToolParameter(name="file_path", type="string",
                              description="Path relative to project root",
                              required=True),
                ToolParameter(name="content", type="string",
                              description="File content",
                              required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        file_path = kwargs.get("file_path", "")
        content = kwargs.get("content", "")
        try:
            abs_path = _safe_path(file_path)
        except ValueError as e:
            return ToolResult(is_error=True, error=str(e))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            return ToolResult(is_error=True, error=f"Write failed: {e}")
        return ToolResult(
            data={"file_path": file_path, "bytes": len(content)},
            description=f"Wrote {len(content)} bytes to {file_path}",
        )


class ListDirTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_dir",
            description="List directory contents with optional recursion depth",
            parameters=[
                ToolParameter(name="path", type="string",
                              description="Directory path relative to project root",
                              required=True),
                ToolParameter(name="depth", type="integer",
                              description="Recursion depth (default: 1, -1 for unlimited)"),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path", "")
        depth = kwargs.get("depth", 1)
        try:
            abs_path = _safe_path(path)
        except ValueError as e:
            return ToolResult(is_error=True, error=str(e))
        if not os.path.isdir(abs_path):
            return ToolResult(is_error=True, error=f"Directory not found: {path}")

        lines = [f"{path}/"]

        def _walk(dirpath, current_depth):
            if depth != -1 and current_depth > depth:
                return
            try:
                entries = sorted(os.listdir(dirpath))
            except PermissionError:
                lines.append("  " * current_depth + "  [permission denied]")
                return
            for entry in entries:
                full = os.path.join(dirpath, entry)
                prefix = "  " * current_depth
                if os.path.isdir(full):
                    lines.append(f"{prefix}  {entry}/")
                    _walk(full, current_depth + 1)
                else:
                    try:
                        size = os.path.getsize(full)
                        lines.append(f"{prefix}  {entry}  ({size} B)")
                    except OSError:
                        lines.append(f"{prefix}  {entry}")

        _walk(abs_path, 1)
        return ToolResult(
            data={"path": path, "entries": len(lines) - 1},
            description="\n".join(lines),
        )


class BashTool(BaseTool):
    MAX_OUTPUT = _OUTPUT_CAP

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description="Execute a shell command (working dir: project root). "
                        "Stdout and stderr are merged.",
            parameters=[
                ToolParameter(name="command", type="string",
                              description="Shell command to execute",
                              required=True),
                ToolParameter(name="timeout", type="integer",
                              description="Timeout in seconds (default: 30)"),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 30)
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=PROJECT_ROOT,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(is_error=True, error=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(is_error=True, error=f"Command failed: {e}")

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        output = stdout + stderr

        if len(output) > self.MAX_OUTPUT:
            output = output[:self.MAX_OUTPUT] + "\n... (truncated at 64KB)"

        return ToolResult(
            data={"exit_code": result.returncode, "output": output},
            description=f"Exit code: {result.returncode} ({len(output)} bytes)",
        )


class SearchTextTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_text",
            description="Search for a regex pattern in files (like grep)",
            parameters=[
                ToolParameter(name="pattern", type="string",
                              description="Regex pattern to search for",
                              required=True),
                ToolParameter(name="path", type="string",
                              description="File or directory path relative to project root",
                              required=True),
                ToolParameter(name="include", type="string",
                              description="Glob to filter files, e.g. '*.py'"),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path", "")
        include = kwargs.get("include")
        try:
            abs_path = _safe_path(path)
        except ValueError as e:
            return ToolResult(is_error=True, error=str(e))
        if not os.path.exists(abs_path):
            return ToolResult(is_error=True, error=f"Path not found: {path}")

        matches = []
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(is_error=True, error=f"Invalid regex: {e}")

        def _search_file(filepath):
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            rel = os.path.relpath(filepath, PROJECT_ROOT)
                            matches.append(f"{rel}:{i}:{line.rstrip()[:200]}")
            except Exception:
                pass

        if os.path.isfile(abs_path):
            _search_file(abs_path)
        else:
            for root, dirs, files in os.walk(abs_path):
                for fname in sorted(files):
                    if include and not fnmatch.fnmatch(fname, include):
                        continue
                    _search_file(os.path.join(root, fname))

        if not matches:
            return ToolResult(description=f"No matches for '{pattern}' in {path}")

        result = "\n".join(matches[:50])
        return ToolResult(
            data={"matches": matches},
            description=f"Found {len(matches)} matches for '{pattern}'",
        )


class PatchFileTool(BaseTool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="patch_file",
            description="Edit a file by replacing exact text (all occurrences)",
            parameters=[
                ToolParameter(name="file_path", type="string",
                              description="Path relative to project root",
                              required=True),
                ToolParameter(name="old_text", type="string",
                              description="Exact text to find and replace",
                              required=True),
                ToolParameter(name="new_text", type="string",
                              description="Replacement text",
                              required=True),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        file_path = kwargs.get("file_path", "")
        old_text = kwargs.get("old_text", "")
        new_text = kwargs.get("new_text", "")
        try:
            abs_path = _safe_path(file_path)
        except ValueError as e:
            return ToolResult(is_error=True, error=str(e))
        if not os.path.isfile(abs_path):
            return ToolResult(is_error=True, error=f"File not found: {file_path}")
        if not old_text:
            return ToolResult(is_error=True, error="old_text cannot be empty")

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return ToolResult(is_error=True, error=f"Read failed: {e}")

        count = content.count(old_text)
        if count == 0:
            return ToolResult(is_error=True, error=f"old_text not found in {file_path}")

        new_content = content.replace(old_text, new_text)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            return ToolResult(is_error=True, error=f"Write failed: {e}")

        return ToolResult(
            data={"file_path": file_path, "replacements": count},
            description=f"Patched {file_path}: {count} replacement(s)",
        )
