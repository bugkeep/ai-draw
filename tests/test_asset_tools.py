"""Tests for the 4 asset agent tools: search, import, replace, list."""

from __future__ import annotations

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from tools.assets.search_vector_asset import (
    SearchVectorAssetTool,
    get_search_cache,
    get_latest_search_id,
    _SEARCH_CACHE,
    _LATEST_SEARCH_ID,
)
from tools.assets.import_vector_asset import ImportVectorAssetTool
from tools.assets.replace_vector_asset import ReplaceVectorAssetTool
from tools.assets.list_asset_candidates import ListAssetCandidatesTool
from tools.registry import ToolRegistry


# ── SearchVectorAssetTool tests ─────────────────────────────────────────────

class TestSearchVectorAssetTool:
    def test_definition(self):
        tool = SearchVectorAssetTool()
        defn = tool.definition()
        assert defn.name == "search_vector_asset"
        assert any(p.name == "query" for p in defn.parameters)
        assert any(p.name == "style" for p in defn.parameters)

    def test_execute_empty_query_returns_error(self):
        result = SearchVectorAssetTool().execute(query="")
        assert result.is_error
        assert "query is required" in result.error

    def test_execute_with_mock_provider(self):
        """Test the tool with a mocked search service that returns candidates."""
        tool = SearchVectorAssetTool()
        mock_svc = MagicMock()
        mock_svc.name = "mock"

        # Mock the search result
        from assets.services.search_service import SearchResult
        from assets.domain.models import AssetCandidate

        mock_result = SearchResult(
            search_id="search_test_1",
            normalized_query="smile",
            candidates=[
                AssetCandidate(
                    asset_id="iconify:mdi:emoticon-happy",
                    provider="iconify",
                    provider_asset_id="mdi:emoticon-happy",
                    title="Emoticon Happy",
                    format="svg",
                    final_score=0.91,
                ),
                AssetCandidate(
                    asset_id="iconify:mdi:emoticon-sad",
                    provider="iconify",
                    provider_asset_id="mdi:emoticon-sad",
                    title="Emoticon Sad",
                    format="svg",
                    final_score=0.30,
                ),
            ],
            provider_results={"iconify": 2},
            errors=[],
        )

        with patch.object(tool, '_search_service') as mock_service:
            mock_service.search = AsyncMock(return_value=mock_result)
            result = tool.execute(query="smile", limit=5)

        assert not result.is_error
        assert "2 candidates" in result.description or "2 candidates" in (result.description or "")
        assert "Emoticon Happy" in (result.description or "")
        assert result.data is not None
        assert len(result.data["candidates"]) == 2
        assert result.data["search_id"] == "search_test_1"

    def test_execute_no_results(self):
        """Test with an empty search result."""
        tool = SearchVectorAssetTool()
        from assets.services.search_service import SearchResult

        mock_result = SearchResult(
            search_id="search_empty",
            normalized_query="zzz",
            candidates=[],
            provider_results={},
            errors=[],
        )

        with patch.object(tool, '_search_service') as mock_service:
            mock_service.search = AsyncMock(return_value=mock_result)
            result = tool.execute(query="zzz")

        assert not result.is_error
        assert "No assets found" in (result.description or "")
        assert result.data is not None
        assert len(result.data["candidates"]) == 0

    def test_execute_populates_cache(self):
        """Search results should be cached for list_asset_candidates."""
        # Clear cache
        _SEARCH_CACHE.clear()

        tool = SearchVectorAssetTool()
        from assets.services.search_service import SearchResult
        from assets.domain.models import AssetCandidate

        mock_result = SearchResult(
            search_id="search_cache_test",
            normalized_query="cat",
            candidates=[
                AssetCandidate(
                    asset_id="iconify:mdi:cat",
                    provider="iconify",
                    provider_asset_id="mdi:cat",
                    title="Cat Icon",
                    format="svg",
                    final_score=0.85,
                ),
            ],
            provider_results={"iconify": 1},
            errors=[],
        )

        with patch.object(tool, '_search_service') as mock_service:
            mock_service.search = AsyncMock(return_value=mock_result)
            result = tool.execute(query="cat")

        assert "search_cache_test" in get_search_cache()
        assert get_latest_search_id() == "search_cache_test"
        cache_entry = get_search_cache()["search_cache_test"]
        assert cache_entry["query"] == "cat"
        assert len(cache_entry["candidates"]) == 1

    def test_registers_via_registry(self):
        """Tool should be findable via ToolRegistry."""
        registry = ToolRegistry()
        registry.register(SearchVectorAssetTool())
        tool = registry.get("search_vector_asset")
        assert tool is not None
        defn = tool.definition()
        assert defn.name == "search_vector_asset"


# ── ImportVectorAssetTool tests ─────────────────────────────────────────────

