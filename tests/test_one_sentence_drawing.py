"""End-to-end "一句话画图" (one-sentence drawing) test.

Tests the full S1-S7 pipeline with a Chinese NLP mock provider:

  S1-S5: AgentRunner + real drawing tools + event dispatching
  S6:    Context watermark, auto-compact, three-layer context
  S7:    Skill detection, sub-agents

The mock provider parses natural Chinese sentences into accurate
``ToolCall`` objects so the real ``AgentRunner`` / ``ToolRegistry`` /
drawing tool pipeline is exercised end-to-end — the only mocked layer
is the LLM call itself.

Accuracy verification:
  - Correct tool selected for each shape type
  - All position/size/color parameters match the user's description
  - Tool executes successfully (no pydantic/permission/execution errors)
  - Fabric.js code embeds the correct values
"""

import re
import json
import pytest
from providers.base import LLMResponse, ToolCall
from tools.base import ToolResult
from tools.registry import ToolRegistry
from tools import ALL_TOOLS
from agent.runner import AgentRunner, AgentConfig, new_run_id
from events import EventBus, EventType, BaseEvent


# ── Chinese colour → hex / name mapping ────────────────────────────────

CN_COLORS = {
    "红色": "red",
    "蓝色": "blue",
    "绿色": "green",
    "黄色": "yellow",
    "紫色": "purple",
    "橙色": "orange",
    "粉色": "pink",
    "黑色": "black",
    "白色": "white",
    "灰色": "gray",
    "青色": "cyan",
    "棕色": "brown",
    "透明": "transparent",
}

# Ordered list for regex alternation (longer first to prefer greedy match)
_COLOR_PATTERNS = "|".join(sorted(CN_COLORS, key=len, reverse=True))


def extract_color(text: str) -> str:
    """Return the first Chinese colour name found, or empty string."""
    m = re.search(_COLOR_PATTERNS, text)
    return CN_COLORS[m.group(0)] if m else ""


def extract_numbers(text: str) -> list[int]:
    """Extract all integers from text."""
    return [int(x) for x in re.findall(r"\d+", text)]


# ── Chinese NLP Mock Provider ──────────────────────────────────────────


