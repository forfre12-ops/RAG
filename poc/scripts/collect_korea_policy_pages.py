"""Collect individually licensed text pages from the Korea Policy Briefing site.

The collector is deliberately conservative:

* discovery is the default and fetches only a small pilot (five detail pages),
* ``--download`` is required to persist raw HTML and extracted text,
* every detail page must carry its own supported KOG-L marker and an explicit
  text-free-use sentence, and
* KOG-L type 1 text pages are training-eligible only with attribution.  This
  follows the Korea Culture Information Service's official 2025 Q3 guidance,
  which lists type 1 as usable for AI training subject to source attribution.

Images, figures, captions, attachment text, copyright boilerplate, and author
profiles are excluded from the extracted body.  The output is an immutable,
unique run directory whose manifest and JSONL files are published atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from typing import BinaryIO, Iterable, Mapping, Sequence
import urllib.parse
import urllib.error
import urllib.request


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_SRC = _ROOT / "src"
for _path in (_ROOT, _SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from koipa.proxy_corpus import validate_proxy_record  # noqa: E402


USER_AGENT = "koipa-korea-policy-collector/1.1"
DEFAULT_OUTPUT_ROOT = _ROOT / "datasets" / "proxy_gold" / "korea_policy_runs"
DEFAULT_LIMIT = 5
HARD_LIMIT = 300
DEFAULT_MAX_HTML_BYTES = 3 * 1024 * 1024
MIN_SECTION_CHARS = 1200
MAX_SECTION_CHARS = 3200
ALLOWED_HOSTS = frozenset({"www.korea.kr", "m.korea.kr"})
ALLOWED_HTML_MIMES = frozenset({"text/html", "application/xhtml+xml"})
KOGL_AI_TRAINING_GUIDANCE_URL = (
    "https://www.kogl.or.kr/namoEditor/binary/files/000001/"
    "2025%EB%85%84_3%EB%B6%84%EA%B8%B0_"
    "%EA%B3%B5%EA%B3%B5%EC%A0%80%EC%9E%91%EB%AC%BC_"
    "%EC%9D%B4%EC%8A%88%EB%A6%AC%ED%8F%AC%ED%8A%B8_-_AI_"
    "%EC%8B%9C%EB%8C%80_%EA%B3%B5%EA%B3%B5%EC%A0%80%EC%9E%91%EB%AC%BC%"
    "EC%9D%B4_%EB%82%98%EC%95%84%EA%B0%80%EC%95%BC_%ED%95%A0_"
    "%EB%B0%A9%ED%96%A5_4.pdf"
)
KOGL_1_TRAINING_POLICY = "training_eligible_with_source_attribution"


@dataclass(frozen=True)
class CollectionKind:
    name: str
    list_path: str
    detail_path: str
    mobile_detail_path: str
    document_type: str


COLLECTION_KINDS: dict[str, CollectionKind] = {
    "press_release": CollectionKind(
        name="press_release",
        list_path="/briefing/pressReleaseList.do",
        detail_path="/briefing/pressReleaseView.do",
        mobile_detail_path="/briefing/pressReleaseView.do",
        document_type="government_press_release",
    ),
    "policy_news": CollectionKind(
        name="policy_news",
        list_path="/news/policyNewsList.do",
        detail_path="/news/policyNewsView.do",
        mobile_detail_path="/news/policyNewsView.do",
        document_type="government_policy_article",
    ),
}


class CollectionError(RuntimeError):
    """A safety, source-integrity, or licence check failed closed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def validate_https_url(url: str, allowed_hosts: Iterable[str] = ALLOWED_HOSTS) -> str:
    """Validate a fixed-host HTTPS URL and return its normalized hostname."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {item.lower().rstrip(".") for item in allowed_hosts}
    if parsed.scheme != "https":
        raise CollectionError(f"HTTPS required: {url}")
    if not host or host not in allowed:
        raise CollectionError(f"host not allowlisted: {host or '<missing>'}")
    if parsed.username is not None or parsed.password is not None:
        raise CollectionError("userinfo is forbidden in source URLs")
    if parsed.port not in (None, 443):
        raise CollectionError(f"non-standard HTTPS port is forbidden: {parsed.port}")
    return host


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: Iterable[str]) -> None:
        super().__init__()
        self.allowed_hosts = frozenset(allowed_hosts)

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_https_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class HtmlPayload:
    body: bytes
    final_url: str
    mime: str
    charset: str
    status: int

    @property
    def text(self) -> str:
        try:
            return self.body.decode(self.charset, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise CollectionError(f"HTML decode failed: {self.charset}") from exc


def _header_value(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    return None if value is None else str(value)


def _content_type(headers: object) -> str:
    getter = getattr(headers, "get_content_type", None)
    if getter is not None:
        return str(getter()).lower()
    return (_header_value(headers, "Content-Type") or "").split(";", 1)[0].lower()


def _content_charset(headers: object) -> str:
    getter = getattr(headers, "get_content_charset", None)
    if getter is not None:
        value = getter()
        if value:
            return str(value).lower()
    raw = _header_value(headers, "Content-Type") or ""
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", raw, re.I)
    return match.group(1).lower() if match else "utf-8"


def _content_length(headers: object) -> int | None:
    raw = _header_value(headers, "Content-Length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise CollectionError(f"invalid Content-Length: {raw}") from exc
    if value < 0:
        raise CollectionError(f"invalid Content-Length: {raw}")
    return value


class SafeHttpClient:
    """HTTPS client with an allowlist, redirect revalidation, and byte caps."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.cookies = CookieJar()

    def _open_with_retry(
        self,
        opener: urllib.request.OpenerDirector,
        request: urllib.request.Request,
        *,
        attempts: int = 3,
    ) -> object:
        """Retry transient transport failures, then expose a bounded error.

        Content, redirect, MIME, and licence failures are deliberately handled
        outside this helper and are never retried.  A single remote disconnect
        must become one rejected page rather than aborting a long immutable run.
        """
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return opener.open(request, timeout=self.timeout)  # noqa: S310
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code != 429 and not 500 <= exc.code <= 599:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < attempts:
                time.sleep(0.5 * attempt)
        detail = type(last_error).__name__ if last_error is not None else "unknown"
        raise CollectionError(
            f"HTML transport failed after {attempts} attempts: {detail}"
        ) from last_error

    def request_html(
        self,
        url: str,
        *,
        max_bytes: int,
        expected_paths: Iterable[str],
    ) -> HtmlPayload:
        validate_https_url(url)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            _AllowlistRedirectHandler(ALLOWED_HOSTS),
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with self._open_with_retry(opener, request) as response:
            final_url = str(response.geturl())
            validate_https_url(final_url)
            final_path = urllib.parse.urlsplit(final_url).path
            if final_path not in frozenset(expected_paths):
                raise CollectionError(f"unexpected redirect path: {final_path}")
            status = int(getattr(response, "status", 200))
            if status != 200:
                raise CollectionError(f"unexpected HTTP status: {status}")
            mime = _content_type(response.headers)
            if mime not in ALLOWED_HTML_MIMES:
                raise CollectionError(f"unexpected HTML MIME: {mime or '<missing>'}")
            declared = _content_length(response.headers)
            if declared is not None and declared > max_bytes:
                raise CollectionError(f"HTML exceeds byte cap: {declared}>{max_bytes}")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise CollectionError(
                    f"HTML exceeds byte cap while streaming: >{max_bytes}"
                )
            prefix = body[:1024].lower().lstrip()
            if not (
                prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")
            ):
                raise CollectionError("response is not an HTML document")
            return HtmlPayload(
                body=body,
                final_url=final_url,
                mime=mime,
                charset=_content_charset(response.headers),
                status=status,
            )


