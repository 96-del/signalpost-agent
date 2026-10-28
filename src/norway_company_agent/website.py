from __future__ import annotations

import json
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import date, datetime
from queue import Queue, Empty
from threading import Thread, local
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
import extruct
import tldextract
import trafilatura

from .evidence import evidence

USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
DOMAIN_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())
SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}
PRIORITY_TERMS = (
    "om-oss", "om_oss", "about", "kontakt", "contact", "ledelse", "management",
    "team", "people", "locations", "lokasjoner", "avdelinger", "butikker",
    "news", "press", "aktuelt", "nyheter",
)
_REQUEST_LOCAL = local()


def _redirect_count() -> int:
    return int(getattr(_REQUEST_LOCAL, "redirects", 0))


def assert_public_url(url: str, *, dns_timeout: float = 5.0) -> None:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Malformed public HTTP(S) URL") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are blocked")
    if port == 0:
        raise ValueError("Port zero is not a public HTTP(S) service")
    if any(ord(character) < 0x20 or character.isspace() for character in url):
        raise ValueError("URLs containing whitespace or control characters are blocked")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local hosts are blocked")
    result: Queue[tuple[set[str] | None, Exception | None]] = Queue(maxsize=1)

    def resolve() -> None:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
            result.put((addresses, None))
        except Exception as exc:
            result.put((None, exc))

    Thread(target=resolve, daemon=True, name="signalpost-dns-check").start()
    try:
        addresses, error = result.get(timeout=dns_timeout)
    except Empty as exc:
        raise ValueError("Hostname lookup timed out") from exc
    if error is not None:
        if isinstance(error, socket.gaierror):
            raise ValueError("Hostname did not resolve") from error
        raise ValueError(f"Hostname lookup failed: {type(error).__name__}") from error
    addresses = addresses or set()
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, multicast, and reserved addresses are blocked")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        assert_public_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            _REQUEST_LOCAL.redirects = _redirect_count() + 1
        return redirected


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def normalize_homepage(value: str | None) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def _registered_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = DOMAIN_EXTRACTOR(parsed.hostname or "")
    return ext.top_domain_under_public_suffix


def identity_text_excerpt(soup: BeautifulSoup, *, limit: int = 3000) -> str:
    """Capture visible legal/contact identity text without treating it as body copy."""
    nodes = soup.select(
        'footer, address, [itemprop="legalName"], [itemprop="address"], '
        '[itemprop="telephone"], [itemprop="email"]'
    )
    text = " ".join(node.get_text(" ", strip=True) for node in nodes)
    return " ".join(text.split())[:limit]


def _robots_allowed(url: str, timeout: float) -> bool:
    assert_public_url(url)
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            parser.parse(response.read(256 * 1024).decode("utf-8", errors="replace").splitlines())
    except urllib.error.HTTPError as exc:
        # A missing robots file means there is no robots policy to apply. Other
        # HTTP errors (including 403, 429 and 5xx) are not evidence of permission.
        if exc.code in {404, 410}:
            return True
        raise
    return parser.can_fetch(USER_AGENT, url)


