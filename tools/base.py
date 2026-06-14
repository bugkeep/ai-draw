from dataclasses import dataclass, field
from typing import Any
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field, create_model


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


# ── Pydantic model helpers ─────────────────────────────────────────────

_TYPE_MAP = {
    "string": (str, ""),
    "integer": (int, 0),
    "number": (float, 0.0),
    "boolean": (bool, False),
}


def _build_param_model(defn: ToolDefinition) -> type[BaseModel]:
    """Build a Pydantic model from a ToolDefinition for runtime validation."""
    fields: dict[str, tuple[type, Any]] = {}
    for param in defn.parameters:
        py_type, default_zero = _TYPE_MAP.get(param.type, (str, ""))
        field_kwargs: dict[str, Any] = {"description": param.description}

        if param.enum:
            # Build an enum type to enforce allowed values
            enum_cls = type(
                f"{defn.name}_{param.name}_enum",
                (str,),
                {e: e for e in param.enum},
            )
            py_type = enum_cls
            field_kwargs["description"] = (
                f"{param.description} (allowed: {', '.join(param.enum)})"
            )

        if param.required:
            # required — no default
            fields[param.name] = (py_type, Field(**field_kwargs))
        elif param.default is not None:
            # optional with explicit default
            fields[param.name] = (py_type, Field(default=param.default, **field_kwargs))
        else:
            # optional, no default — allow None
            fields[param.name] = (py_type | None, Field(default=None, **field_kwargs))

    return create_model(f"{defn.name}_validator", **fields)


@dataclass
class ToolResult:
    is_error: bool = False
    data: Any = None
    code: str = ""
    description: str = ""
    error: str = ""
    error_type: str = ""  # "" | "invalid_args" | "not_found" | "permission_denied" | "execution_error" | "exception"

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
    _validator: type[BaseModel] | None = None

    @abstractmethod
    def definition(self) -> ToolDefinition:
        ...

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        ...

    def validate_params(self, params: dict) -> dict:
        """Validate and coerce parameters using an auto-built Pydantic model.

        Returns the validated (and type-coerced) kwargs dict on success.
        Raises ``pydantic.ValidationError`` on failure.
        """
        defn = self.definition()
        if not defn.parameters:
            return params  # no params defined — nothing to validate
        if self._validator is None:
            self._validator = _build_param_model(defn)
        validated = self._validator(**params)
        return validated.model_dump(exclude_none=True)
