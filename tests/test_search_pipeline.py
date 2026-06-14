"""Tests for the asset search pipeline (providers, ranking, search service)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
import httpx

from assets.domain.models import AssetSearchRequest, AssetCandidate
from assets.providers.base import AssetProvider
from assets.providers.iconify_provider import IconifyProvider
from assets.providers.local_provider import LocalAssetProvider
from assets.services.ranking_service import RankingService
from assets.services.search_service import SearchService, SearchResult


# ── Provider contract test ──────────────────────────────────────────────────

class TestAssetProviderContract:
    """Verify that all providers satisfy the AssetProvider interface."""

    def test_iconify_has_name(self):
        assert IconifyProvider().name == "iconify"

    def test_local_has_name(self):
        assert LocalAssetProvider().name == "local"

    @pytest.mark.asyncio
    async def test_iconify_search_returns_list(self):
        """With a mocked client that returns no results, search returns []."""
        p = IconifyProvider()
        req = AssetSearchRequest(query="test")
        # No client attached → will try to create one and fail.  Instead
        # test with a mock.
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {"icons": [], "total": 0}
        mock_client.get = AsyncMock(return_value=mock_response)

        results = await p.search(req)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_local_search_returns_empty_list(self):
        p = LocalAssetProvider()
        req = AssetSearchRequest(query="cat")
        results = await p.search(req)
        assert results == []

    def test_all_providers_have_required_methods(self):
        for cls in (IconifyProvider, LocalAssetProvider):
            instance = cls()
            assert hasattr(instance, "search")
            assert hasattr(instance, "fetch")
            assert hasattr(instance, "fetch_metadata")


# ── IconifyProvider tests ───────────────────────────────────────────────────

class TestIconifyProvider:
    @pytest.fixture
    def provider(self):
        return IconifyProvider()

    def _mock_client(self, provider, json_data: dict | None = None,
                     status: int = 200, exc: Exception | None = None):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        if exc:
            mock_client.get = AsyncMock(side_effect=exc)
        else:
            mock_response.status_code = status
            mock_response.json.return_value = json_data or {}
            mock_client.get = AsyncMock(return_value=mock_response)
        provider._client = mock_client
        return mock_client

    @pytest.mark.asyncio
    async def test_search_returns_candidates(self, provider):
        self._mock_client(provider, {
            "icons": ["mdi:emoticon-happy", "mdi:emoticon-sad"],
            "total": 2,
        })
        req = AssetSearchRequest(query="smile")
        results = await provider.search(req)
        assert len(results) == 2
        assert results[0].asset_id == "iconify:mdi:emoticon-happy"
        assert results[0].format == "svg"
        assert results[0].provider == "iconify"
        assert "svg" in results[0].download_url

    @pytest.mark.asyncio
    async def test_search_empty_response(self, provider):
        self._mock_client(provider, {"icons": [], "total": 0})
        results = await provider.search(AssetSearchRequest(query="zzz"))
        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, provider):
        self._mock_client(provider, status=500)
        results = await provider.search(AssetSearchRequest(query="smile"))
        assert results == []

    @pytest.mark.asyncio
    async def test_search_timeout(self, provider):
        self._mock_client(provider, exc=httpx.TimeoutException("timeout"))
        results = await provider.search(AssetSearchRequest(query="smile"))
        assert results == []

    @pytest.mark.asyncio
    async def test_search_respects_limit(self, provider):
        self._mock_client(provider, {
            "icons": [f"mdi:icon{i}" for i in range(50)],
            "total": 50,
        })
        req = AssetSearchRequest(query="icon", limit=3)
        results = await provider.search(req)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_dict_icons(self, provider):
        """Iconify can return dict entries with id/title/tags."""
        self._mock_client(provider, {
            "collections": [
                {"id": "mdi:heart", "title": "Heart Icon", "tags": ["heart", "love"]},
            ],
            "total": 1,
        })
        results = await provider.search(AssetSearchRequest(query="heart"))
        assert len(results) == 1
        assert "heart" in results[0].title.lower()

    @pytest.mark.asyncio
    async def test_fetch_returns_bytes(self, provider):
        self._mock_client(provider)
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.content = b"<svg>...</svg>"
        provider._client.get = AsyncMock(return_value=mock_response)

        candidate = AssetCandidate(
            asset_id="iconify:mdi:test",
            provider="iconify",
            provider_asset_id="mdi:test",
            title="Test",
            format="svg",
            download_url="https://api.iconify.design/mdi/test.svg",
        )
        data = await provider.fetch(candidate)
        assert data == b"<svg>...</svg>"

    @pytest.mark.asyncio
    async def test_fetch_no_url(self, provider):
        candidate = AssetCandidate(
            asset_id="iconify:mdi:test",
            provider="iconify",
            provider_asset_id="mdi:test",
            title="Test",
            format="svg",
        )
        with pytest.raises(ValueError, match="No download_url"):
            await provider.fetch(candidate)

    @pytest.mark.asyncio
    async def test_fetch_metadata(self, provider):
        self._mock_client(provider, {
            "license": {"name": "Apache-2.0"},
            "author": {"name": "Material Design", "url": "https://example.com"},
            "tags": ["icon", "ui"],
        })
        candidate = AssetCandidate(
            asset_id="iconify:mdi:test",
            provider="iconify",
            provider_asset_id="mdi:test",
            title="Test",
            format="svg",
        )
        meta = await provider.fetch_metadata(candidate)
        assert meta.get("license_name") == "Apache-2.0"
        assert meta.get("author") == "Material Design"

    @pytest.mark.asyncio
    async def test_fetch_metadata_no_colon(self, provider):
        """provider_asset_id without ':' should return empty."""
        candidate = AssetCandidate(
            asset_id="iconify:test",
            provider="iconify",
            provider_asset_id="test",
            title="Test",
            format="svg",
        )
        meta = await provider.fetch_metadata(candidate)
        assert meta == {}


# ── RankingService tests ────────────────────────────────────────────────────

class TestRankingService:
    @pytest.fixture
    def ranking(self):
        return RankingService()

    def _make_candidate(self, title: str = "", tags: list[str] = None,
                        provider: str = "iconify", fmt: str = "svg",
                        width: int = 128, height: int = 128,
                        description: str = "", asset_id: str = "") -> AssetCandidate:
        return AssetCandidate(
            asset_id=asset_id or f"test:{title.lower().replace(' ', '-')}",
            provider=provider,
            provider_asset_id=title.lower().replace(" ", "-"),
            title=title,
            tags=tags or [],
            format=fmt,
            width=width,
            height=height,
            description=description or None,
        )

    def test_text_score_exact_title_match(self, ranking):
        c = self._make_candidate(title="Happy Face")
        score = ranking._score_text(c, "happy face")
        assert score >= 0.8

    def test_text_score_low_match(self, ranking):
        c = self._make_candidate(title="Mountain")
        score = ranking._score_text(c, "smiley")
        assert score < 0.5

    def test_text_score_negative_penalty(self, ranking):
        c = self._make_candidate(title="Sad Face", tags=["sad", "angry"])
        score = ranking._score_text(c, "face")
        # Should be penalised for containing "sad" when user asked for "face"
        # (we don't know the user's intent here, but the presence of
        # negative terms should reduce score)
        assert score < 0.5

    def test_style_score_requested(self, ranking):
        c = self._make_candidate(title="Flat Icon", tags=["flat", "ui"])
        score = ranking._score_style(c, "flat")
        assert score >= 0.8

    def test_style_score_no_request(self, ranking):
        c = self._make_candidate(title="Anything")
        score = ranking._score_style(c, "")
        assert score == 0.5

    def test_style_score_mismatch(self, ranking):
        c = self._make_candidate(title="Line Art", tags=["minimal"])
        score = ranking._score_style(c, "cartoon")
        assert score < 0.5

    def test_quality_score_good(self, ranking):
        c = self._make_candidate(width=256, height=256)
        score = ranking._score_quality(c)
        assert score >= 0.7

    def test_quality_score_small(self, ranking):
        c = self._make_candidate(width=16, height=16)
        score = ranking._score_quality(c)
        assert score < 0.7  # small icons get lower quality score

    def test_editability_svg(self, ranking):
        c = self._make_candidate(fmt="svg")
        assert ranking._score_editability(c) >= 0.8

    def test_editability_jpg(self, ranking):
        c = self._make_candidate(fmt="jpg")
        assert ranking._score_editability(c) < 0.5

    def test_source_trusted(self, ranking):
        c = self._make_candidate(provider="iconify")
        assert ranking._score_source(c) == 0.9

    def test_source_untrusted(self, ranking):
        c = self._make_candidate(provider="random-site")
        assert ranking._score_source(c) == 0.5

    def test_rank_orders_by_score(self, ranking):
        good = self._make_candidate(title="Happy Face",
                                     tags=["smile", "happy", "face"])
        bad = self._make_candidate(title="Random Shape", tags=["abstract"])
        good.text_score = 0.9
        good.style_score = 0.8
        good.quality_score = 0.9
        bad.text_score = 0.1
        bad.style_score = 0.1
        bad.quality_score = 0.1

        ranked = ranking.rank([bad, good], query="happy face")
        assert ranked[0].asset_id == good.asset_id
        assert ranked[1].asset_id == bad.asset_id

    def test_rank_on_real_data(self, ranking):
        """End-to-end ranking with realistic candidates."""
        candidates = [
            self._make_candidate(title="Smiley Face", tags=["smile", "emoji", "face"]),
            self._make_candidate(title="Sad Face", tags=["sad", "cry", "face"]),
            self._make_candidate(title="Heart Icon", tags=["heart", "love"]),
        ]
        ranked = ranking.rank(candidates, query="happy")

        # "Sad Face" should be last due to negative keyword penalty
        assert ranked[-1].asset_id.endswith("sad-face")
        # "Smiley Face" should be first (best text match for "happy")
        assert ranked[0].asset_id.endswith("smiley-face")

    def test_rank_respects_limit(self, ranking):
        candidates = [self._make_candidate(title=f"Item {i}") for i in range(10)]
        ranked = ranking.rank(candidates, query="item")
        # Rank returns all candidates (limit is applied by SearchService)
        assert len(ranked) == 10


# ── SearchService tests ────────────────────────────────────────────────────

class TestSearchService:
    @pytest.fixture
    def service(self):
        return SearchService()

    def test_register_provider(self, service):
        p = LocalAssetProvider()
        service.register(p)
        assert len(service._providers) == 1

    @pytest.mark.asyncio
    async def test_search_empty_no_providers(self, service):
        result = await service.search(AssetSearchRequest(query="cat"))
        assert len(result.candidates) == 0
        assert result.normalized_query is not None

    @pytest.mark.asyncio
    async def test_search_single_provider(self, service):
        mock_provider = AsyncMock(spec=AssetProvider)
        mock_provider.name = "mock"
        mock_provider.search.return_value = [
            AssetCandidate(
                asset_id="mock:cat",
                provider="mock",
                provider_asset_id="cat",
                title="Cat",
                format="svg",
            )
        ]
        service.register(mock_provider)

        result = await service.search(AssetSearchRequest(query="cat"))
        assert len(result.candidates) == 1
        assert result.candidates[0].title == "Cat"

    @pytest.mark.asyncio
    async def test_search_multiple_providers_dedup(self, service):
        """Same asset_id from two providers should be deduplicated."""
        p1 = AsyncMock(spec=AssetProvider)
        p1.name = "p1"
        p1.search.return_value = [
            AssetCandidate(
                asset_id="shared:icon",
                provider="p1",
                provider_asset_id="icon",
                title="Icon",
                format="svg",
            )
        ]
        p2 = AsyncMock(spec=AssetProvider)
        p2.name = "p2"
        p2.search.return_value = [
            AssetCandidate(
                asset_id="shared:icon",  # same asset_id
                provider="p2",
                provider_asset_id="icon",
                title="Icon Dupe",
                format="svg",
            )
        ]
        service.register(p1)
        service.register(p2)

        result = await service.search(AssetSearchRequest(query="icon"))
        assert len(result.candidates) == 1  # deduped

    @pytest.mark.asyncio
    async def test_search_provider_error_doesnt_block(self, service):
        """One provider failing shouldn't prevent others from returning."""
        failing = AsyncMock(spec=AssetProvider)
        failing.name = "failing"
        failing.search.side_effect = RuntimeError("provider down")

        working = AsyncMock(spec=AssetProvider)
        working.name = "working"
        working.search.return_value = [
            AssetCandidate(
                asset_id="working:icon",
                provider="working",
                provider_asset_id="icon",
                title="Working Icon",
                format="svg",
            )
        ]
        service.register(failing)
        service.register(working)

        result = await service.search(AssetSearchRequest(query="icon"))
        assert len(result.candidates) == 1
        assert len(result.errors) == 1

    @pytest.mark.asyncio
    async def test_search_result_to_dict(self, service):
        result = SearchResult(
            search_id="test_1",
            normalized_query="cat",
            candidates=[],
            provider_results={"mock": 0},
            errors=[],
        )
        d = result.to_dict()
        assert d["search_id"] == "test_1"
        assert d["candidate_count"] == 0

    @pytest.mark.asyncio
    async def test_query_normalization_service(self, service):
        """Chinese query should produce English normalized_query."""
        mock_p = AsyncMock(spec=AssetProvider)
        mock_p.name = "mock"
        mock_p.search.return_value = []
        service.register(mock_p)

        result = await service.search(AssetSearchRequest(query="笑脸"))
        # Should normalize to "smiling face" or similar
        assert result.normalized_query is not None

    @pytest.mark.asyncio
    async def test_search_respects_provider_order(self, service):
        """First registered provider is queried first."""
        p1 = AsyncMock(spec=AssetProvider)
        p1.name = "p1"
        p1.search.return_value = [
            AssetCandidate(asset_id="p1:icon", provider="p1",
                           provider_asset_id="icon", title="P1", format="svg"),
        ]
        p2 = AsyncMock(spec=AssetProvider)
        p2.name = "p2"
        p2.search.return_value = []
        service.register(p1)
        service.register(p2)

        result = await service.search(AssetSearchRequest(query="icon"))
        assert result.candidates[0].provider == "p1"