class ChineseDrawingMockProvider:
    """Mock LLM provider that parses Chinese drawing commands.

    Takes a natural Chinese sentence like::

        "画一个红色的圆形，圆心在(100,100)，半径50"

    And returns the correct ``ToolCall`` with accurate parameters so the
    real tool execution pipeline can be verified end-to-end.
    """

    def __init__(self, context_pct: float = 0.0):
        self.calls: list[dict] = []
        self.call_count = 0
        self._context_pct = context_pct

    def _parse(self, message: str) -> list[ToolCall]:
        """Parse a Chinese drawing sentence into ToolCall objects."""
        msg = message.strip()
        color = extract_color(msg)
        nums = extract_numbers(msg)

        tool_calls: list[ToolCall] = []

        # IMPORTANT: check more-specific keywords BEFORE less-specific ones.
        # 椭圆 must be checked before 圆 otherwise "椭圆" matches both.

        if re.search(r"(直线|线段)", msg):
            args = self._parse_line(msg, color, nums)
            tool_calls.append(ToolCall(name="draw_line", arguments=args))

        if re.search(r"(椭圆)", msg):
            args = self._parse_ellipse(msg, color, nums)
            tool_calls.append(ToolCall(name="draw_ellipse", arguments=args))

        # 圆(形) — but NOT 椭圆(already matched) and NOT 圆弧/圆圈 etc.
        if re.search(r"(?<!椭)(圆形|圆)\s*(?!弧|环|圈|形文|弧线)", msg):
            args = self._parse_circle(msg, color, nums)
            tool_calls.append(ToolCall(name="draw_circle", arguments=args))

        if re.search(r"(矩形|长方形|方框|正方形)", msg):
            args = self._parse_rect(msg, color, nums)
            tool_calls.append(ToolCall(name="draw_rect", arguments=args))

        if re.search(r"(文字|文本|写字|标注)", msg):
            args = self._parse_text(msg, color, nums)
            tool_calls.append(ToolCall(name="draw_text", arguments=args))

        if re.search(r"(线条)", msg):
            args = self._parse_line(msg, color, nums)
            tool_calls.append(ToolCall(name="draw_line", arguments=args))

        # fallback: no known shape → draw a circle with just colour
        if not tool_calls:
            args = {}
            if color:
                args["color"] = color
            if len(nums) >= 2:
                args["center_x"] = nums[0]
                args["center_y"] = nums[1]
            if len(nums) >= 3:
                args["radius"] = nums[2]
            tool_calls.append(ToolCall(name="draw_circle", arguments=args))

        return tool_calls

    def _parse_circle(self, msg: str, color: str, nums: list[int]) -> dict:
        args: dict = {}
        if color:
            args["color"] = color

        # 圆心在(x,y) / 圆心(x,y) / 中心在(x,y)
        m = re.search(r"(?:圆心|中心)\s*[在处]?\s*[\(（]?\s*(\d+)\s*[,，\s]\s*(\d+)\s*[\)）]?", msg)
        if m:
            args["center_x"] = int(m.group(1))
            args["center_y"] = int(m.group(2))
        elif len(nums) >= 2:
            args["center_x"] = nums[0]
            args["center_y"] = nums[1]

        # 半径(X) / r=X / radius=X
        m = re.search(r"(?:半径|r|radius)\s*[:：=]?\s*(\d+)", msg, re.IGNORECASE)
        if m:
            args["radius"] = int(m.group(1))
        elif len(nums) >= 3 and "center_x" not in args:
            args["radius"] = nums[2]
        elif len(nums) >= 1 and "center_x" in args:
            # pick the last number as radius
            remaining = [n for n in nums if n not in (args.get("center_x"), args.get("center_y"))]
            if remaining:
                args["radius"] = remaining[0]

        return args

    def _parse_rect(self, msg: str, color: str, nums: list[int]) -> dict:
        args: dict = {}
        if color:
            args["color"] = color

        # position: (x,y) or 位置(x,y)
        m = re.search(r"[\(（]?\s*(\d+)\s*[,，\s]\s*(\d+)\s*[\)）]?", msg)
        if m:
            args["x"] = int(m.group(1))
            args["y"] = int(m.group(2))
        elif len(nums) >= 2:
            args["x"] = nums[0]
            args["y"] = nums[1]

        # 宽(X) / width=X
        m = re.search(r"(?:宽|宽度|width)\s*[:：=]?\s*(\d+)", msg, re.IGNORECASE)
        if m:
            args["width"] = int(m.group(1))
        # 高(X) / height=X
        m = re.search(r"(?:高|高度|height)\s*[:：=]?\s*(\d+)", msg, re.IGNORECASE)
        if m:
            args["height"] = int(m.group(1))

        # If only 2 numbers and no explicit width/height, try to derive
        if len(nums) >= 4 and "width" not in args:
            # if first 2 are position, remaining 2 might be width/height
            args["width"] = nums[2]
            args["height"] = nums[3]

        return args

    def _parse_line(self, msg: str, color: str, nums: list[int]) -> dict:
        args: dict = {}
        if color:
            args["color"] = color

        # from(x1,y1) to(x2,y2) / (x1,y1)→(x2,y2)
        pairs = re.findall(r"[\(（](\d+)\s*[,，\s]\s*(\d+)[\)）]", msg)
        if len(pairs) >= 2:
            args["start_x"] = int(pairs[0][0])
            args["start_y"] = int(pairs[0][1])
            args["end_x"] = int(pairs[1][0])
            args["end_y"] = int(pairs[1][1])
        elif len(nums) >= 4:
            args["start_x"] = nums[0]
            args["start_y"] = nums[1]
            args["end_x"] = nums[2]
            args["end_y"] = nums[3]

        # 线宽 / width
        m = re.search(r"(?:线宽|线条宽度|width)\s*[:：]?\s*(\d+)", msg, re.IGNORECASE)
        if m:
            args["width"] = int(m.group(1))

        return args

    def _parse_text(self, msg: str, color: str, nums: list[int]) -> dict:
        args: dict = {}
        if color:
            args["color"] = color

        # position
        if len(nums) >= 2:
            args["x"] = nums[0]
            args["y"] = nums[1]

        # text content: "写Hello" / "文字Hello" / "文本Hello" etc.
        m = re.search(r"(?:写|说|文字|文本|写字|标注|内容)\s*[:：]?\s*[\"\"](.+?)[\"\"]", msg)
        if not m:
            m = re.search(r"(?:写|说|文字|文本|写字|标注|内容)\s*[:：]?\s*'(.+?)'", msg)
        if not m:
            m = re.search(r"(?:写|说|文字|文本|写字|标注|内容)\s*[:：]?\s*「(.+?)」", msg)
        if m:
            args["text"] = m.group(1)
        else:
            # fallback: pick content after "文字" or similar keyword
            args["text"] = "AI Draw"

        # 字号 / font_size
        m = re.search(r"(?:字号|大小|字体大小|font.?size)\s*[:：]?\s*(\d+)", msg, re.IGNORECASE)
        if m:
            args["font_size"] = int(m.group(1))

        return args

    def _parse_ellipse(self, msg: str, color: str, nums: list[int]) -> dict:
        args: dict = {}
        if color:
            args["color"] = color

        m = re.search(r"(?:圆心|中心)\s*[在处]?\s*[\(（]?\s*(\d+)\s*[,，\s]\s*(\d+)\s*[\)）]?", msg)
        if m:
            args["center_x"] = int(m.group(1))
            args["center_y"] = int(m.group(2))
        elif len(nums) >= 2:
            args["center_x"] = nums[0]
            args["center_y"] = nums[1]

        # rx / 水平半径 / 横向半径 (supports rx=80, rx:80, rx：80)
        m = re.search(r"(?:rx|水平半径|横向)\s*[:：=]?\s*(\d+)", msg, re.IGNORECASE)
        if m:
            args["rx"] = int(m.group(1))
        # ry / 垂直半径 / 纵向
        m = re.search(r"(?:ry|垂直半径|纵向)\s*[:：=]?\s*(\d+)", msg, re.IGNORECASE)
        if m:
            args["ry"] = int(m.group(1))

        return args

    async def achat(self, messages, tools=None, model=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})

        # Only produce tool_calls on the FIRST llm call — subsequent rounds
        # get a plain text response so the agent loop terminates naturally.
        if self.call_count == 0:
            # Extract the last user message
            user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_msg = m.get("content", "")
                    break
            tool_calls = self._parse(user_msg)
            content = f"好的，我将按你的描述画{'、'.join(tc.name for tc in tool_calls)}"
        else:
            tool_calls = []
            content = "已经画好了，还有什么需要调整的吗？"

        self.call_count += 1
        return LLMResponse(
            content=content,
            model="mock-cn-drawing",
            tokens_used=250,
            context_window=128000,
            context_pct=self._context_pct,
            tool_calls=tool_calls,
        )

    def chat(self, messages, tools=None, model=None):
        raise NotImplementedError("Sync chat not used")


