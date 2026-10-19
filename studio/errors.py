import json

from pydantic import ValidationError


class AgentOutputError(ValueError):
    pass


def validation_summary(error: Exception) -> str:
    if isinstance(error, ValidationError):
        issues = error.errors(include_input=False, include_context=False, include_url=False)
        return "；".join(f"{'.'.join(map(str, issue['loc'])) or 'action'}: {issue['msg']}" for issue in issues)
    if isinstance(error, json.JSONDecodeError):
        return f"JSON 第 {error.lineno} 行第 {error.colno} 列：{error.msg}"
    return str(error)
