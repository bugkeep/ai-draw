from tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class NoteSaveTool(BaseTool):
    """Save a long-term memory note to the current session's notes.md.

    The note is injected into the system prompt on every subsequent run
    under ``Remembered facts:`` so the LLM can recall it across turns.

    Only works within a session context — without an active session the
    tool is a no-op (returns success with a descriptive message).
    """

    def __init__(self, store=None):
        self._store = store

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="note_save",
            description=(
                "Save a long-term memory note for the current conversation "
                "session. Use this to remember facts about the user, their "
                "preferences, or decisions that should persist across "
                "multiple turns. The note is automatically included in the "
                "system prompt on all future turns."
            ),
            parameters=[
                ToolParameter(
                    name="content",
                    type="string",
                    description="The fact or decision to remember",
                    required=True,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        content = kwargs.get("content", "")
        if not content:
            return ToolResult(is_error=True, error="content is required")

        if self._store is None:
            return ToolResult(
                is_error=False,
                description="No active session — note not saved",
            )

        self._store.append_notes(content)
        return ToolResult(
            is_error=False,
            description=f"Note saved: {content[:120]}",
            data={"saved": True, "preview": content[:120]},
        )


SESSION_TOOLS = [NoteSaveTool]