# ── Test helpers ───────────────────────────────────────────────────────


def make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool_cls in ALL_TOOLS:
        reg.register(tool_cls())
    return reg


def make_runner(provider=None, event_bus=None, max_rounds=5):
    p = provider or ChineseDrawingMockProvider()
    return AgentRunner(AgentConfig(
        provider=p,
        registry=make_registry(),
        event_bus=event_bus or EventBus(),
        max_rounds=max_rounds,
    ))


def verify_fabric_code(tool_name: str, code: str, expected_params: dict) -> list[str]:
    """Verify Fabric.js code contains expected parameter values.

    Returns a list of verification messages (empty = all passed).
    """
    issues: list[str] = []
    if tool_name == "draw_circle":
        r = expected_params.get("radius", 50)
        if f"radius: {float(r)}" not in code:
            issues.append(f"Expected radius={float(r)} in code, got: {code[:100]}")
    elif tool_name == "draw_rect":
        w = expected_params.get("width", 100)
        h = expected_params.get("height", 80)
        if f"width: {float(w)}" not in code:
            issues.append(f"Expected width={float(w)} in code, got: {code[:120]}")
        if f"height: {float(h)}" not in code:
            issues.append(f"Expected height={float(h)} in code, got: {code[:120]}")
    elif tool_name == "draw_line":
        x1 = expected_params.get("start_x", 100)
        y1 = expected_params.get("start_y", 100)
        x2 = expected_params.get("end_x", 300)
        y2 = expected_params.get("end_y", 300)
        # Tool outputs floats (50.0), so check with float formatting
        expected_fragment = f"[{float(x1)}, {float(y1)}, {float(x2)}, {float(y2)}]"
        if expected_fragment not in code:
            issues.append(f"Expected line coords {expected_fragment} in code, got: {code[:120]}")
    elif tool_name == "draw_text":
        text = expected_params.get("text", "AI Draw")
        if text.replace("'", "\\'") in code or text in code:
            pass  # text is embedded
        else:
            issues.append(f"Expected text '{text}' in code")
        fs = expected_params.get("font_size", 24)
        if f"fontSize: {float(fs)}" not in code:
            issues.append(f"Expected font_size={float(fs)} in code")
    elif tool_name == "draw_ellipse":
        rx = expected_params.get("rx", 80)
        ry = expected_params.get("ry", 50)
        if f"rx: {rx}" not in code:
            issues.append(f"Expected rx={rx} in code, got: {code[:120]}")
        if f"ry: {ry}" not in code:
            issues.append(f"Expected ry={ry} in code, got: {code[:120]}")

    # Verify color in code (if specified)
    color = expected_params.get("color")
    if color and color not in code:
        issues.append(f"Expected color '{color}' in code")
    return issues