class TestImportVectorAssetTool:
    def test_definition(self):
        tool = ImportVectorAssetTool()
        defn = tool.definition()
        assert defn.name == "import_vector_asset"
        assert any(p.name == "asset_id" for p in defn.parameters)
        assert any(p.name == "object_id" for p in defn.parameters)
        assert any(p.name == "left" for p in defn.parameters)

    def test_execute_empty_asset_id(self):
        result = ImportVectorAssetTool().execute()
        assert result.is_error
        assert "asset_id is required" in result.error

    def test_execute_invalid_asset_id_format(self):
        result = ImportVectorAssetTool().execute(asset_id="no-colon")
        assert result.is_error

    def test_execute_generates_js_code(self):
        """With a mocked cache hit, the tool should generate valid Fabric.js code."""
        tool = ImportVectorAssetTool()
        # Mock the cache to simulate a cache hit
        with patch.object(tool, '_cache') as mock_cache:
            mock_cache.make_cache_key.return_value = "abc123"
            mock_cache.has_sanitized.return_value = True
            mock_cache.read_sanitized.return_value = b"<svg>...</svg>"

            result = tool.execute(
                asset_id="iconify:mdi:emoticon-happy",
                object_id="smiley_1",
                semantic_type="smiley_face",
                left=100,
                top=200,
                width=240,
                height=240,
            )

        assert not result.is_error
        assert result.code is not None
        assert "fabric.loadSVGFromURL" in result.code
        assert "fabric.util.groupSVGElements" in result.code
        assert "smiley_1" in result.code
        assert "smiley_face" in result.code
        assert "/assets/content/abc123.svg" in result.code
        assert result.data is not None
        assert result.data["object_id"] == "smiley_1"
        assert result.data["import_mode"] == "svg_group"

    def test_execute_auto_derives_ids(self):
        """When object_id and semantic_type are omitted, they should be auto-derived."""
        tool = ImportVectorAssetTool()
        with patch.object(tool, '_cache') as mock_cache:
            mock_cache.make_cache_key.return_value = "auto_derive_test"
            mock_cache.has_sanitized.return_value = True
            mock_cache.read_sanitized.return_value = b"<svg>...</svg>"

            result = tool.execute(
                asset_id="iconify:mdi:heart",
                left=300,
                top=200,
                width=100,
                height=100,
            )

        assert not result.is_error
        assert result.data["asset_id"] == "iconify:mdi:heart"
        assert "heart_" in result.data["object_id"]  # auto-derived from asset_id

    def test_execute_with_fill(self):
        """Fill color should appear in the JS code."""
        tool = ImportVectorAssetTool()
        with patch.object(tool, '_cache') as mock_cache:
            mock_cache.make_cache_key.return_value = "fill_test"
            mock_cache.has_sanitized.return_value = True
            mock_cache.read_sanitized.return_value = b"<svg>...</svg>"

            result = tool.execute(
                asset_id="iconify:mdi:star",
                object_id="star_1",
                semantic_type="star",
                fill="#FF0000",
            )

        assert not result.is_error
        assert "#FF0000" in result.code

    def test_registers_via_registry(self):
        registry = ToolRegistry()
        registry.register(ImportVectorAssetTool())
        tool = registry.get("import_vector_asset")
        assert tool is not None
        assert tool.definition().name == "import_vector_asset"


# ── ReplaceVectorAssetTool tests ────────────────────────────────────────────

class TestReplaceVectorAssetTool:
    def test_definition(self):
        tool = ReplaceVectorAssetTool()
        defn = tool.definition()
        assert defn.name == "replace_vector_asset"
        assert any(p.name == "object_id" for p in defn.parameters)
        assert any(p.name == "asset_id" for p in defn.parameters)

    def test_execute_missing_params(self):
        tool = ReplaceVectorAssetTool()
        r1 = tool.execute()
        assert r1.is_error
        r2 = tool.execute(object_id="x")
        assert r2.is_error
        r3 = tool.execute(asset_id="x")
        assert r3.is_error

    def test_execute_generates_replace_js(self):
        """With a mocked import, generates JS that finds and replaces an object."""
        tool = ReplaceVectorAssetTool()

        # Mock the internal import tool to generate a successful result
        with patch.object(tool, '_import_tool') as mock_import:
            mock_import.execute.return_value = type('Result', (), {
                'is_error': False,
                'data': {
                    'cache_key': 'replace_test_key',
                    'asset_url': '/assets/content/replace_test_key.svg',
                },
                'code': '',
                'description': '',
            })()

            result = tool.execute(
                object_id="smiley_1",
                asset_id="iconify:mdi:emoticon-happy",
            )

        assert not result.is_error
        assert "smiley_1" in result.code
        assert "replace_test_key.svg" in result.code
        assert "canvas.remove" in result.code
        assert result.data["object_id"] == "smiley_1"

    def test_execute_preserves_transform(self):
        """JS code should copy position, scale, angle from the old object."""
        tool = ReplaceVectorAssetTool()
        with patch.object(tool, '_import_tool') as mock_import:
            mock_import.execute.return_value = type('Result', (), {
                'is_error': False,
                'data': {
                    'cache_key': 'preserve_test',
                    'asset_url': '/assets/content/preserve_test.svg',
                },
                'code': '',
                'description': '',
            })()

            result = tool.execute(
                object_id="my_icon",
                asset_id="iconify:mdi:star",
            )

        assert "oldTransform" in result.code
        assert "oldTransform.left" in result.code
        assert "oldTransform.top" in result.code
        assert "oldTransform.scaleX" in result.code
        assert "oldTransform.angle" in result.code

    def test_execute_with_fill(self):
        tool = ReplaceVectorAssetTool()
        with patch.object(tool, '_import_tool') as mock_import:
            mock_import.execute.return_value = type('Result', (), {
                'is_error': False,
                'data': {
                    'cache_key': 'fill_replace',
                    'asset_url': '/assets/content/fill_replace.svg',
                },
                'code': '',
                'description': '',
            })()

            result = tool.execute(
                object_id="star_1",
                asset_id="iconify:mdi:star",
                fill="#00FF00",
            )

        assert "#00FF00" in result.code
        assert "fill" in result.code.lower()

    def test_registers_via_registry(self):
        registry = ToolRegistry()
        registry.register(ReplaceVectorAssetTool())
        tool = registry.get("replace_vector_asset")
        assert tool is not None
        assert tool.definition().name == "replace_vector_asset"


