import math
import re
from typing import Any


COLOR_WORDS = {
    "红": "#e03131", "蓝": "#1971c2", "绿": "#2b8a3e", "黄": "#f08c00",
    "紫": "#7048e8", "橙": "#e8590c", "粉": "#d6336c", "黑": "#212529",
}
COLOR_NAMES = {f"{name}色": value for name, value in COLOR_WORDS.items()}
SHAPE_NAMES = {
    "圆形": ("ellipse", 130, 130), "圆": ("ellipse", 130, 130),
    "椭圆": ("ellipse", 170, 110), "矩形": ("rectangle", 170, 110),
    "长方形": ("rectangle", 190, 105), "正方形": ("rectangle", 130, 130),
    "菱形": ("diamond", 150, 130),
}
CREATE_PATTERN = re.compile(
    r"^(?:请)?(?:画|绘制|创建|添加)(?:一个|一幅|一张)?"
    r"(?P<color>红色|蓝色|绿色|黄色|紫色|橙色|粉色|黑色)?(?:的)?"
    r"(?P<shape>圆形|椭圆|长方形|正方形|矩形|菱形|圆)"
    r"(?:[，,\s]*名字叫(?P<name>[^。.!！]{1,40}))?[。.!！]?$"
)
MOVE_PATTERN = re.compile(
    r"^(?:请)?(?:把)?(?:它|这个|选中的|刚才的)?(?:图形|圆形|圆|矩形|方形|文字|文本|箭头)?"
    r"(?:向|往)?(左|右|上|下)(?:边|方)?(?:移动|挪动|移|挪)\s*(\d+(?:\.\d+)?)"
    r"(?:\s*(?:像素|px))?[。.!！]?$"
)
COLOR_PATTERN = re.compile(
    r"^(?:请)?(?:把)?(?:它|这个|选中的|刚才的)?(?:图形|圆形|圆|矩形|方形|文字|文本|箭头)?"
    r"(?:的)?(?:颜色|填充)?(?:改成|变成|换成)(红色|蓝色|绿色|黄色|紫色|橙色|粉色|黑色)[。.!！]?$"
)
SELECT_PATTERN = re.compile(
    r"^(?:请)?(?:选中|选择)(?:它|这个|刚才的|选中的)?"
    r"(?:图形|圆形|圆|矩形|方形|文字|文本|箭头)?[。.!！]?$"
)
SMILEY_PATTERN = re.compile(
    r"^(?:请)?(?:用画笔|手绘|用笔|画笔)(?:画|绘制)?(?:一张|一个|一幅)?笑脸"
    r"(?:，(?:展示作画过程|分多笔显示轮廓、眼睛和嘴巴))?[。.!！]?$"
)


def _target_ids(message: str, document: dict[str, Any]) -> list[str]:
    elements = [element for element in document["elements"] if not element.get("isDeleted")]
    if not elements:
        return []
    type_name = None
    if "圆" in message:
        type_name = "ellipse"
    elif "矩形" in message or "方形" in message:
        type_name = "rectangle"
    elif "文字" in message or "文本" in message:
        type_name = "text"
    elif "箭头" in message:
        type_name = "arrow"
    if type_name:
        matches = [element for element in elements if element["type"] == type_name]
        return [matches[-1]["id"]] if matches else []
    selected = [element_id for element_id in document["selection"] if any(element["id"] == element_id for element in elements)]
    return selected or [elements[-1]["id"]]


def _stroke(points: list[tuple[float, float]], name: str) -> dict[str, Any]:
    return {"kind": "create_element", "args": {
        "type": "freedraw", "points": [(round(x, 2), round(y, 2)) for x, y in points],
        "name": name, "strokeColor": "#25283d", "strokeWidth": 3, "roughness": 0,
    }}


def smiley_strokes(document: dict[str, Any]) -> list[dict[str, Any]]:
    visible = [element for element in document["elements"] if not element.get("isDeleted")]
    offset = min(len(visible), 4) * 22
    center_x, center_y, radius = 450 + offset, 310 + offset, 115
    head = [(center_x + radius * math.cos(angle * 2 * math.pi / 56),
             center_y + radius * math.sin(angle * 2 * math.pi / 56)) for angle in range(57)]
    left_eye = [(center_x - 43 + 7 * math.cos(angle * 2 * math.pi / 14),
                 center_y - 30 + 10 * math.sin(angle * 2 * math.pi / 14)) for angle in range(15)]
    right_eye = [(center_x + 43 + 7 * math.cos(angle * 2 * math.pi / 14),
                  center_y - 30 + 10 * math.sin(angle * 2 * math.pi / 14)) for angle in range(15)]
    mouth = [(center_x + 63 * math.cos(angle), center_y + 12 + 62 * math.sin(angle))
             for angle in [math.pi * index / 24 for index in range(25)]]
    return [_stroke(head, "笑脸轮廓"), _stroke(left_eye, "左眼"),
            _stroke(right_eye, "右眼"), _stroke(mouth, "微笑")]


def fast_actions(message: str, document: dict[str, Any]) -> tuple[list[dict[str, Any]], str] | None:
    text = message.strip()
    if re.search(r"不要|别|取消", text):
        return None
    if SMILEY_PATTERN.fullmatch(text):
        return smiley_strokes(document), "已用画笔依次画出轮廓、眼睛和微笑。"

    creation = CREATE_PATTERN.fullmatch(text)
    if creation:
        shape, width, height = SHAPE_NAMES[creation["shape"]]
        color = COLOR_NAMES.get(creation["color"], "#343a40")
        visible_count = sum(not item.get("isDeleted") for item in document["elements"])
        offset = min(visible_count, 8) * 32
        name = creation["name"] or creation["shape"]
        return [{"kind": "create_element", "args": {
            "type": shape, "x": 260 + offset, "y": 180 + offset,
            "width": width, "height": height, "name": name,
            "strokeColor": color,
            "backgroundColor": color if creation["color"] else "transparent",
        }}], f"已画好{name}。"

    ids = _target_ids(text, document)
    if not ids:
        return None
    if SELECT_PATTERN.fullmatch(text):
        return [{"kind": "select_elements", "args": {"ids": ids}}], "已选中图形。"

    if re.fullmatch(r"(?:请)?(?:删除|删掉|移除)(?:它|这个|刚才的图形)[。.!！]?", text):
        return [{"kind": "delete_elements", "args": {"ids": ids}}], "已删除选中的图形。"

    move = MOVE_PATTERN.fullmatch(text)
    if move:
        direction, distance_text = move.groups()
        distance = float(distance_text)
        dx = distance if direction == "右" else -distance if direction == "左" else 0
        dy = distance if direction == "下" else -distance if direction == "上" else 0
        return [{"kind": "move_elements", "args": {"ids": ids, "dx": dx, "dy": dy}}], f"已移动 {distance:g} 像素。"

    color_match = COLOR_PATTERN.fullmatch(text)
    if color_match:
        color = COLOR_NAMES[color_match[1]]
        return [{"kind": "update_elements", "args": {"ids": ids, "patch": {
            "strokeColor": color, "backgroundColor": color,
        }}}], "已修改图形颜色。"
    return None