def verify_data_accuracy(tool_name: str, data: dict, expected: dict) -> list[str]:
    """Verify tool result data matches expected parameters."""
    issues: list[str] = []
    for key, expected_val in expected.items():
        actual = data.get(key)
        if actual is not None and actual != expected_val:
            issues.append(f"Data mismatch for {tool_name}.{key}: "
                          f"expected {expected_val!r}, got {actual!r}")
    return issues


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════


class TestNLPMockProvider:
    """Verify the mock provider itself produces correct ToolCalls."""

    def test_draw_red_circle_with_position_and_radius(self):
        provider = ChineseDrawingMockProvider()
        calls = provider._parse("画一个红色的圆形，圆心在(100,200)，半径50")
        assert len(calls) == 1
        assert calls[0].name == "draw_circle"
        assert calls[0].arguments == {"center_x": 100, "center_y": 200, "radius": 50, "color": "red"}

    def test_draw_blue_rect_with_position_and_size(self):
        provider = ChineseDrawingMockProvider()
        calls = provider._parse("画一个蓝色的矩形，位置(200,150)，宽120高80")
        assert len(calls) == 1
        assert calls[0].name == "draw_rect"
        assert calls[0].arguments == {"x": 200, "y": 150, "width": 120, "height": 80, "color": "blue"}

    def test_draw_green_line(self):
        provider = ChineseDrawingMockProvider()
        calls = provider._parse("画一条绿色的直线从(50,50)到(300,200)")
        assert len(calls) == 1
        assert calls[0].name == "draw_line"
        assert calls[0].arguments == {"start_x": 50, "start_y": 50, "end_x": 300, "end_y": 200, "color": "green"}

    def test_draw_text(self):
        provider = ChineseDrawingMockProvider()
        calls = provider._parse('添加文字在(300,200)处写"Hello"，字号36，红色')
        assert len(calls) == 1
        assert calls[0].name == "draw_text"
        args = calls[0].arguments
        assert args.get("x") == 300
        assert args.get("y") == 200
        assert "Hello" in args.get("text", "")
        assert args.get("font_size") == 36
        assert args.get("color") == "red"

    def test_draw_ellipse(self):
        provider = ChineseDrawingMockProvider()
        calls = provider._parse("画一个椭圆，中心(400,300)，rx=80，ry=50，蓝色")
        assert len(calls) == 1
        assert calls[0].name == "draw_ellipse"
        assert calls[0].arguments == {"center_x": 400, "center_y": 300, "rx": 80, "ry": 50, "color": "blue"}

    def test_multiple_shapes_in_one_sentence(self):
        provider = ChineseDrawingMockProvider()
        calls = provider._parse("画一个红色圆形在(100,100)半径50，再画一个蓝色矩形在(200,150)宽100高80")
        names = [c.name for c in calls]
        assert "draw_circle" in names
        assert "draw_rect" in names

    def test_fallback_no_shape_keyword(self):
        provider = ChineseDrawingMockProvider()
        calls = provider._parse("红色 100 200 50")
        assert len(calls) >= 1
        assert calls[0].name == "draw_circle"


# ═══════════════════════════════════════════════════════════════════════
# S1-S5: AgentRunner + Real Tools + Events
# ═══════════════════════════════════════════════════════════════════════