# ── ListAssetCandidatesTool tests ───────────────────────────────────────────

class TestListAssetCandidatesTool:
    def test_definition(self):
        tool = ListAssetCandidatesTool()
        defn = tool.definition()
        assert defn.name == "list_asset_candidates"

    def test_execute_no_cache(self):
        """With empty cache, should return 'no recent searches'."""
        _SEARCH_CACHE.clear()
        result = ListAssetCandidatesTool().execute()
        assert not result.is_error
        assert "no recent searches" in (result.description or "").lower()

    def test_execute_with_cache(self):
        """With populated cache, should return candidates."""
        _SEARCH_CACHE.clear()
        _SEARCH_CACHE["search_list_test"] = {
            "query": "cat",
            "style": "",
            "candidates": [
                {
                    "asset_id": "iconify:mdi:cat",
                    "title": "Cat Icon",
                    "provider": "iconify",
                    "score": 0.85,
                    "preview_url": "",
                    "license": "",
                    "format": "svg",
                    "width": 128,
                    "height": 128,
                },
            ],
        }

        # Set latest search ID
        import tools.assets.search_vector_asset as sv_module
        sv_module._LATEST_SEARCH_ID = "search_list_test"

        result = ListAssetCandidatesTool().execute()
        assert not result.is_error
        assert "Cat Icon" in (result.description or "")
        assert len(result.data["candidates"]) == 1

    def test_execute_specific_search_id(self):
        """Specific search_id should return that entry."""
        _SEARCH_CACHE.clear()
        _SEARCH_CACHE["search_a"] = {
            "query": "apple",
            "candidates": [{"asset_id": "test:apple", "title": "Apple", "score": 0.9, "provider": "test"}],
        }
        _SEARCH_CACHE["search_b"] = {
            "query": "banana",
            "candidates": [{"asset_id": "test:banana", "title": "Banana", "score": 0.8, "provider": "test"}],
        }

        result = ListAssetCandidatesTool().execute(search_id="search_b")
        assert not result.is_error
        assert "banana" in (result.description or "").lower()
        assert result.data["search_id"] == "search_b"

    def test_execute_unknown_search_id(self):
        _SEARCH_CACHE.clear()
        _SEARCH_CACHE["known"] = {"query": "x", "candidates": []}
        result = ListAssetCandidatesTool().execute(search_id="unknown")
        assert result.is_error

    def test_registers_via_registry(self):
        registry = ToolRegistry()
        registry.register(ListAssetCandidatesTool())
        tool = registry.get("list_asset_candidates")
        assert tool is not None


# ── Full registry integration ───────────────────────────────────────────────

class TestAssetToolRegistryIntegration:
    def test_all_asset_tools_register(self):
        registry = ToolRegistry()
        registry.register(SearchVectorAssetTool())
        registry.register(ImportVectorAssetTool())
        registry.register(ReplaceVectorAssetTool())
        registry.register(ListAssetCandidatesTool())

        definitions = registry.get_tool_definitions()
        names = [d["function"]["name"] for d in definitions]
        assert "search_vector_asset" in names
        assert "import_vector_asset" in names
        assert "replace_vector_asset" in names
        assert "list_asset_candidates" in names

    def test_all_tools_in_global_list(self):
        from tools import ALL_TOOLS
        tool_names = [cls.__name__ for cls in ALL_TOOLS]
        assert "SearchVectorAssetTool" in tool_names
        assert "ImportVectorAssetTool" in tool_names
        assert "ReplaceVectorAssetTool" in tool_names
        assert "ListAssetCandidatesTool" in tool_names

    def test_tool_count_increased(self):
        from tools import ALL_TOOLS
        assert len(ALL_TOOLS) == 32  # prior tools + concentric circles + 4 voice layout edit tools