def _social_links(base_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    candidates = [str(node.get("href") or "") for node in soup.select("a[href]")]
    candidates.extend(str(node.get("data-href") or "") for node in soup.select("[data-href]"))
    candidates.extend(str(node.get("src") or "") for node in soup.select("iframe[src]"))
    for candidate in candidates:
        url = urllib.parse.urljoin(base_url, candidate)
        parsed_candidate = urllib.parse.urlparse(url)
        if (parsed_candidate.hostname or "").casefold().removeprefix("www.") == "facebook.com" and parsed_candidate.path.startswith("/plugins/"):
            embedded = urllib.parse.parse_qs(parsed_candidate.query).get("href", [])
            if embedded:
                url = embedded[0]
        normalized = normalize_social_url(url)
        if not normalized:
            continue
        found[(normalized["platform"], normalized["url"])] = normalized
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def structured_social_links(value: Any) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            same_as = node.get("sameAs")
            urls = same_as if isinstance(same_as, list) else [same_as]
            for raw in urls:
                if not isinstance(raw, str):
                    continue
                normalized = normalize_social_url(raw.strip())
                if normalized:
                    found[(normalized["platform"], normalized["url"])] = normalized
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def normalize_social_url(url: str) -> dict[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    platform = next((label for domain, label in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if not platform:
        return None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    lowered = [part.casefold() for part in parts]
    rejected_first = {
        "facebook": {"sharer", "sharer.php", "share.php", "dialog", "policy.php", "privacy", "events", "groups", "plugins"},
        "instagram": {"p", "reel", "reels", "stories", "explore"},
        "x": {"intent", "share", "home", "search", "i"},
    }
    if not parts or lowered[0] in rejected_first.get(platform, set()):
        return None
    if platform == "facebook" and lowered[0] == "profile.php":
        return None
    if platform == "linkedin" and (lowered[0] != "company" or len(parts) < 2):
        return None
    if platform == "youtube" and lowered[0] not in {"channel", "user", "c"} and not parts[0].startswith("@"):
        return None
    if host == "youtu.be":
        return None
    if platform == "tiktok" and not parts[0].startswith("@"):
        return None
    if platform == "x" and len(parts) != 1:
        return None
    canonical_host = {
        "linkedin": "linkedin.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "x": "x.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
    }[platform]
    if platform == "linkedin":
        parts = parts[:2]
    elif platform == "youtube":
        parts = parts[:1] if parts[0].startswith("@") else parts[:2]
    return {"platform": platform, "url": f"https://{canonical_host}/{'/'.join(parts)}"}


def _priority_links(base_url: str, soup: BeautifulSoup, limit: int = 4) -> list[str]:
    base = urllib.parse.urlparse(base_url)
    candidates: dict[str, int] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != base.netloc.lower():
            continue
        haystack = (parsed.path + " " + anchor.get_text(" ", strip=True)).casefold()
        rank = next((index for index, term in enumerate(PRIORITY_TERMS) if term in haystack), None)
        if rank is None:
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        candidates[clean] = min(rank, candidates.get(clean, rank))
    return [url for url, _ in sorted(candidates.items(), key=lambda item: (item[1], item[0]))[:limit]]


def _fetch_secondary_page(url: str, *, homepage_domain: str, timeout: float, max_bytes: int) -> tuple[dict[str, Any] | None, list[dict[str, str]], int, int, int, str | None]:
    redirects_before = _redirect_count()
    try:
        if not _robots_allowed(url, timeout):
            return None, [], 1 + _redirect_count() - redirects_before, 0, 0, "robots.txt disallows page"
    except Exception as exc:
        # A failed policy check for one secondary page must not discard an
        # already-fetched homepage or other independently checked pages.
        return None, [], 1 + _redirect_count() - redirects_before, 0, 0, f"robots check failed: {type(exc).__name__}: {str(exc)[:120]}"
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            final_url = response.geturl()
            if len(raw) > max_bytes or "html" not in response.headers.get("content-type", "").lower():
                return None, [], 2 + _redirect_count() - redirects_before, len(raw), elapsed, "unsupported or oversized page"
            if _registered_domain(final_url) != homepage_domain:
                return None, [], 2 + _redirect_count() - redirects_before, len(raw), elapsed, "redirected outside registered domain"
        page_html = raw.decode("utf-8", errors="replace")
        page_soup = BeautifulSoup(page_html, "lxml")
        page_text = trafilatura.extract(page_html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        page = {
            "url": final_url,
            "title": page_soup.title.get_text(" ", strip=True)[:500] if page_soup.title else "",
            "main_text_excerpt": page_text[:5000],
            "identity_text_excerpt": identity_text_excerpt(page_soup),
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            **extract_published_metadata(page_soup),
        }
        return page, _social_links(final_url, page_soup), 2 + _redirect_count() - redirects_before, len(raw), elapsed, None
    except Exception as exc:
        return None, [], 2 + _redirect_count() - redirects_before, 0, int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {str(exc)[:120]}"


def _jsonld_organisations(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            kinds = set(kind if isinstance(kind, list) else [kind])
            if kinds & {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant"}:
                values.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(metadata.get("json-ld", []))
    return values[:20]


ARTICLE_TYPES = {"article", "newsarticle", "blogposting", "report"}


def extract_published_metadata(soup: BeautifulSoup) -> dict[str, str | None]:
    """Return explicit article metadata; never mistake a modified/updated date for publication."""
    article_type: str | None = None
    headline: str | None = None
    published_at: str | None = None

    def normalized_date(value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed_date = date.fromisoformat(raw[:10])
            except ValueError:
                return None
            return parsed_date.isoformat()
        return parsed.date().isoformat()

    for selector in (
        'meta[property="article:published_time"]',
        'meta[name="datePublished"]',
        'meta[itemprop="datePublished"]',
        'time[itemprop="datePublished"][datetime]',
    ):
        node = soup.select_one(selector)
        if node:
            published_at = normalized_date(node.get("content") or node.get("datetime"))
            if published_at:
                break

    def walk(node: Any) -> None:
        nonlocal article_type, headline, published_at
        if isinstance(node, dict):
            raw_type = node.get("@type")
            kinds = raw_type if isinstance(raw_type, list) else [raw_type]
            matching_type = next((str(kind).rsplit("/", 1)[-1].casefold() for kind in kinds if str(kind).rsplit("/", 1)[-1].casefold() in ARTICLE_TYPES), None)
            if matching_type:
                article_type = article_type or matching_type
                headline = headline or str(node.get("headline") or node.get("name") or "").strip() or None
                published_at = published_at or normalized_date(node.get("datePublished"))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

    og_type = soup.select_one('meta[property="og:type"]')
    if not article_type and og_type and str(og_type.get("content") or "").casefold() == "article":
        article_type = "article"
    og_title = soup.select_one('meta[property="og:title"]')
    headline = headline or (str(og_title.get("content") or "").strip() if og_title else None)
    return {"published_at": published_at, "article_type": article_type, "article_headline": headline}


def _extraction_state(text: str, soup: BeautifulSoup) -> str:
    return "js_fallback_candidate" if len(text.strip()) < 100 and len(soup.select("script[src]")) >= 2 else "static_complete"


def fetch_website(url: str | None, *, timeout: float = 15.0, max_bytes: int = 2_000_000) -> tuple[dict[str, Any], dict[str, Any]]:
    redirects_before = _redirect_count()
    supplied_url = str(url or "").strip()
    supplied_scheme = bool(re.match(r"^https?://", supplied_url, re.I))
    try:
        normalized = normalize_homepage(url)
    except (ValueError, UnicodeError) as exc:
        return evidence("website", "source_error", "registry_linked_company_website", supplied_url or "https://data.brreg.no/enhetsregisteret/api/enheter", note=f"Invalid registry website URL: {str(exc)[:160]}"), {"requests": 0, "bytes": 0, "latencies_ms": []}
    if not normalized:
        if supplied_url:
            return evidence(
                "website", "source_error", "registry_linked_company_website",
                "https://data.brreg.no/enhetsregisteret/api/enheter",
                note="Registry website value is malformed or uses an unsupported scheme; no website request was made",
            ), {"requests": 0, "bytes": 0, "latencies_ms": []}
        return evidence("website", "not_found", "registry_linked_company_website", "https://data.brreg.no/enhetsregisteret/api/enheter", note="No registry website URL was supplied"), {"requests": 0, "bytes": 0, "latencies_ms": []}
    try:
        assert_public_url(normalized)
    except ValueError as exc:
        status = "source_error" if "did not resolve" in str(exc).casefold() or "timed out" in str(exc).casefold() or "lookup failed" in str(exc).casefold() else "blocked"
        return evidence("website", status, "registry_linked_company_website", normalized, note=str(exc)), {"requests": 0, "bytes": 0, "latencies_ms": []}
    try:
        if not _robots_allowed(normalized, timeout):
            return evidence("website", "blocked", "registry_linked_company_website", normalized, note="robots.txt disallows this user agent"), {"requests": 1 + _redirect_count() - redirects_before, "bytes": 0, "latencies_ms": []}
    except urllib.error.HTTPError as exc:
        status = "blocked" if exc.code in {401, 403, 429} else "source_error"
        return evidence("website", status, "registry_linked_company_website", normalized, note=f"robots.txt HTTP {exc.code}; homepage not fetched"), {"requests": 1 + _redirect_count() - redirects_before, "bytes": 0, "latencies_ms": []}
    except Exception as exc:
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"robots check failed; homepage not fetched: {type(exc).__name__}: {str(exc)[:140]}"), {"requests": 1 + _redirect_count() - redirects_before, "bytes": 0, "latencies_ms": []}
    started = time.monotonic()
    request = urllib.request.Request(normalized, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            if len(raw) > max_bytes:
                return evidence("website", "blocked", "registry_linked_company_website", normalized, note="Homepage exceeds byte limit"), {"requests": 2 + _redirect_count() - redirects_before, "bytes": len(raw), "latencies_ms": [elapsed]}
            if "html" not in content_type.lower():
                return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"Unsupported content type: {content_type}"), {"requests": 2 + _redirect_count() - redirects_before, "bytes": len(raw), "latencies_ms": [elapsed]}
            final_url = response.geturl()
            assert_public_url(final_url)
        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        structured = extruct.extract(html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"])
        text = trafilatura.extract(html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
        description = str(description_tag.get("content") or "").strip() if description_tag else ""
        identity_text = identity_text_excerpt(soup)
        value = {
            "requested_url": normalized,
            "final_url": final_url,
            "registered_domain": _registered_domain(final_url),
            "title": title[:500],
            "description": description[:2000],
            "main_text_excerpt": text[:5000],
            "identity_text_excerpt": identity_text,
            "social_links": _social_links(final_url, soup),
            "structured_organisations": _jsonld_organisations(structured),
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "extraction_state": _extraction_state(text, soup),
        }
        pages = [{"url": final_url, "title": title[:500], "main_text_excerpt": text[:5000], "identity_text_excerpt": identity_text, "content_sha256": value["content_sha256"]}]
        social = value["social_links"]
        crawl_errors = []
        requests = 2 + _redirect_count() - redirects_before
        bytes_received = len(raw)
        page_latencies = [elapsed]
        homepage_domain = value["registered_domain"]
        for page_url in _priority_links(final_url, soup):
            page, page_social, page_requests, page_bytes, page_elapsed, page_error = _fetch_secondary_page(
                page_url,
                homepage_domain=homepage_domain,
                timeout=timeout,
                max_bytes=min(max_bytes, 1_000_000),
            )
            requests += page_requests
            bytes_received += page_bytes
            if page_elapsed:
                page_latencies.append(page_elapsed)
            if page:
                pages.append(page)
                social.extend(page_social)
            elif page_error:
                crawl_errors.append({"url": page_url, "error": page_error})
        value["pages"] = pages
        value["social_links"] = list({(item["platform"], item["url"]): item for item in social}.values())
        value["crawl_errors"] = crawl_errors
        return evidence("website", "available", "registry_linked_company_website", final_url, value=value, note="Company-controlled claim layer; not an official registry fact", content_sha256=value["content_sha256"]), {"requests": requests, "bytes": bytes_received, "latencies_ms": page_latencies}
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        status = "not_found" if exc.code in {404, 410} else "source_error"
        return evidence("website", status, "registry_linked_company_website", normalized, note=f"HTTP {exc.code}"), {"requests": 2 + _redirect_count() - redirects_before, "bytes": 0, "latencies_ms": [elapsed]}
    except urllib.error.URLError as exc:
        if not supplied_scheme and normalized.startswith("https://"):
            first_elapsed = int((time.monotonic() - started) * 1000)
            outer_requests = 2 + _redirect_count() - redirects_before
            record, metrics = fetch_website("http://" + supplied_url, timeout=timeout, max_bytes=max_bytes)
            metrics["requests"] += outer_requests
            metrics["latencies_ms"].insert(0, first_elapsed)
            return record, metrics
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"URLError: {str(exc.reason)[:180]}"), {"requests": 2 + _redirect_count() - redirects_before, "bytes": 0, "latencies_ms": [elapsed]}
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"{type(exc).__name__}: {str(exc)[:180]}"), {"requests": 2 + _redirect_count() - redirects_before, "bytes": 0, "latencies_ms": [elapsed]}
