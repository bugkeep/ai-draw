"""Tests for DrawingModeRouter, domain models, and routing rules."""

import pytest
import asyncio
from assets.domain.enums import DrawingMode, AssetType, AssetErrorCode
from assets.domain.models import DrawingRoute, AssetSearchRequest
from assets.domain.errors import (
    AssetError, AssetProviderError, AssetDownloadError,
    AssetSanitizeError, AssetCacheError, AssetImportError,
)
from agent.router import DrawingModeRouter
from agent.prompts import get_mode_prompt, BASE_SYSTEM_PROMPT, PLANNING_SYSTEM_PROMPT


# ── Domain model tests ──────────────────────────────────────────────────────

class TestDrawingMode:
    def test_values(self):
        assert DrawingMode.PRIMITIVE.value == "primitive"
        assert DrawingMode.DIAGRAM.value == "diagram"
        assert DrawingMode.VECTOR_ASSET.value == "vector_asset"
        assert DrawingMode.RASTER_ASSET.value == "raster_asset"
        assert DrawingMode.IMAGE_GENERATION.value == "image_generation"
        assert DrawingMode.CANVAS_EDIT.value == "canvas_edit"


class TestAssetErrorCode:
    def test_values(self):
        assert AssetErrorCode.PROVIDER_UNAVAILABLE.value == "provider_unavailable"
        assert AssetErrorCode.DOWNLOAD_FAILED.value == "download_failed"
        assert AssetErrorCode.UNSAFE_URL.value == "unsafe_url"
        assert AssetErrorCode.IMPORT_FAILED.value == "import_failed"


class TestDrawingRoute:
    def test_basic_route(self):
        route = DrawingRoute(
            mode=DrawingMode.VECTOR_ASSET,
            confidence=0.95,
            subject="smiling face",
            reason="common icon",
            requires_search=True,
        )
        assert route.mode == DrawingMode.VECTOR_ASSET
        assert route.confidence == 0.95
        assert route.subject == "smiling face"
        assert route.requires_search is True
        assert route.requires_existing_object is False

    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            DrawingRoute(mode=DrawingMode.PRIMITIVE, confidence=1.5,
                         subject="test", reason="test")

    def test_canvas_edit_route(self):
        route = DrawingRoute(
            mode=DrawingMode.CANVAS_EDIT,
            confidence=0.80,
            subject="circle",
            reason="move request",
            requires_search=False,
            requires_existing_object=True,
        )
        assert route.requires_existing_object is True


class TestAssetError:
    def test_base_error(self):
        err = AssetError(AssetErrorCode.NO_RESULTS, "nothing found")
        assert err.code == AssetErrorCode.NO_RESULTS
        assert "nothing found" in str(err)

    def test_provider_error(self):
        err = AssetProviderError("timeout", provider="iconify")
        assert err.provider == "iconify"
        assert err.code == AssetErrorCode.PROVIDER_UNAVAILABLE

    def test_download_error(self):
        err = AssetDownloadError(AssetErrorCode.UNSAFE_URL, "blocked")
        assert err.code == AssetErrorCode.UNSAFE_URL

    def test_sanitize_error(self):
        err = AssetSanitizeError("injected script")
        assert err.code == AssetErrorCode.SVG_SANITIZE_FAILED


# ── Router classification tests ────────────────────────────────────────────

