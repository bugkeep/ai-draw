from dataclasses import dataclass, field
from typing import Any
from abc import ABC, abstractmethod


@dataclass
class ToolParameter:
    name: str = ""
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    enum: list[str] | None = None

    def to_json_schema(self) -> dict:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class ToolDefinition:
    name: str = ""
    description: str = ""
    parameters: list[ToolParameter] = field(default_factory=list)

    def to_openai(self) -> dict:
        props = {}
        required = []
        for param in self.parameters:
            props[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }

    def to_anthropic(self) -> dict:
        props = {}
        required = []
        for param in self.parameters:
            props[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }


@dataclass
class ToolResult:
    is_error: bool = False
    data: Any = None
    code: str = ""
    description: str = ""
    error: str = ""
    error_type: str = ""

    def to_dict(self) -> dict:
        result = {"is_error": self.is_error}
        if not self.is_error:
            result["data"] = self.data
            result["code"] = self.code
            result["description"] = self.description
        else:
            result["error"] = self.error
            result["error_type"] = self.error_type
        return result


class BaseTool(ABC):
    @abstractmethod
    def definition(self) -> ToolDefinition:
        ...

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        ...