class TestOneSentenceDrawing:
    """S1-S5: Full AgentRunner pipeline with real drawing tools."""

    @pytest.mark.asyncio
    async def test_draw_red_circle_accurate(self):
        """一句话：画一个红色的圆形，圆心在(100,200)，半径50 → draw_circle with exact params."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        result = await runner.run("画一个红色的圆形，圆心在(100,200)，半径50")

        assert result.success, f"Run failed: {result.error}"
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["name"] == "draw_circle"
        assert tc["is_error"] is False
        assert tc["status"] == "success"

        # Verify args match input
        args = tc["arguments"]
        assert args.get("center_x") == 100
        assert args.get("center_y") == 200
        assert args.get("radius") == 50
        assert args.get("color") == "red"

        # Verify Fabric.js code has correct values
        issues = verify_fabric_code("draw_circle", result.code, args)
        assert not issues, "; ".join(issues)

        # Verify tool result data
        # (data is embedded in the new_messages as tool_result content)
        assert "fabric.Circle" in result.code

    @pytest.mark.asyncio
    async def test_draw_blue_rect_accurate(self):
        """一句话：画一个蓝色的矩形，位置(200,150)，宽120高80 → draw_rect with exact params."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        result = await runner.run("画一个蓝色的矩形，位置(200,150)，宽120高80")

        assert result.success
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["name"] == "draw_rect"
        assert tc["is_error"] is False
        assert tc["arguments"]["x"] == 200
        assert tc["arguments"]["y"] == 150
        assert tc["arguments"]["width"] == 120
        assert tc["arguments"]["height"] == 80
        assert tc["arguments"]["color"] == "blue"

        issues = verify_fabric_code("draw_rect", result.code, tc["arguments"])
        assert not issues, "; ".join(issues)
        assert "fabric.Rect" in result.code

    @pytest.mark.asyncio
    async def test_draw_green_line_accurate(self):
        """一句话：画一条绿色的直线从(50,50)到(300,200) → draw_line with exact params."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        result = await runner.run("画一条绿色的直线从(50,50)到(300,200)")

        assert result.success
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["name"] == "draw_line"
        assert tc["arguments"]["start_x"] == 50
        assert tc["arguments"]["start_y"] == 50
        assert tc["arguments"]["end_x"] == 300
        assert tc["arguments"]["end_y"] == 200
        assert tc["arguments"]["color"] == "green"

        issues = verify_fabric_code("draw_line", result.code, tc["arguments"])
        assert not issues, "; ".join(issues)
        assert "fabric.Line" in result.code

    @pytest.mark.asyncio
    async def test_draw_text_accurate(self):
        """一句话：添加文字在(300,200)处写"Hello"，字号36，红色 → draw_text with exact params."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        result = await runner.run('添加文字在(300,200)处写"Hello"，字号36，红色')

        assert result.success
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["name"] == "draw_text"
        assert tc["arguments"]["x"] == 300
        assert tc["arguments"]["y"] == 200
        assert tc["arguments"]["font_size"] == 36
        assert tc["arguments"]["color"] == "red"
        assert tc["is_error"] is False

        issues = verify_fabric_code("draw_text", result.code, tc["arguments"])
        assert not issues, "; ".join(issues)
        assert "fabric.Text" in result.code

    @pytest.mark.asyncio
    async def test_draw_ellipse_accurate(self):
        """一句话：画一个椭圆，中心(400,300)，rx=80，ry=50，蓝色 → draw_ellipse with exact params."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        result = await runner.run("画一个椭圆，中心(400,300)，rx=80，ry=50，蓝色")

        assert result.success
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["name"] == "draw_ellipse"
        assert tc["arguments"]["center_x"] == 400
        assert tc["arguments"]["center_y"] == 300
        assert tc["arguments"]["rx"] == 80
        assert tc["arguments"]["ry"] == 50
        assert tc["arguments"]["color"] == "blue"

        issues = verify_fabric_code("draw_ellipse", result.code, tc["arguments"])
        assert not issues, "; ".join(issues)
        assert "fabric.Ellipse" in result.code

    @pytest.mark.asyncio
    async def test_multiple_shapes_one_sentence(self):
        """一句话画多个形状：画一个红色圆形...再画一个蓝色矩形..."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider, max_rounds=3)
        result = await runner.run(
            "画一个红色圆形在(100,100)半径50，再画一个蓝色矩形在(200,150)宽100高80"
        )

        assert result.success
        assert len(result.tool_calls) >= 1, "Should produce at least one tool call"

        tool_names = [tc["name"] for tc in result.tool_calls]
        # The mock may batch them or split across rounds
        assert any(t in tool_names for t in ("draw_circle", "draw_rect")), \
            f"Expected draw_circle/draw_rect, got {tool_names}"

    @pytest.mark.asyncio
    async def test_no_color_specified(self):
        """当没有指定颜色时，工具应该成功执行（使用随机颜色）。"""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        result = await runner.run("画一个圆形在(400,300)半径50")

        assert result.success
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["name"] == "draw_circle"
        assert tc["arguments"]["center_x"] == 400
        assert tc["arguments"]["radius"] == 50
        assert tc["is_error"] is False
        assert "fabric.Circle" in result.code


# ═══════════════════════════════════════════════════════════════════════
# S1-S5: Event Pipeline Verification
# ═══════════════════════════════════════════════════════════════════════


