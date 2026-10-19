import asyncio

import pytest

from studio.research import canonical_url, public_url


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