class TestDrawingModeRouter:
    @pytest.fixture
    def router(self):
        return DrawingModeRouter()

    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    def route(self, router, msg, canvas_state=None):
        return asyncio.run(router.route(msg, canvas_state))

    # ── Primitive ──────────────────────────────────────────────────────

    def test_primitive_circle(self, router):
        r = self.route(router, "画一个圆")
        assert r.mode == DrawingMode.PRIMITIVE
        assert r.confidence >= 0.60
        assert not r.requires_search

    def test_primitive_rectangle(self, router):
        r = self.route(router, "画一个矩形")
        assert r.mode == DrawingMode.PRIMITIVE

    def test_primitive_triangle(self, router):
        r = self.route(router, "画一个三角形")
        assert r.mode == DrawingMode.PRIMITIVE

    def test_primitive_line(self, router):
        r = self.route(router, "画一条直线")
        assert r.mode == DrawingMode.PRIMITIVE

    def test_primitive_ellipse(self, router):
        r = self.route(router, "画一个椭圆")
        assert r.mode == DrawingMode.PRIMITIVE

    # ── Canvas Edit ────────────────────────────────────────────────────

    def test_canvas_edit_move(self, router):
        r = self.route(router, "把笑脸移动到右上角")
        assert r.mode == DrawingMode.CANVAS_EDIT

    def test_canvas_edit_color(self, router):
        r = self.route(router, "把笑脸改成蓝色")
        assert r.mode == DrawingMode.CANVAS_EDIT

    def test_canvas_edit_undo(self, router):
        r = self.route(router, "撤销")
        assert r.mode == DrawingMode.CANVAS_EDIT

    def test_canvas_edit_redo(self, router):
        r = self.route(router, "重做")
        assert r.mode == DrawingMode.CANVAS_EDIT

    def test_canvas_edit_short_with_objects(self, router):
        r = self.route(router, "放大", canvas_state={"objects": [{"type": "circle"}]})
        assert r.mode == DrawingMode.CANVAS_EDIT
        assert r.requires_existing_object

    def test_canvas_edit_replace(self, router):
        r = self.route(router, "换一个更可爱的笑脸")
        assert r.mode == DrawingMode.CANVAS_EDIT

    # ── Diagram ────────────────────────────────────────────────────────

    def test_diagram_flowchart(self, router):
        r = self.route(router, "画一个微服务调用流程图")
        assert r.mode == DrawingMode.DIAGRAM

    def test_diagram_uml(self, router):
        r = self.route(router, "画一个UML类图")
        assert r.mode == DrawingMode.DIAGRAM

    def test_diagram_topology(self, router):
        r = self.route(router, "画一个网络拓扑图")
        assert r.mode == DrawingMode.DIAGRAM

    # ── Vector Asset ───────────────────────────────────────────────────

    def test_vector_smiley(self, router):
        r = self.route(router, "画一个黄色笑脸")
        assert r.mode == DrawingMode.VECTOR_ASSET
        assert r.requires_search

    def test_vector_heart(self, router):
        r = self.route(router, "画一个红色爱心")
        assert r.mode == DrawingMode.VECTOR_ASSET

    def test_vector_car(self, router):
        r = self.route(router, "画一辆蓝色汽车")
        assert r.mode == DrawingMode.VECTOR_ASSET

    def test_vector_cat(self, router):
        r = self.route(router, "画一只猫")
        assert r.mode == DrawingMode.VECTOR_ASSET

    def test_vector_cloud(self, router):
        r = self.route(router, "画一朵白云")
        assert r.mode == DrawingMode.VECTOR_ASSET

    def test_primitive_tree(self, router):
        r = self.route(router, "画一棵大树")
        assert r.mode == DrawingMode.PRIMITIVE

    def test_vector_star(self, router):
        r = self.route(router, "画一颗星星")
        assert r.mode == DrawingMode.VECTOR_ASSET

    def test_vector_rainbow(self, router):
        r = self.route(router, "画一条彩虹")
        assert r.mode == DrawingMode.VECTOR_ASSET

    # ── Image Generation ───────────────────────────────────────────────

    def test_image_gen_scene(self, router):
        r = self.route(router, "画一个东京雨夜街景")
        assert r.mode == DrawingMode.IMAGE_GENERATION

    def test_image_gen_detailed_illustration(self, router):
        r = self.route(router, "画一幅细节丰富的森林插画")
        assert r.mode == DrawingMode.IMAGE_GENERATION

    def test_image_gen_spoken_multi_subject_scene(self, router):
        r = self.route(
            router,
            "画一棵树 树旁边应该有一座房子 树旁边有一个人 人旁边有一条河",
        )
        assert r.mode == DrawingMode.IMAGE_GENERATION

    # ── Subject extraction ─────────────────────────────────────────────

    def test_subject_extraction(self, router):
        r = self.route(router, "画一个黄色笑脸")
        assert r.subject == "黄色笑脸"

    def test_subject_short(self, router):
        r = self.route(router, "画猫")
        assert "猫" in r.subject

    def test_subject_no_prefix(self, router):
        r = self.route(router, "撤销")
        assert r.subject == "撤销"

    # ── Style inference ────────────────────────────────────────────────

    def test_style_flat(self, router):
        r = self.route(router, "画一个扁平风格的笑脸")
        assert r.style == "flat"

    def test_style_cartoon(self, router):
        r = self.route(router, "画一个卡通的猫")
        assert r.style == "cartoon"


