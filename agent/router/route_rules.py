"""Hard routing constraints for the DrawingModeRouter.

These rules encode deterministic decisions that MUST NOT be overridden
by the LLM.  They act as guardrails before the router makes its final
classification.

Each rule is a callable that receives (message, canvas_state, history)
and returns a list of (mode, delta) tuples where delta is a signed
confidence adjustment.
"""

from __future__ import annotations
import re
from typing import Any

from assets.domain.enums import DrawingMode

RE_RULES: list[tuple[re.Pattern, DrawingMode, float, str]] = []


def _cr(pattern: str, mode: DrawingMode, delta: float, reason: str):
    """Compile a regex rule and append it to RE_RULES."""
    RE_RULES.append((re.compile(pattern, re.IGNORECASE), mode, delta, reason))


# ── CANVAS_EDIT rules (checked first — modification requests win) ──────────

# 移动、拖动
_cr(r"^把.*(?:移动|挪|拖动|拖拽|移到|挪到|拖动到)", DrawingMode.CANVAS_EDIT, 0.50, "explicit move request")
_cr(r"(?:往|向|朝).*(?:移动|挪|移|拖)", DrawingMode.CANVAS_EDIT, 0.45, "directional move request")
_cr(r"(?:放大|缩小|拉大|拉小|变大|变小|扩大|缩小一点)", DrawingMode.CANVAS_EDIT, 0.50, "explicit resize request")
_cr(r"(?:旋转|转动|转一下|rotate|顺时针|逆时针)", DrawingMode.CANVAS_EDIT, 0.50, "explicit rotate request")
_cr(r"(?:置顶|置底|最前面|最后面|上一层|下一层|上移一层|下移一层|bring.?front|send.?back)", DrawingMode.CANVAS_EDIT, 0.50, "explicit stacking order request")
_cr(r"(?:对齐|居中|左对齐|右对齐|顶部对齐|底部对齐|align)", DrawingMode.CANVAS_EDIT, 0.50, "explicit align request")
_cr(r"(?:均匀分布|平均分布|等距|横向分布|纵向分布|distribute)", DrawingMode.CANVAS_EDIT, 0.50, "explicit distribute request")
_cr(r"(?:复制|拷贝|再来一个|一样的|duplicate|copy)", DrawingMode.CANVAS_EDIT, 0.50, "explicit duplicate request")
_cr(r"(?:成组|组合|合并成组|取消成组|取消组合|拆开组合|ungroup|group)", DrawingMode.CANVAS_EDIT, 0.50, "explicit group request")
_cr(r"(?:透明|半透明|不透明度|透明度|opacity)", DrawingMode.CANVAS_EDIT, 0.50, "explicit opacity request")
_cr(r"(?:描边|边框|轮廓线|outline|stroke)", DrawingMode.CANVAS_EDIT, 0.50, "explicit stroke request")
_cr(r"(?:填充|油漆桶|渐变|吸管|取色|复制样式|fill|paint.?bucket|gradient|eyedropper|sample.?color|copy.?style)", DrawingMode.CANVAS_EDIT, 0.50, "explicit fill/style request")
_cr(r"(?:选中|选择|框选|区域选择|选取|套索|魔棒|选择相似|相似对象|select|marquee|region|lasso|magic.?wand|similar)", DrawingMode.CANVAS_EDIT, 0.50, "explicit select request")
_cr(r"(?:裁剪|裁掉|剪裁|crop)", DrawingMode.CANVAS_EDIT, 0.50, "explicit crop request")
_cr(r"(?:遮罩|剪贴|剪贴蒙版|蒙版|mask|clip)", DrawingMode.CANVAS_EDIT, 0.50, "explicit mask request")
_cr(r"(?:混合模式|正片叠底|滤色|叠加|变暗|变亮|差值|排除|blend|multiply|screen|overlay)", DrawingMode.CANVAS_EDIT, 0.50, "explicit blend mode request")
_cr(r"(?:滤镜|模糊|调亮|调暗|亮度|对比度|灰度|反色|饱和度|filter|blur|brightness|contrast|grayscale|invert|saturation)", DrawingMode.CANVAS_EDIT, 0.50, "explicit image filter request")
# 改颜色
_cr(r"(?:改|换|变成).*[颜颜]色", DrawingMode.CANVAS_EDIT, 0.50, "explicit color change")
_cr(r"(?:改成|变为|变成)\s*(?:红色|蓝色|绿色|黄色|黑色|白色|紫色|橙色|粉色|灰色)", DrawingMode.CANVAS_EDIT, 0.50, "explicit recolor")
# 删除
_cr(r"^把.*(?:删除|删掉|移除|去掉)", DrawingMode.CANVAS_EDIT, 0.50, "explicit delete")
_cr(r"^(?:删除|删掉|移除|去掉|清除)\s*(?:所有|全部|全部内容|画布)", DrawingMode.CANVAS_EDIT, 0.50, "explicit clear")
# 撤销/重做
_cr(r"^(?:撤销|回退|撤回|undo)", DrawingMode.CANVAS_EDIT, 0.60, "undo request")
_cr(r"^(?:重做|恢复|redo)", DrawingMode.CANVAS_EDIT, 0.60, "redo request")
# 换一个/替换
_cr(r"换一[个只]", DrawingMode.CANVAS_EDIT, 0.40, "replacement request")
_cr(r"替换", DrawingMode.CANVAS_EDIT, 0.40, "replacement request")