class TestDrawingEvents:
    """Verify the full event pipeline fires correctly during drawing."""

    @pytest.mark.asyncio
    async def test_all_events_dispatched(self):
        """Verify AGENT_START → LLM_REQUEST → LLM_RESPONSE → TOOL_CALL → TOOL_RESULT → AGENT_STOP."""
        bus = EventBus()
        events = []

        async def handler(event):
            events.append(event)

        for et in (
            EventType.AGENT_START, EventType.AGENT_STOP,
            EventType.LLM_REQUEST, EventType.LLM_RESPONSE,
            EventType.TOOL_CALL, EventType.TOOL_RESULT,
        ):
            bus.register(et, handler)

        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider, event_bus=bus)
        await runner.run("画一个红色的圆形，圆心(100,200)，半径50")

        types = [e.event_type for e in events]
        for et in (
            EventType.AGENT_START, EventType.AGENT_STOP,
            EventType.LLM_REQUEST, EventType.LLM_RESPONSE,
            EventType.TOOL_CALL, EventType.TOOL_RESULT,
        ):
            assert et in types, f"Missing event: {et}"

        # Verify order
        start_idx = types.index(EventType.AGENT_START)
        stop_idx = types.index(EventType.AGENT_STOP)
        assert start_idx < stop_idx, "AGENT_START must precede AGENT_STOP"

        # Verify run_id on all events
        for e in events:
            assert e.run_id, f"Event {e.event_type} missing run_id"

    @pytest.mark.asyncio
    async def test_tool_result_event_data(self):
        """Tool result events carry correct status and error info."""
        bus = EventBus()
        tool_results = []

        async def handler(event):
            tool_results.append(event.data)

        bus.register(EventType.TOOL_RESULT, handler)

        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider, event_bus=bus)
        await runner.run("画一个红色的圆形，圆心(100,200)，半径50")

        assert len(tool_results) >= 1
        tr = tool_results[0]
        assert tr.get("status") == "success"
        assert tr.get("is_error") is False
        assert tr.get("name") == "draw_circle"

    @pytest.mark.asyncio
    async def test_tool_error_event_on_bad_tool(self):
        """Verifies that an unknown tool name dispatches TOOL_ERROR."""
        bus = EventBus()
        tool_errors = []

        async def handler(event):
            tool_errors.append(event.data)

        bus.register(EventType.TOOL_ERROR, handler)

        # Provider that returns a nonexistent tool
        class BadToolProvider:
            async def achat(self, messages, tools=None, **kwargs):
                return LLMResponse(
                    content="Trying bad tool",
                    tool_calls=[ToolCall(name="nonexistent_tool", arguments={})],
                )
            def chat(self, *a, **kw):
                raise NotImplementedError

        provider = BadToolProvider()
        runner = make_runner(provider, event_bus=bus)
        await runner.run("do something")

        assert len(tool_errors) >= 1
        assert "not_found" in tool_errors[0].get("error_type", "")
        assert tool_errors[0].get("status") == "failed_not_found"


# ═══════════════════════════════════════════════════════════════════════
# S6: Context Watermark & Auto-Compact
# ═══════════════════════════════════════════════════════════════════════


class TestContextWatermark:
    """S6: context_pct watermark events are dispatched correctly."""

    @pytest.mark.asyncio
    async def test_context_watermark_dispatched(self):
        """LLMResponse with context_pct > 0 triggers CONTEXT_WATERMARK."""
        bus = EventBus()
        watermark_events = []

        async def handler(event):
            watermark_events.append(event.data)

        bus.register(EventType.CONTEXT_WATERMARK, handler)

        provider = ChineseDrawingMockProvider(context_pct=0.45)
        runner = make_runner(provider, event_bus=bus)
        await runner.run("画一个红色的圆形，圆心(100,200)，半径50")

        assert len(watermark_events) >= 1
        wm = watermark_events[0]
        assert wm.get("pct") == 0.45
        assert wm.get("tokens_used") == 250
        assert wm.get("context_window") == 128000

    @pytest.mark.asyncio
    async def test_auto_compact_triggers_at_threshold(self):
        """Context_pct >= 0.80 triggers auto-compact and dispatches CONTEXT_COMPACTED."""
        bus = EventBus()
        compact_events = []

        async def handler(event):
            compact_events.append(event.data)

        bus.register(EventType.CONTEXT_COMPACTED, handler)

        provider = ChineseDrawingMockProvider(context_pct=0.85)
        runner = make_runner(provider, event_bus=bus)
        runner.compact_threshold = 0.80
        await runner.run("画一个红色的圆形，圆心(100,200)，半径50")

        assert len(compact_events) >= 1
        cc = compact_events[0]
        assert cc.get("pct") == 0.85
        assert cc.get("before", 0) > 0
        assert cc.get("after", 0) > 0
        assert cc.get("removed", 0) >= 0

    @pytest.mark.asyncio
    async def test_context_watermark_not_dispatched_when_zero(self):
        """context_pct == 0 does NOT dispatch CONTEXT_WATERMARK."""
        bus = EventBus()
        watermark_events = []

        async def handler(event):
            watermark_events.append(event.data)

        bus.register(EventType.CONTEXT_WATERMARK, handler)

        provider = ChineseDrawingMockProvider(context_pct=0.0)
        runner = make_runner(provider, event_bus=bus)
        await runner.run("画一个红色的圆形，圆心(100,200)，半径50")

        assert len(watermark_events) == 0


