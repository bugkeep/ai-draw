import asyncio
import hashlib
import ipaddress
import json
import os
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import trafilatura
from ddgs import DDGS
from ddgs.exceptions import DDGSException

from .agent_store import stable_id
from .store import StudioStore


RESEARCH_TOOLS = frozenset({"search_references", "read_reference"})
TECHNIQUE_TERMS = ("draw", "sketch", "outline", "perspective", "proportion", "shade", "shading",
                   "contour", "composition", "step", "画法", "绘画", "构图", "比例", "透视", "轮廓", "步骤", "明暗")
COLLECTION_HOSTS = ("pinterest.", "shutterstock.", "istockphoto.", "freepik.", "alamy.", "dreamstime.",
                    "youtube.com", "youtu.be", "tiktok.com")
PROCEDURE = re.compile(
    r"(?:\bstep\s*\d+\b|(?:^|[.!?]\s+)(?:(?:first|next|then|now|finally)[,: ]+)?"
    r"(?:draw|sketch|add|mark|outline|shade|place|begin|start|connect|keep|use|make)\b|"
    r"\b(?:i|we)\s+(?:draw|sketch|shade|use)\b|第[一二三四五六七八九十\d]+步|画出|勾勒|绘制|起稿|皴擦|然后|接着)",
    re.IGNORECASE,
)


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Research requires a public HTTP or HTTPS source")
    query = [(key, item) for key, item in parse_qsl(parts.query) if not key.lower().startswith("utm_")
             and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), ""))


async def public_url(value: str) -> str:
    url = canonical_url(value)
    parts = urlsplit(url)
    addresses = await asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise ValueError("Reference URLs must resolve to public Internet addresses")
    return url


def passages(content: str, terms: list[str], *, require_match: bool = False) -> list[str]:
    paragraphs = [part.strip() for part in content.splitlines() if part.strip()]
    parts = [part + ("\n" + paragraphs[index + 1] if len(part) < 180 and index + 1 < len(paragraphs) else "")
             for index, part in enumerate(paragraphs) if len(part) >= 15]
    def score(part):
        text = part.casefold()
        return (sum(2 for term in terms if term.casefold() in text)
                + sum(term in text for term in TECHNIQUE_TERMS) + len(PROCEDURE.findall(part)) * 6)
    ranked = sorted(((index, part) for index, part in enumerate(parts)
                     if not require_match or any(term.casefold() in part.casefold() for term in terms)),
                    key=lambda item: (-score(item[1]), item[0]))
    selected = []
    for index, part in ranked:
        if score(part) > 0 and not any(abs(index - prior) <= 1 for prior, _ in selected):
            selected.append((index, part[:1100]))
        if len(selected) == 3:
            break
    return [part for _, part in sorted(selected)]