# ── PRIMITIVE rules (explicit geometry requests) ───────────────────────────

_cr(r"画一个?\s*(?:圆|圆形|圆圈|正圆)", DrawingMode.PRIMITIVE, 0.50, "explicit circle request")
_cr(r"画一个?\s*(?:矩形|长方形|正方形|方块|方框)", DrawingMode.PRIMITIVE, 0.50, "explicit rectangle request")
_cr(r"画一个?\s*(?:直线|线段|线|线条)", DrawingMode.PRIMITIVE, 0.50, "explicit line request")
_cr(r"画一个?\s*(?:三角形|三角)", DrawingMode.PRIMITIVE, 0.50, "explicit triangle request")
_cr(r"画一个?\s*(?:椭圆|椭圆形|椭圆)", DrawingMode.PRIMITIVE, 0.50, "explicit ellipse request")
_cr(r"画一个?\s*(?:五角星|星星)", DrawingMode.PRIMITIVE, 0.40, "explicit star request")
_cr(r"画\s*(?:一[个棵只朵座条颗株])?\s*(?:树|树木|花朵|花|草|叶子|森林)", DrawingMode.PRIMITIVE, 0.45, "nature object → draw with primitives")
_cr(r"画一个?\s*(?:多边形|五边形|六边形)", DrawingMode.PRIMITIVE, 0.50, "explicit polygon request")
_cr(r"画一个?\s*(?:路径|path|bezier|贝塞尔|曲线)", DrawingMode.PRIMITIVE, 0.50, "explicit path/curve request")
_cr(r"画一个?\s*(?:文本|文字|标签|标注)", DrawingMode.PRIMITIVE, 0.50, "explicit text request")
_cr(r"(?:同心圆|同星圆|同圆心|套圆|靶心|concentric|bullseye)", DrawingMode.PRIMITIVE, 0.60, "explicit concentric circle request")

# ── DIAGRAM rules ──────────────────────────────────────────────────────────

_cr(r"(?:流程图|flow.?chart|架构图|部署图|拓扑图)", DrawingMode.DIAGRAM, 0.60, "diagram keyword")
_cr(r"(?:微服务|架构|拓扑|部署|网络).*(?:图|架构|拓扑)", DrawingMode.DIAGRAM, 0.50, "architecture diagram")
_cr(r"(?:类图|时序图|ER图|UML|uml|用例图|组件图)", DrawingMode.DIAGRAM, 0.60, "UML diagram")
_cr(r"(?:组织架构|思维导图|脑图|mind.?map)", DrawingMode.DIAGRAM, 0.50, "mind map / org chart")