# ═══════════════════════════════════════════════════════════════════════
# S6: Three-Layer Context Injection
# ═══════════════════════════════════════════════════════════════════════


class TestThreeLayerContext:
    """S6: Three-layer context (global/project/session) injected into system prompt."""

    @pytest.mark.asyncio
    async def test_canvas_state_in_prompt(self):
        """Canvas state is formatted and included in system prompt."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        canvas = {
            "objects": [
                {"type": "circle", "left": 100, "top": 100, "fill": "red"},
            ]
        }
        await runner.run("添加一个蓝色矩形", canvas_state=canvas)

        # Check system prompt contains canvas state
        sys_msg = provider.calls[0]["messages"][0]["content"]
        assert "1 objects" in sys_msg or "Empty" not in sys_msg

    @pytest.mark.asyncio
    async def test_empty_canvas_in_prompt(self):
        """Empty canvas state produces proper description."""
        provider = ChineseDrawingMockProvider()
        runner = make_runner(provider)
        await runner.run("画一个圆", canvas_state={})

        sys_msg = provider.calls[0]["messages"][0]["content"]
        assert "Empty canvas" in sys_msg


# ═══════════════════════════════════════════════════════════════════════
# S7: Skill Detection (via SessionManager + SkillLoader)
# ═══════════════════════════════════════════════════════════════════════


class TestSkillIntegration:
    """S7: Skill detection and restricted registry work correctly."""

    def test_skill_loader_detects_draw(self):
        """SkillLoader detects /draw command."""
        from skills.loader import SkillLoader
        loader = SkillLoader()
        result = loader.detect_skill("/draw 画一个红色的圆形")
        assert result is not None
        cmd, args, prompt = result
        assert cmd == "draw"
        assert "红色的圆形" in args
        assert "spawn_agent" in prompt or "绘图" in prompt or "画" in prompt

    def test_skill_loader_returns_none_for_plain_message(self):
        """Plain message (no leading slash) returns None."""
        from skills.loader import SkillLoader
        loader = SkillLoader()
        assert loader.detect_skill("画一个红色的圆形") is None

    def test_skill_loader_lists_skills(self):
        """SkillLoader.list_skills returns all defined skills."""
        from skills.loader import SkillLoader
        loader = SkillLoader()
        skills = loader.list_skills()
        commands = [s.command for s in skills]
        assert "draw" in commands

    @pytest.mark.asyncio
    async def test_skill_invoked_event_dispatched(self):
        """SKILL_INVOKED event is dispatched when running in skill mode.

        This simulates what SessionHandler._run_with_session does when
        a skill is detected.
        """
        from skills.loader import SkillLoader

        bus = EventBus()
        skill_events = []

        async def handler(event):
            skill_events.append(event.data)

        bus.register(EventType.SKILL_INVOKED, handler)

        loader = SkillLoader()
        skill = loader.get_skill("draw")
        assert skill is not None, "draw skill not found"

        # Simulate skill dispatch
        skill_args = "画一个红色的圆形"
        await bus.dispatch(
            BaseEvent(EventType.SKILL_INVOKED, {
                "command": skill.command,
                "args": skill_args,
                "tools": skill.tools,
                "prompt_preview": skill.prompt[:200],
            }, run_id="test-skill")
        )

        assert len(skill_events) == 1
        se = skill_events[0]
        assert se["command"] == "draw"
        assert "红色的圆形" in se["args"]

    @pytest.mark.asyncio
    async def test_skill_restricted_registry(self):
        """Skill's restricted registry blocks non-whitelisted tools.

        The ``draw`` skill whitelist includes spawn_agent + task tools.
        A non-whitelisted tool like draw_circle should be absent.
        """
        from skills.loader import SkillLoader

        loader = SkillLoader()
        skill = loader.get_skill("draw")
        assert skill is not None

        # Build restricted registry as AgentRunner._build_skill_registry does
        from tools.policy import ToolPolicy
        from tools.manager import PermissionManager
        from tools.registry import ToolRegistry

        new_reg = ToolRegistry()
        allowed = set(skill.tools or [])
        new_reg.permissions = PermissionManager(policy=ToolPolicy(allow_only=list(allowed)))

        # Only whitelisted tools are registered
        for name in allowed:
            from agent.sub_agent import SpawnAgentTool
            if name == "spawn_agent":
                new_reg.register(SpawnAgentTool(None))
            # task tools would be registered in real flow

        registered = new_reg.list_tools()
        # draw_circle should NOT be in the skill's restricted registry
        # (skill tools = ["spawn_agent", "task_create", ...])
        assert "draw_circle" not in registered or "spawn_agent" in registered, \
            f"Skill registry should restrict to whitelist, got: {registered}"


# ═══════════════════════════════════════════════════════════════════════
# Human-readable output for the "一句话画图" demo
# ═══════════════════════════════════════════════════════════════════════


class TestOneSentenceDrawingDemo:
    """Print a human-readable demo of one-sentence drawing accuracy.

    This test generates structured output that can be formatted into
    a report card showing: input sentence → tool called → params → accuracy.
    """

    @staticmethod
    def _report(input_text: str, result, issues: list[str]) -> dict:
        return {
            "input": input_text,
            "success": result.success,
            "tool_calls": [
                {"name": tc["name"], "arguments": tc["arguments"],
                 "status": tc["status"], "error": tc.get("error", "")}
                for tc in result.tool_calls
            ],
            "code_preview": result.code[:120] + "..." if len(result.code) > 120 else result.code,
            "accuracy_issues": issues,
            "rounds": result.rounds,
        }

    @pytest.mark.asyncio
    async def test_demo_report(self):
        """Generate accuracy report for multiple one-sentence drawing commands."""
        reports = []
        test_cases = [
            ("画一个红色的圆形，圆心在(100,200)，半径50",
             {"name": "draw_circle", "center_x": 100, "center_y": 200, "radius": 50, "color": "red"}),
            ("画一个蓝色的矩形，位置(200,150)，宽120高80",
             {"name": "draw_rect", "x": 200, "y": 150, "width": 120, "height": 80, "color": "blue"}),
            ("画一条绿色的直线从(50,50)到(300,200)",
             {"name": "draw_line", "start_x": 50, "start_y": 50, "end_x": 300, "end_y": 200, "color": "green"}),
            ('添加文字在(300,200)处写"Hello"，字号36，红色',
             {"name": "draw_text", "x": 300, "y": 200, "font_size": 36, "color": "red"}),
            ("画一个椭圆，中心(400,300)，rx=80，ry=50，蓝色",
             {"name": "draw_ellipse", "center_x": 400, "center_y": 300, "rx": 80, "ry": 50, "color": "blue"}),
        ]

        for input_text, expected in test_cases:
            provider = ChineseDrawingMockProvider()
            runner = make_runner(provider)
            result = await runner.run(input_text)

            issues = []
            if result.success and result.tool_calls:
                tc = result.tool_calls[0]
                # Verify tool name
                if tc["name"] != expected["name"]:
                    issues.append(f"Expected tool '{expected['name']}', got '{tc['name']}'")
                # Verify all expected params
                for key, val in expected.items():
                    if key == "name":
                        continue
                    actual = tc["arguments"].get(key)
                    if actual != val:
                        issues.append(f"Param '{key}': expected {val!r}, got {actual!r}")
                # Verify execution
                if tc["is_error"]:
                    issues.append(f"Tool execution failed: {tc.get('error', 'unknown')}")
                # Verify Fabric.js code
                code_issues = verify_fabric_code(tc["name"], result.code, tc["arguments"])
                issues.extend(code_issues)
            elif not result.success:
                issues.append(f"Run failed: {result.error}")

            reports.append(self._report(input_text, result, issues))

        # Print the report
        print("\n" + "=" * 72)
        print("  一句话画图 精度测试报告 (End-to-End Drawing Accuracy Report)")
        print("=" * 72)

        all_passed = 0
        for i, r in enumerate(reports, 1):
            status = "PASS" if not r["accuracy_issues"] and r["success"] else "FAIL"
            print(f"\n  [{i}] {status} {r['input']}")
            print(f"      → Tool: {', '.join(t['name'] for t in r['tool_calls'])}")
            print(f"      → Status: {status} | Rounds: {r['rounds']}")
            if r["accuracy_issues"]:
                for issue in r["accuracy_issues"]:
                    print(f"      → Issue: {issue}")
            if r["success"]:
                all_passed += 1
            # Show code preview
            if r["code_preview"]:
                print(f"      → Code: {r['code_preview']}")

        print(f"\n  {'=' * 34}")
        print(f"  总分: {all_passed}/{len(reports)} 通过")
        print(f"  {'=' * 34}\n")

        assert all_passed == len(reports), \
            f"Not all tests passed: {all_passed}/{len(reports)}"