class DrawingResearch:
    def __init__(self, store: StudioStore):
        self.store = store

    def _cached_source(self, task_id: str, reference_id: str) -> dict:
        session_id = self.store.get_task(task_id)['session_id']
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT l.payload FROM tool_logs l JOIN tasks original ON original.id=l.task_id "
                "JOIN tasks current ON current.id=? WHERE original.session_id=? AND original.rowid<=current.rowid "
                "AND l.kind='research_source' "
                "AND json_extract(l.payload,'$.reference_id')=? ORDER BY l.id DESC LIMIT 1",
                (task_id, session_id, reference_id),
            ).fetchone()
        if row is None:
            raise ValueError("Reference not found in this canvas")
        return json.loads(row["payload"])

    async def _candidate(self, client: httpx.AsyncClient, candidate: dict, terms: list[str]) -> dict:
        try:
            return await self._read(client, candidate, terms)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return {"url": candidate["url"], "error": str(exc)[:300]}

    async def _read(self, client: httpx.AsyncClient, candidate: dict, terms: list[str]) -> dict:
        url = candidate["url"]
        for _ in range(20):
            url = await public_url(url)
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    if response.next_request is None:
                        raise ValueError("Reference redirect has no destination")
                    url = str(response.next_request.url)
                    continue
                response.raise_for_status()
                mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if mime not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise ValueError("Reference page has no readable article text")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise ValueError("Reference page exceeds the article reading budget")
            if mime == "text/plain":
                text = bytes(data).decode("utf-8", errors="replace")
            else:
                text = trafilatura.extract(bytes(data), favor_precision=True, include_comments=False,
                                           include_tables=False, no_fallback=True) or ""
            text = text.strip()
            if len(text) < 300:
                raise ValueError("Reference page contains insufficient article content")
            lower = text.casefold()
            matched = [term for term in terms if term.casefold() in lower]
            techniques = [term for term in TECHNIQUE_TERMS if term in lower]
            excerpts = passages(text, terms)
            if not matched or len(techniques) < 2 or not excerpts or not any(PROCEDURE.search(part) for part in excerpts):
                raise ValueError("Reference page has no useful drawing instructions for the requested subject")
            return {**candidate, "url": url, "content": text, "excerpts": excerpts,
                    "matched_terms": matched, "technique_terms": techniques,
                    "score": len(matched) * 4 + len(techniques),
                    "fingerprint": hashlib.sha256(text.encode()).hexdigest(), "verified_at": time.time()}
        raise ValueError("Reference page has too many redirects")

    async def search(self, task_id: str, agent_id: str, arguments: dict) -> dict:
        started = time.perf_counter()
        session_id = self.store.get_task(task_id)["session_id"]
        terms = list(dict.fromkeys(term.strip() for term in arguments["subject_terms"] if term.strip()))
        if not terms:
            raise ValueError("Provide concrete subject terms for reference filtering")
        cache_key = hashlib.sha256(json.dumps([arguments["query"].strip().casefold(), sorted(terms),
                                               arguments.get("page", 1)]).encode()).hexdigest()
        with self.store.lock:
            cached = self.store.connection.execute(
                "SELECT l.payload FROM tool_logs l JOIN tasks original ON original.id=l.task_id "
                "JOIN tasks current ON current.id=? WHERE original.session_id=? AND original.rowid<=current.rowid "
                "AND l.kind='research_result' AND l.created_at>? "
                "AND json_extract(l.payload,'$.cache_key')=? ORDER BY l.id DESC LIMIT 1",
                (task_id, session_id, time.time() - 3600, cache_key),
            ).fetchone()
        if cached is not None:
            output = json.loads(cached["payload"])["output"]
            return {**output, "goal": arguments["goal"], "cached": True, "latency_ms": 0}
        document_id = self.store.session_document_id(session_id)
        self.store.publish(document_id, "phase", {"task_id": task_id, "agent_id": agent_id, "phase": "research"})
        try:
            backend = os.getenv("DRAW_AGENT_SEARCH_BACKEND", "yahoo,duckduckgo,google,startpage")
            results = await asyncio.to_thread(DDGS(timeout=12).text, arguments["query"], max_results=8,
                                             page=arguments.get("page", 1), backend=backend)
        except DDGSException as exc:
            raise ValueError(f"Drawing reference search failed: {exc}") from None
        self.store.log_tool(task_id, agent_id, 0, "research_search", {"query": arguments["query"], "backend": backend, "results": results})
        candidates, excluded, seen = [], [], set()
        for result in results:
            try:
                url = canonical_url(result["href"])
                host = urlsplit(url).hostname or ""
                if any(domain in host for domain in COLLECTION_HOSTS):
                    raise ValueError("Image collection or stock marketplace has no drawing instruction article")
                if url in seen:
                    continue
                seen.add(url)
                metadata = f"{result.get('title', '')} {result.get('body', '')}".casefold()
                matches = sum(term.casefold() in metadata for term in terms)
                techniques = sum(term in metadata for term in TECHNIQUE_TERMS)
                if not matches and not techniques:
                    raise ValueError("Search result does not match the drawing subject")
                candidates.append({"title": result.get("title", ""), "url": url,
                                   "snippet": result.get("body", ""),
                                   "rank": matches * 4 + techniques})
            except (ValueError, KeyError) as exc:
                excluded.append({"url": result.get("href", ""), "reason": str(exc)})
        candidates.sort(key=lambda item: -item["rank"])
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8), follow_redirects=False,
                                     headers={"User-Agent": "DrawAgentStudio/0.1 drawing-reference-reader"}) as client:
            read = await asyncio.gather(*(self._candidate(client, candidate, terms) for candidate in candidates[:5]))
        sources, fingerprints, domains = [], set(), {}
        for candidate, source in zip(candidates[:5], read):
            if "error" in source:
                excluded.append({"url": candidate["url"], "reason": source["error"]})
                continue
            host = urlsplit(source["url"]).hostname
            if source["fingerprint"] in fingerprints or domains.get(host, 0) >= 2:
                excluded.append({"url": source["url"], "reason": "Duplicate content or repeated source domain"})
                continue
            fingerprints.add(source["fingerprint"])
            domains[host] = domains.get(host, 0) + 1
            source["reference_id"] = stable_id("reference", f"{session_id}/{source['url']}")
            self.store.log_tool(task_id, agent_id, 0, "research_source", source)
            sources.append(source)
        sources.sort(key=lambda source: -source["score"])
        self.store.log_tool(task_id, agent_id, 0, "research_filter", {"query": arguments["query"], "excluded": excluded,
                            "selected": [source["reference_id"] for source in sources[:3]]})
        if not sources:
            return {"ok": False, "error": "No useful drawing articles were verified. Refine the subject and technique query.",
                    "rejected_count": len(excluded), "next_page": arguments.get("page", 1) + 1}
        output = {"ok": True, "goal": arguments["goal"], "query": arguments["query"],
                "sources": [{key: source[key] for key in ("reference_id", "title", "url", "excerpts", "matched_terms")}
                            for source in sources[:3]], "rejected_count": len(excluded),
                "next_page": arguments.get("page", 1) + 1,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1), "content_is_untrusted": True}
        self.store.log_tool(task_id, agent_id, 0, "research_result", {"cache_key": cache_key, "output": output})
        return output

    async def call(self, task_id: str, agent_id: str, name: str, arguments: dict) -> dict:
        if name == "search_references":
            return await self.search(task_id, agent_id, arguments)
        source = self._cached_source(task_id, arguments["reference_id"])
        terms = [term.strip() for term in arguments["focus_terms"] if term.strip()]
        if not terms:
            raise ValueError("Provide concrete focus terms for reference reading")
        excerpts = passages(source["content"], terms, require_match=True)
        if not excerpts:
            return {"ok": False, "error": "The verified article contains no passages matching these focus terms."}
        return {"ok": True, "reference_id": source["reference_id"], "title": source["title"],
                "url": source["url"], "excerpts": excerpts, "content_is_untrusted": True}