# ── VECTOR_ASSET rules (common single objects → search SVG) ────────────────

_VECTOR_WORD = r"(?:画\s*(?:一[个只辆朵棵座条颗])?(?:\s*\S+)?\s*)"

_cr(_VECTOR_WORD + r"(?:笑脸|哭脸|表情|emoji)", DrawingMode.VECTOR_ASSET, 0.50, "facial expression icon")
_cr(_VECTOR_WORD + r"(?:爱心|心形|心)", DrawingMode.VECTOR_ASSET, 0.50, "heart icon")
_cr(_VECTOR_WORD + r"(?:汽车|车|轿车|卡车|公交|自行车|飞机|火车|船|摩托车)", DrawingMode.VECTOR_ASSET, 0.50, "vehicle icon")
_cr(_VECTOR_WORD + r"(?:猫|狗|兔子|小鸟|鱼|蝴蝶|蜜蜂|熊猫|老虎|狮子|企鹅|大象|猴子)", DrawingMode.VECTOR_ASSET, 0.50, "animal icon")
_cr(_VECTOR_WORD + r"(?:云|云朵|太阳|月亮|星星|彩虹|闪电|雪花)", DrawingMode.VECTOR_ASSET, 0.50, "weather icon")
_cr(_VECTOR_WORD + r"(?:花朵|花|草|叶子|森林)", DrawingMode.VECTOR_ASSET, 0.45, "nature icon")
_cr(_VECTOR_WORD + r"(?:房子|房屋|城堡|建筑|楼房|教堂|塔)", DrawingMode.VECTOR_ASSET, 0.45, "building icon")
_cr(_VECTOR_WORD + r"(?:苹果|香蕉|草莓|西瓜|水果|食物|汉堡|披萨|蛋糕|冰淇淋)", DrawingMode.VECTOR_ASSET, 0.45, "food icon")
_cr(_VECTOR_WORD + r"(?:电脑|手机|鼠标|键盘|打印机|屏幕|相机|电视机)", DrawingMode.VECTOR_ASSET, 0.45, "device icon")
_cr(_VECTOR_WORD + r"(?:国旗|旗帜|地图|地球|世界|中国)", DrawingMode.VECTOR_ASSET, 0.45, "map/globe icon")
_cr(_VECTOR_WORD + r"(?:礼物|礼盒|信封|邮件|铃铛|锁|钥匙|灯泡|齿轮|放大镜)", DrawingMode.VECTOR_ASSET, 0.45, "object icon")
_cr(_VECTOR_WORD + r"(?:人像|人物|头像|用户|小人|机器人)", DrawingMode.VECTOR_ASSET, 0.40, "person icon")
_cr(_VECTOR_WORD + r"(?:音符|音乐|播放|暂停|停止|录音|喇叭)", DrawingMode.VECTOR_ASSET, 0.40, "media icon")

# Also match bare object names (no "draw" prefix) for common icons
_cr(r"(?:笑脸|smiley|emoji|爱心|heart)", DrawingMode.VECTOR_ASSET, 0.30, "bare icon reference")
_cr(r"(?:猫|狗|猫猫|狗狗)", DrawingMode.VECTOR_ASSET, 0.30, "bare animal reference")

# ── RASTER_ASSET rules (real photo requests) ───────────────────────────────

_cr(r"(?:真实|真实照片|照片|写实|实拍)", DrawingMode.RASTER_ASSET, 0.50, "explicit photo request")
_cr(r"(?:找一张|搜索).*(?:照片|图片|图)", DrawingMode.RASTER_ASSET, 0.40, "image search request")