# ── Prompt fragment tests ───────────────────────────────────────────────────

class TestModePrompts:
    def test_primitive_prompt(self):
        p = get_mode_prompt("primitive")
        assert "Primitive Drawing Mode" in p
        assert "draw_circle" in p

    def test_vector_asset_prompt(self):
        p = get_mode_prompt("vector_asset")
        assert "Vector Asset Mode" in p
        assert "search_vector_asset" in p
        assert "import_vector_asset" in p

    def test_canvas_edit_prompt(self):
        p = get_mode_prompt("canvas_edit")
        assert "Canvas Edit Mode" in p
        assert "object_id" in p
        assert "NOT by array index" in p

    def test_diagram_prompt(self):
        p = get_mode_prompt("diagram")
        assert "Diagram Drawing Mode" in p
        assert "draw_text" in p

    def test_image_gen_prompt(self):
        p = get_mode_prompt("image_generation")
        assert "Image Generation Mode" in p
        assert "editable vector scene" in p
        assert "at least 6" in p
        assert "Do NOT" in p and "center every unspecified element" in p

    def test_raster_prompt(self):
        p = get_mode_prompt("raster_asset")
        assert "Raster Asset Mode" in p

    def test_unknown_mode(self):
        p = get_mode_prompt("nonexistent")
        assert p == ""


# ── Prompt template tests ───────────────────────────────────────────────────

class TestPromptTemplates:
    def test_base_prompt_has_mode_placeholder(self):
        assert "{mode_prompt}" in BASE_SYSTEM_PROMPT

    def test_base_prompt_has_canvas_placeholder(self):
        assert "{canvas_state}" in BASE_SYSTEM_PROMPT

    def test_planning_prompt_has_mode_placeholder(self):
        assert "{mode_prompt}" in PLANNING_SYSTEM_PROMPT

    def test_planning_prompt_has_canvas_placeholder(self):
        assert "{canvas_state}" in PLANNING_SYSTEM_PROMPT

    def test_base_prompt_formatting(self):
        prompt = BASE_SYSTEM_PROMPT.format(
            canvas_state="Empty canvas", mode_prompt=""
        )
        assert "Empty canvas" in prompt


# ── Complete routing accuracy ───────────────────────────────────────────────

class TestRoutingAccuracy:
    """Fixed test set from the plan: 15 diverse inputs."""

    @pytest.fixture
    def router(self):
        return DrawingModeRouter()

    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    TEST_CASES = [
        ("画一个圆", DrawingMode.PRIMITIVE),
        ("画一个矩形", DrawingMode.PRIMITIVE),
        ("画一个三角形", DrawingMode.PRIMITIVE),
        ("画一条直线", DrawingMode.PRIMITIVE),
        ("把笑脸移动到右上角", DrawingMode.CANVAS_EDIT),
        ("把笑脸改成蓝色", DrawingMode.CANVAS_EDIT),
        ("撤销", DrawingMode.CANVAS_EDIT),
        ("画一个微服务调用流程图", DrawingMode.DIAGRAM),
        ("画一个UML类图", DrawingMode.DIAGRAM),
        ("画一个黄色笑脸", DrawingMode.VECTOR_ASSET),
        ("画一个红色爱心", DrawingMode.VECTOR_ASSET),
        ("画一辆蓝色汽车", DrawingMode.VECTOR_ASSET),
        ("画一只猫", DrawingMode.VECTOR_ASSET),
        ("画一朵白云", DrawingMode.VECTOR_ASSET),
        ("画一个东京雨夜街景", DrawingMode.IMAGE_GENERATION),
    ]

    def test_all_cases(self, router, event_loop):
        passed = 0
        for msg, expected in self.TEST_CASES:
            route = event_loop.run_until_complete(router.route(msg))
            if route.mode == expected:
                passed += 1
            else:
                print(f"  FAIL: '{msg}' → {route.mode.value} (expected {expected.value})")
        assert passed == len(self.TEST_CASES), f"{passed}/{len(self.TEST_CASES)} passed"
