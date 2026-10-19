import asyncio
import hashlib
import json

import pytest

from studio.research import DrawingResearch, canonical_url, public_url
from studio.store import StudioStore


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/",
    "http://[::1]/",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
])
def test_reference_reader_rejects_private_network_destinations(url):
    with pytest.raises(ValueError, match="public Internet addresses"):
        asyncio.run(public_url(url))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/article",
    "https://user:password@example.com/article",
])
def test_reference_reader_requires_public_http_sources_without_credentials(url):
    with pytest.raises(ValueError, match="public HTTP or HTTPS"):
        canonical_url(url)


def test_research_sources_and_search_cache_exclude_later_tasks_and_other_canvases(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'research-scope.sqlite3')
        research = DrawingResearch(store)
        try:
            session = store.create_session('连续参考资料')
            other = store.create_session('其他画布')
            earlier, current, later = [store.create_task(session['id']) for _ in range(3)]
            foreign = store.create_task(other['id'])
            reference_id = 'reference_' + 'a' * 32
            source = {'reference_id': reference_id, 'title': 'Hand structure',
                'url': 'https://example.org/hand-study',
                'content': 'Draw the wrist as the connection between the forearm and the palm. Keep the fingers attached to the palm.'}
            store.log_tool(earlier['id'], 'first-reader', 0, 'research_source', source)
            store.log_tool(later['id'], 'later-reader', 0, 'research_source', {**source, 'title': 'Later revision'})
            store.log_tool(foreign['id'], 'foreign-reader', 0, 'research_source', {**source, 'title': 'Other canvas'})
            output = await research.call(current['id'], 'main', 'read_reference', {
                'reference_id': reference_id, 'focus_terms': ['wrist']})
            assert output['title'] == 'Hand structure' and output['excerpts']
            later_id = 'reference_' + 'b' * 32
            store.log_tool(later['id'], 'later-reader', 0, 'research_source', {**source, 'reference_id': later_id})
            with pytest.raises(ValueError, match='not found'):
                await research.call(current['id'], 'main', 'read_reference', {
                    'reference_id': later_id, 'focus_terms': ['wrist']})
            query = {'query': 'hand drawing', 'subject_terms': ['hand'], 'goal': '手腕连接'}
            cache_key = hashlib.sha256(json.dumps(['hand drawing', ['hand'], 1]).encode()).hexdigest()
            original = {'ok': True, 'sources': [source]}
            store.log_tool(earlier['id'], 'first-reader', 0, 'research_result', {'cache_key': cache_key, 'output': original})
            store.log_tool(later['id'], 'later-reader', 0, 'research_result', {
                'cache_key': cache_key, 'output': {'ok': True, 'sources': [{'title': 'Later task source'}]}})
            store.log_tool(foreign['id'], 'foreign-reader', 0, 'research_result', {
                'cache_key': cache_key, 'output': {'ok': True, 'sources': [{'title': 'Other canvas source'}]}})
            cached = await research.search(current['id'], 'main', query)
            assert cached['cached'] and cached['sources'] == [source]
        finally:
            store.close()
    asyncio.run(check())