def build_list_url(kind: CollectionKind, page_index: int) -> str:
    if page_index < 1:
        raise ValueError("page_index must be positive")
    query = urllib.parse.urlencode({"pageIndex": page_index})
    return f"https://www.korea.kr{kind.list_path}?{query}"


def build_detail_url(kind: CollectionKind, news_id: str, *, mobile: bool) -> str:
    if not re.fullmatch(r"\d{9}", news_id):
        raise CollectionError(f"invalid newsId: {news_id}")
    host = "m.korea.kr" if mobile else "www.korea.kr"
    path = kind.mobile_detail_path if mobile else kind.detail_path
    return f"https://{host}{path}?{urllib.parse.urlencode({'newsId': news_id})}"


_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _ListingParser(HTMLParser):
    """Extract only detail links nested under the main ``list_type`` list."""

    def __init__(self, kind: CollectionKind) -> None:
        super().__init__(convert_charrefs=True)
        self.kind = kind
        self.stack: list[tuple[str, frozenset[str]]] = []
        self.list_depth: int | None = None
        self.anchor: dict | None = None
        self.anchor_depth: int | None = None
        self.rows: list[dict] = []
        self.last_page = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = frozenset(values.get("class", "").split())
        self.stack.append((tag, classes))
        depth = len(self.stack)
        if "list_type" in classes and self.list_depth is None:
            self.list_depth = depth
        if tag == "a":
            onclick = values.get("onclick", "")
            page_match = re.search(r"pageLink\((\d+)\)", onclick)
            if page_match:
                self.last_page = max(self.last_page, int(page_match.group(1)))
        if (
            tag == "a"
            and self.list_depth is not None
            and depth > self.list_depth
            and self.anchor is None
        ):
            href = html.unescape(values.get("href", ""))
            parsed = urllib.parse.urlsplit(
                urllib.parse.urljoin("https://www.korea.kr", href)
            )
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=False)
            news_id = (query.get("newsId") or [""])[0]
            if (
                parsed.hostname == "www.korea.kr"
                and parsed.path == self.kind.detail_path
                and re.fullmatch(r"\d{9}", news_id)
            ):
                self.anchor = {"news_id": news_id, "title_parts": [], "lead_parts": []}
                self.anchor_depth = depth

    def handle_data(self, data: str) -> None:
        if self.anchor is None:
            return
        classes = set().union(*(classes for _, classes in self.stack))
        if any(tag == "strong" for tag, _ in self.stack):
            self.anchor["title_parts"].append(data)
        elif "lead" in classes:
            self.anchor["lead_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        depth = len(self.stack)
        if tag == "a" and self.anchor is not None and self.anchor_depth == depth:
            title = _normalize_inline(" ".join(self.anchor.pop("title_parts")))
            lead = _normalize_inline(" ".join(self.anchor.pop("lead_parts")))
            self.anchor.update(
                {
                    "title": title,
                    "listing_lead_chars": len(lead),
                    "source_reference": build_detail_url(
                        self.kind, self.anchor["news_id"], mobile=False
                    ),
                    "fetch_url": build_detail_url(
                        self.kind, self.anchor["news_id"], mobile=True
                    ),
                    "collection_kind": self.kind.name,
                }
            )
            self.rows.append(self.anchor)
            self.anchor = None
            self.anchor_depth = None
        self._pop_to(tag)

    def _pop_to(self, tag: str) -> None:
        while self.stack:
            popped_tag, _ = self.stack.pop()
            if self.list_depth is not None and len(self.stack) < self.list_depth:
                self.list_depth = None
            if popped_tag == tag:
                break


def parse_listing(html_text: str, kind: CollectionKind) -> tuple[list[dict], int]:
    parser = _ListingParser(kind)
    parser.feed(html_text)
    deduplicated: list[dict] = []
    seen: set[str] = set()
    for row in parser.rows:
        if row["news_id"] not in seen:
            seen.add(row["news_id"])
            deduplicated.append(row)
    return deduplicated, parser.last_page


_TYPE_BLOCK_RE = re.compile(
    rb"<div\b[^>]*class\s*=\s*[\"'][^\"']*\btype\b[^\"']*[\"'][^>]*>.*?</div\s*>",
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>", re.S)
_ALT_RE = re.compile(r"\balt\s*=\s*([\"'])(.*?)\1", re.I | re.S)


def _visible_text(raw_html: str) -> str:
    alternatives = [match.group(2) for match in _ALT_RE.finditer(raw_html)]
    visible = _TAG_RE.sub(" ", raw_html)
    return _normalize_inline(html.unescape(" ".join([*alternatives, visible])))


def _normalize_inline(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class LicenseEvidence:
    code: str
    exact_html: str
    exact_snippet: str
    sha256: str
    training_use_permitted: bool
    evaluation_use_permitted: bool
    status: str
    permission_basis: str

    def to_dict(self) -> dict:
        return {
            "license_code": self.code,
            "license_exact_html": self.exact_html,
            "license_exact_snippet": self.exact_snippet,
            "license_evidence_sha256": self.sha256,
            "training_use_permitted": self.training_use_permitted,
            "evaluation_use_permitted": self.evaluation_use_permitted,
            "license_status": self.status,
            "permission_basis": self.permission_basis,
        }


def _classify_license_snippet(snippet: str) -> tuple[str, bool, str]:
    compact = re.sub(r"\s+", "", snippet)
    if re.search(r"(?:2|3|4)유형", compact):
        raise CollectionError("blocked KOG-L type on detail page")
    text_limited = "텍스트" in compact
    free_use = "자유이용" in compact or "자유롭게이용" in compact
    if not text_limited or not free_use:
        raise CollectionError("detail page lacks explicit text free-use wording")
    if "0유형" in compact and "조건없이" in compact:
        return (
            "KOGL-0",
            True,
            "page-level KOG-L 0 marker and condition-free text wording",
        )
    if re.search(r"(?:AI|인공지능)유형", compact, re.I) and re.search(
        r"(?:AI|인공지능).*학습", compact, re.I
    ):
        return "KOGL-AI", True, "page-level AI marker and explicit AI-learning wording"
    if "1유형" in compact and "출처표시" in compact:
        return (
            "KOGL-1",
            True,
            "page-level KOG-L 1 text wording; official 2025-Q3 guidance "
            "permits AI training with source attribution",
        )
    raise CollectionError("unsupported or ambiguous KOG-L marker on detail page")


def extract_license_evidence(
    raw_html: bytes, *, charset: str = "utf-8"
) -> LicenseEvidence:
    matches = list(_TYPE_BLOCK_RE.finditer(raw_html))
    if not matches:
        raise CollectionError("detail page has no item-level KOG-L type block")
    accepted: list[LicenseEvidence] = []
    errors: list[str] = []
    for match in matches:
        block = match.group(0)
        try:
            exact_html = block.decode(charset, errors="strict")
            snippet = _visible_text(exact_html)
            code, training_permitted, basis = _classify_license_snippet(snippet)
        except (LookupError, UnicodeDecodeError, CollectionError) as exc:
            errors.append(str(exc))
            continue
        accepted.append(
            LicenseEvidence(
                code=code,
                exact_html=exact_html,
                exact_snippet=snippet,
                sha256=sha256_bytes(block),
                training_use_permitted=training_permitted,
                evaluation_use_permitted=True,
                status="training_eligible" if training_permitted else "license_hold",
                permission_basis=basis,
            )
        )
    blocked_errors = sorted({item for item in errors if "blocked KOG-L" in item})
    if blocked_errors:
        raise CollectionError(
            f"detail page licence rejected: {'; '.join(blocked_errors)}"
        )
    if not accepted:
        reason = "; ".join(sorted(set(errors))) or "no supported type block"
        raise CollectionError(f"detail page licence rejected: {reason}")
    codes = {item.code for item in accepted}
    hashes = {item.sha256 for item in accepted}
    if len(codes) != 1 or len(hashes) != 1:
        raise CollectionError("conflicting item-level licence blocks")
    return accepted[0]


_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "figure",
        "figcaption",
        "picture",
        "video",
        "audio",
        "svg",
        "canvas",
    }
)
_SKIP_CLASS_PARTS = (
    "caption",
    "photo",
    "image",
    "thumb",
    "kogl",
    "law_copy",
    "copyright",
    "remark",
    "filedown",
    "reporter",
    "profile",
)
_BLOCK_TAGS = frozenset(
    {"p", "li", "tr", "td", "th", "h2", "h3", "h4", "h5", "blockquote", "br"}
)


class _BodyParser(HTMLParser):
    """Extract text from the first article ``view_cont`` subtree only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []
        self.body_root_depth: int | None = None
        self.body_seen = False
        self.body_complete = False
        self.parts: list[str] = []
        self.agency = ""
        self._agency_link_open = False
        self._agency_link_parts: list[str] = []
        self.og_title = ""

    @property
    def in_body(self) -> bool:
        return self.body_root_depth is not None

    @property
    def skipped(self) -> bool:
        return any(frame[1] for frame in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = frozenset(values.get("class", "").split())
        # Press-release pages carry the agency on ``view_cont[data-title]``.
        # Policy-news pages instead render it as an article-header link.  Read
        # only that structural link; never infer an agency from arbitrary body
        # prose or from the listing page.
        if tag == "a" and "gotosite" in classes and not self.agency:
            self._agency_link_open = True
            self._agency_link_parts = []
        if tag == "meta" and values.get("property", "").lower() == "og:title":
            self.og_title = _normalize_inline(html.unescape(values.get("content", "")))
        is_root = (
            not self.body_seen
            and self.body_root_depth is None
            and "view_cont" in classes
        )
        class_text = " ".join(classes).casefold()
        if self.in_body and any(
            part in class_text for part in ("remark", "kogl", "law_copy", "copyright")
        ):
            self.body_complete = True
        skip = (
            self.skipped
            or tag in _SKIP_TAGS
            or any(part in class_text for part in _SKIP_CLASS_PARTS)
        )
        if is_root:
            self.stack = [(tag, skip, True)]
            self.body_root_depth = 1
            self.body_seen = True
            root_agency = _normalize_inline(values.get("data-title", ""))
            if root_agency:
                self.agency = root_agency
        elif self.in_body and tag not in _VOID_TAGS:
            self.stack.append((tag, skip, is_root))
        if self.in_body and not self.body_complete and not skip and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._agency_link_open:
            self._agency_link_parts.append(data)
        if self.in_body and not self.body_complete and not self.skipped:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._agency_link_open:
            agency = _normalize_inline(
                html.unescape(" ".join(self._agency_link_parts))
            )
            if agency:
                self.agency = agency
            self._agency_link_open = False
            self._agency_link_parts = []
        if not self.in_body:
            return
        if (
            self.in_body
            and not self.body_complete
            and not self.skipped
            and tag in _BLOCK_TAGS
        ):
            self.parts.append("\n")
        while self.stack:
            popped_tag, _, is_root = self.stack.pop()
            if is_root:
                self.body_root_depth = None
            if popped_tag == tag:
                break

    def body(self) -> str:
        lines = [_normalize_inline(line) for line in "".join(self.parts).splitlines()]
        return "\n\n".join(line for line in lines if line)


_DATE_PUBLISHED_RE = re.compile(
    r'["\']datePublished["\']\s*:\s*["\'](?P<value>\d{4}-\d{2}-\d{2}(?:T[^"\']*)?)["\']',
    re.I,
)


def extract_page_metadata(html_text: str) -> dict:
    parser = _BodyParser()
    parser.feed(html_text)
    date_match = _DATE_PUBLISHED_RE.search(html_text)
    published_at = date_match.group("value") if date_match else ""
    if not parser.og_title:
        raise CollectionError("detail page missing og:title")
    if not parser.agency:
        raise CollectionError("detail page missing source agency")
    if not published_at:
        raise CollectionError("detail page missing datePublished")
    body = parser.body()
    if not body:
        raise CollectionError("detail page has no extractable text body")
    return {
        "title": parser.og_title,
        "source_agency": parser.agency,
        "published_at": published_at,
        "body": body,
    }


def section_text(
    body: str,
    *,
    minimum: int = MIN_SECTION_CHARS,
    maximum: int = MAX_SECTION_CHARS,
) -> list[dict]:
    """Split normalized body text into exact, auditable 1,200--3,200 char spans."""
    if minimum < 1 or maximum < minimum:
        raise ValueError("invalid section bounds")
    if len(body) < minimum:
        return []
    boundaries = {match.end() for match in re.finditer(r"\n\n|[.!?。]\s+", body)}
    sections: list[dict] = []
    cursor = 0
    body_length = len(body)
    while body_length - cursor > maximum:
        desired = maximum
        remainder_if_max = body_length - (cursor + maximum)
        if remainder_if_max < minimum:
            desired = body_length - cursor - minimum
        floor = cursor + minimum
        ceiling = cursor + desired
        candidates = [point for point in boundaries if floor <= point <= ceiling]
        end = max(candidates, default=ceiling)
        start = cursor
        while start < end and body[start].isspace():
            start += 1
        while end > start and body[end - 1].isspace():
            end -= 1
        if end - start < minimum:
            raise CollectionError("unable to produce a minimum-length body section")
        sections.append({"text": body[start:end], "body_start": start, "body_end": end})
        cursor = end
    start = cursor
    while start < body_length and body[start].isspace():
        start += 1
    end = body_length
    while end > start and body[end - 1].isspace():
        end -= 1
    if end - start >= minimum:
        sections.append({"text": body[start:end], "body_start": start, "body_end": end})
    elif sections and len(sections[-1]["text"]) + (end - start) <= maximum:
        prior = sections.pop()
        merged_start = prior["body_start"]
        merged_text = body[merged_start:end].strip()
        sections.append(
            {"text": merged_text, "body_start": merged_start, "body_end": end}
        )
    for section in sections:
        length = len(section["text"])
        if not minimum <= length <= maximum:
            raise CollectionError(f"section length outside bounds: {length}")
        if body[section["body_start"] : section["body_end"]] != section["text"]:
            raise CollectionError(
                "section offsets do not map exactly to normalized body"
            )
    return sections


def make_proxy_record(
    *,
    listing: Mapping[str, object],
    metadata: Mapping[str, str],
    section: Mapping[str, object],
    section_index: int,
    payload: HtmlPayload,
    licence: LicenseEvidence,
    retrieved_at: str,
) -> dict:
    news_id = str(listing["news_id"])
    kind = COLLECTION_KINDS[str(listing["collection_kind"])]
    source_hash = sha256_bytes(payload.body)
    record = {
        "doc_id": f"korea-policy-{news_id}-s{section_index:02d}",
        "text": str(section["text"]),
        "title": metadata["title"],
        "label": "S3",
        "document_origin": "public_real",
        "proxy_role": "public_document",
        "document_family_id": f"korea-policy-{news_id}",
        "family_profile_id": f"korea-policy-{kind.name}",
        "document_type": kind.document_type,
        "domain": "public_policy",
        "industry": "government",
        "source_id": "korea-policy-briefing",
        "source_reference": str(listing["source_reference"]),
        "source_url": str(listing["source_reference"]),
        "source_title": metadata["title"],
        "source_agency": metadata["source_agency"],
        "published_at": metadata["published_at"],
        "source_license": licence.code,
        "source_sha256": source_hash,
        "raw_html_sha256": source_hash,
        "retrieved_at": retrieved_at,
        "license_evidence_sha256": licence.sha256,
        "license_exact_snippet": licence.exact_snippet,
        "license_status": licence.status,
        "training_use_permitted": licence.training_use_permitted,
        "evaluation_use_permitted": licence.evaluation_use_permitted,
        "evaluation_availability": "eligible",
        "excluded_modalities": ["image", "caption", "video", "attachment"],
        "section_index": section_index,
        "body_start": int(section["body_start"]),
        "body_end": int(section["body_end"]),
        "collection_schema": "korea-policy-public-proxy-v1",
    }
    training_check = validate_proxy_record(
        record, stage="candidate", intended_use="training"
    )
    evaluation_check = validate_proxy_record(
        record, stage="candidate", intended_use="evaluation"
    )
    record["proxy_training_validation"] = training_check.to_dict()
    record["proxy_evaluation_validation"] = evaluation_check.to_dict()
    # This collector's primary output is the public-real evaluation challenge.
    # Keep the historical key as an evaluation-scoped compatibility alias so a
    # KOGL-1 record is not misleadingly reported as an invalid candidate merely
    # because it is intentionally excluded from training.
    record["proxy_candidate_validation"] = evaluation_check.to_dict()
    return record


def create_unique_run_dir(output_root: Path, run_id: str | None = None) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    chosen = (
        run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", chosen):
        raise CollectionError(f"unsafe run id: {chosen}")
    path = output_root / chosen
    try:
        path.mkdir()
    except FileExistsError as exc:
        raise CollectionError(f"run directory already exists: {path}") from exc
    return path


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise CollectionError(f"refusing to replace existing output: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".part", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(canonical_json_bytes(row) for row in rows)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def collect(
    args: argparse.Namespace,
    *,
    client: SafeHttpClient | None = None,
) -> tuple[Path, dict]:
    client = client or SafeHttpClient(timeout=args.timeout)
    run_dir = create_unique_run_dir(args.output_root, args.run_id)
    started_at = utc_now()
    chosen_kind_names = list(COLLECTION_KINDS) if args.kind == "both" else [args.kind]
    listings: list[dict] = []
    listing_audit: list[dict] = []
    seen_news_ids: set[str] = set()
    for kind_name in chosen_kind_names:
        kind = COLLECTION_KINDS[kind_name]
        for page_index in range(
            args.start_list_page,
            args.start_list_page + args.list_pages,
        ):
            list_url = build_list_url(kind, page_index)
            payload = client.request_html(
                list_url,
                max_bytes=args.max_html_bytes,
                expected_paths={kind.list_path},
            )
            rows, last_page = parse_listing(payload.text, kind)
            listing_audit.append(
                {
                    "collection_kind": kind.name,
                    "page_index": page_index,
                    "source_url": list_url,
                    "final_url": payload.final_url,
                    "raw_html_sha256": sha256_bytes(payload.body),
                    "listed_rows": len(rows),
                    "reported_last_page": last_page,
                    "listing_lead_at_least_1200": sum(
                        int(row["listing_lead_chars"]) >= MIN_SECTION_CHARS
                        for row in rows
                    ),
                }
            )
            for row in rows:
                if row["news_id"] not in seen_news_ids:
                    seen_news_ids.add(row["news_id"])
                    listings.append(row)
    selected = listings[: args.limit]
    page_results: list[dict] = []
    records: list[dict] = []
    for position, listing in enumerate(selected, start=1):
        retrieved_at = utc_now()
        result = {
            "news_id": listing["news_id"],
            "collection_kind": listing["collection_kind"],
            "source_reference": listing["source_reference"],
            "fetch_url": listing["fetch_url"],
            "listing_title": listing["title"],
            "listing_lead_chars": listing["listing_lead_chars"],
            "retrieved_at": retrieved_at,
        }
        try:
            kind = COLLECTION_KINDS[str(listing["collection_kind"])]
            payload = client.request_html(
                str(listing["fetch_url"]),
                max_bytes=args.max_html_bytes,
                expected_paths={kind.mobile_detail_path, kind.detail_path},
            )
            expected_id = str(listing["news_id"])
            final_query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(payload.final_url).query
            )
            if (final_query.get("newsId") or [""])[0] != expected_id:
                raise CollectionError("detail redirect changed newsId")
            licence = extract_license_evidence(payload.body, charset=payload.charset)
            metadata = extract_page_metadata(payload.text)
            sections = section_text(
                metadata["body"], minimum=args.min_chars, maximum=args.max_chars
            )
            page_records = [
                make_proxy_record(
                    listing=listing,
                    metadata=metadata,
                    section=section,
                    section_index=index,
                    payload=payload,
                    licence=licence,
                    retrieved_at=retrieved_at,
                )
                for index, section in enumerate(sections, start=1)
            ]
            raw_path: Path | None = None
            text_path: Path | None = None
            if args.download:
                raw_path = run_dir / "raw_html" / f"{expected_id}.html"
                text_path = run_dir / "text" / f"{expected_id}.txt"
                atomic_write(raw_path, payload.body)
                atomic_write(text_path, (metadata["body"] + "\n").encode("utf-8"))
            result.update(
                {
                    "status": "accepted" if sections else "quality_hold",
                    "final_url": payload.final_url,
                    "raw_html_sha256": sha256_bytes(payload.body),
                    "raw_html_bytes": len(payload.body),
                    "raw_html_path": _relative(raw_path, run_dir) if raw_path else None,
                    "text_path": _relative(text_path, run_dir) if text_path else None,
                    "source_title": metadata["title"],
                    "source_agency": metadata["source_agency"],
                    "published_at": metadata["published_at"],
                    "body_chars": len(metadata["body"]),
                    "section_count": len(sections),
                    **licence.to_dict(),
                }
            )
            records.extend(page_records)
        except CollectionError as exc:
            result.update({"status": "rejected", "rejection_reason": str(exc)})
        page_results.append(result)
        if args.delay_seconds and position < len(selected):
            time.sleep(args.delay_seconds)

    evaluation_valid_records = sum(
        bool(record["proxy_evaluation_validation"]["ok"]) for record in records
    )
    training_valid_records = sum(
        bool(record["proxy_training_validation"]["ok"]) for record in records
    )
    training_eligible_pages = sum(
        row.get("training_use_permitted") is True for row in page_results
    )
    evaluation_eligible_pages = sum(
        row.get("evaluation_use_permitted") is True for row in page_results
    )
    pages_with_sections = sum(
        int(row.get("section_count") or 0) > 0 for row in page_results
    )
    accepted_pages = sum(row.get("status") == "accepted" for row in page_results)
    sample_size = len(page_results)
    section_yield = len(records) / sample_size if sample_size else 0.0
    projected_detail_pages_for_300 = (
        int((300 / section_yield) + 0.999999) if section_yield > 0 else None
    )
    manifest = {
        "schema": "korea-policy-public-proxy-run-v1",
        "run_id": run_dir.name,
        "mode": "download" if args.download else "discover_only",
        "started_at": started_at,
        "completed_at": utc_now(),
        "policy": {
            "allowed_hosts": sorted(ALLOWED_HOSTS),
            "item_level_license_required": True,
            "accepted_license_markers": ["KOGL-0", "KOGL-1", "KOGL-AI"],
            "blocked_license_markers": ["KOGL-2", "KOGL-3", "KOGL-4"],
            "kogl_1_training_policy": KOGL_1_TRAINING_POLICY,
            "training_permission_evidence": {
                "issuer": "Korea Culture Information Service",
                "title": "2025 Q3 public-copyright issue report",
                "url": KOGL_AI_TRAINING_GUIDANCE_URL,
                "rule": "KOG-L type 1 text is usable for AI training with source attribution",
                "attribution_required": True,
            },
            "excluded_modalities": ["image", "caption", "video", "attachment"],
            "section_chars": {"minimum": args.min_chars, "maximum": args.max_chars},
            "hard_detail_page_limit": HARD_LIMIT,
        },
        "request": {
            "kind": args.kind,
            "start_list_page": args.start_list_page,
            "list_pages": args.list_pages,
            "detail_limit": args.limit,
            "max_html_bytes": args.max_html_bytes,
        },
        "inventory": {
            "listing_pages_fetched": len(listing_audit),
            "unique_pages_listed": len(listings),
            "reported_last_page_max": max(
                (int(row["reported_last_page"]) for row in listing_audit), default=0
            ),
            "listing_audit": listing_audit,
        },
        "pilot": {
            "detail_pages_attempted": sample_size,
            "accepted_pages": accepted_pages,
            "rejected_pages": sum(
                row.get("status") == "rejected" for row in page_results
            ),
            "quality_hold_pages": sum(
                row.get("status") == "quality_hold" for row in page_results
            ),
            "pages_with_sections": pages_with_sections,
            "training_eligible_pages": training_eligible_pages,
            "license_hold_pages": evaluation_eligible_pages - training_eligible_pages,
            "evaluation_eligible_pages": evaluation_eligible_pages,
            "sections": len(records),
            "evaluation_validation_passed_sections": evaluation_valid_records,
            "evaluation_validation_held_sections": (
                len(records) - evaluation_valid_records
            ),
            "training_validation_passed_sections": training_valid_records,
            "training_validation_held_sections": len(records) - training_valid_records,
            # Compatibility aliases follow this collector's evaluation purpose.
            "candidate_validation_passed_sections": evaluation_valid_records,
            "candidate_validation_held_sections": (
                len(records) - evaluation_valid_records
            ),
            "section_yield_per_detail_page": round(section_yield, 4),
            "projected_detail_pages_for_300_sections": projected_detail_pages_for_300,
            "projection_caveat": (
                "Observed-run yield only; future listing pages may differ and every "
                "item still requires page-level licence verification."
            ),
        },
        "pages": page_results,
    }
    if args.download:
        atomic_write(run_dir / "records.jsonl", _jsonl_bytes(records))
    atomic_write(run_dir / "manifest.json", canonical_json_bytes(manifest))
    return run_dir, manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--discover-only", action="store_true")
    mode.add_argument("--download", action="store_true")
    parser.add_argument(
        "--kind", choices=[*COLLECTION_KINDS, "both"], default="press_release"
    )
    parser.add_argument("--start-list-page", type=int, default=1)
    parser.add_argument("--list-pages", type=int, default=1)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--min-chars", type=int, default=MIN_SECTION_CHARS)
    parser.add_argument("--max-chars", type=int, default=MAX_SECTION_CHARS)
    parser.add_argument("--max-html-bytes", type=int, default=DEFAULT_MAX_HTML_BYTES)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay-seconds", type=float, default=0.2)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= HARD_LIMIT:
        parser.error(f"--limit must be between 1 and {HARD_LIMIT}")
    if not 1 <= args.list_pages <= 100:
        parser.error("--list-pages must be between 1 and 100")
    if not 1 <= args.start_list_page <= 10_000:
        parser.error("--start-list-page must be between 1 and 10000")
    if args.start_list_page + args.list_pages - 1 > 10_000:
        parser.error("requested listing-page range exceeds 10000")
    if args.min_chars < MIN_SECTION_CHARS:
        parser.error(f"--min-chars cannot be below {MIN_SECTION_CHARS}")
    if args.max_chars > MAX_SECTION_CHARS or args.max_chars < args.min_chars:
        parser.error(f"--max-chars must be between --min-chars and {MAX_SECTION_CHARS}")
    if args.max_html_bytes < 1024 or args.max_html_bytes > 16 * 1024 * 1024:
        parser.error("--max-html-bytes must be between 1024 and 16777216")
    if args.timeout <= 0 or not 0 <= args.delay_seconds <= 10:
        parser.error("timeout must be positive and delay must be between 0 and 10")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_dir, manifest = collect(args)
    except CollectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "mode": manifest["mode"],
                "pilot": manifest["pilot"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