# ── IMAGE_GENERATION rules (complex scenes) ────────────────────────────────

_cr(r"(?:场景|街景|风景|风景画|山水|日落|日出|夜景)", DrawingMode.IMAGE_GENERATION, 0.50, "scene description")
_cr(r"(?:骑.*的.*|戴.*的.*|穿着.*的.*)", DrawingMode.IMAGE_GENERATION, 0.40, "character with modifiers")
_cr(r"(?:复杂|细节丰富|丰富细节|完整画面|完整场景|一幅.*(?:画|插画)|插画)", DrawingMode.IMAGE_GENERATION, 0.55, "explicit detailed composition")
_cr(
    r"(?:3d|三维|立体|透视|三分之四视角|斜侧面|纵深|空间感|结构完整|正常结构|真实结构|复杂结构)",
    DrawingMode.IMAGE_GENERATION,
    0.75,
    "perspective or structurally detailed subject",
)
_cr(
    r"(?:detailed|complex|perspective|three[- ]quarter|3/4 view|isometric|realistic structure)",
    DrawingMode.IMAGE_GENERATION,
    0.75,
    "detailed or perspective subject",
)
_cr(r"(?:背景|前景|远处|近处|周围|天空|地面).*(?:还有|以及|同时|旁边|远处|近处|前景|背景)", DrawingMode.IMAGE_GENERATION, 0.45, "multi-layer composition")

# ── Negative rules (de-boosts for misclassification) ────────────────────────

_cr(r"(?:照片|写实|真实照片)", DrawingMode.VECTOR_ASSET, -0.30, "photo request, not vector")
_cr(r"(?:流程图|架构|拓扑)", DrawingMode.VECTOR_ASSET, -0.40, "diagram request, not icon")


def apply_rules(message: str, canvas_state: dict[str, Any] | None = None,
                history: list[dict] | None = None) -> dict[DrawingMode, float]:
    """Apply all hard routing rules to produce a mode-confidence map.

    Returns a dictionary mapping each DrawingMode to its net confidence
    adjustment accumulated from all matching rules.
    """
    scores: dict[DrawingMode, float] = {mode: 0.0 for mode in DrawingMode}

    for pattern, mode, delta, _reason in RE_RULES:
        if pattern.search(message):
            scores[mode] = scores.get(mode, 0.0) + delta

    if _looks_like_multi_subject_scene(message):
        scores[DrawingMode.IMAGE_GENERATION] += 0.65

    # ── Canvas-state-derived rules ──────────────────────────────────────
    # If canvas is non-empty and message looks like a short feedback token,
    # boost CANVAS_EDIT.
    if canvas_state and canvas_state.get("objects"):
        obj_count = len(canvas_state["objects"])
        if obj_count > 0 and len(message.strip()) < 10:
            scores[DrawingMode.CANVAS_EDIT] = scores.get(DrawingMode.CANVAS_EDIT, 0.0) + 0.30
        if obj_count > 0 and _is_positional_reference(message):
            scores[DrawingMode.CANVAS_EDIT] = scores.get(DrawingMode.CANVAS_EDIT, 0.0) + 0.40

    return scores


def _is_positional_reference(msg: str) -> bool:
    """Check if message references a position on canvas (suggests edit, not new draw)."""
    return bool(re.search(r"(?:左上|右上|左下|右下|左边|右边|上面|下面|中间|旁边)", msg))


def _looks_like_multi_subject_scene(msg: str) -> bool:
    """Detect spoken scene descriptions with several related subjects."""
    subjects = set(re.findall(
        r"(树|房子|房屋|人|人物|河|河流|山|太阳|云|道路|桥|花|草地)",
        msg,
    ))
    relations = re.findall(r"(?:旁边|附近|左边|右边|前面|后面|远处|近处)", msg)
    return len(subjects) >= 3 or (len(subjects) >= 2 and len(relations) >= 2)
