import os

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def read_global_context() -> str:
    """Read global context from ~/.claude/context.md (shared across all projects)."""
    path = os.path.join(os.path.expanduser("~"), ".claude", "context.md")
    return _read_file(path)


def read_project_context() -> str:
    """Read project-level context from <project_root>/context.md."""
    path = os.path.join(PROJECT_ROOT, "context.md")
    return _read_file(path)


def read_session_notes(store) -> str:
    """Read session notes via SessionStore.read_notes()."""
    if store is None:
        return ""
    return store.read_notes()


def format_three_layer_context(store=None) -> str:
    """Combine all three context layers into a single formatted string.

    Layer order (higher priority wins on conflict):
      1. Global  — ~/.claude/context.md    (shared across all projects)
      2. Project — <project_root>/context.md  (this repo)
      3. Session — notes.md via SessionStore  (current conversation)
    """
    global_ctx = read_global_context()
    project_ctx = read_project_context()
    session_ctx = read_session_notes(store)

    parts = []
    if global_ctx:
        parts.append("=== Global Context ===\n" + global_ctx)
    if project_ctx:
        parts.append("=== Project Context ===\n" + project_ctx)
    if session_ctx:
        parts.append("=== Session Notes ===\n" + session_ctx)

    return "\n\n".join(parts)


def _read_file(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""
