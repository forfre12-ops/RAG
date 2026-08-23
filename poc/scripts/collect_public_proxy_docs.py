"""Discover and optionally download approved public proxy documents.

The default mode is discovery only: it writes an immutable run manifest and
exact snapshots of the official discovery/licence responses, but no document
bodies.  ``--download`` is deliberately required before any attachment is
retrieved.

Only three registry sources are implemented here:

* MOLIT aggregate-resource survey reports (AGRIS JSON listing API),
* Gyeonggi disaster-safety manuals (official attachment links), and
* KMA weather yearbooks (official archive pages).

Every network request is HTTPS-only and host-allowlisted.  Downloads are
streamed through a byte cap, checked for MIME and file magic, hashed, and
published atomically inside a unique run directory.  HTML error pages are
never accepted as documents.  A catalog-level licence is recorded but never
promoted to attachment-level permission: documents remain on ``license_hold``
until an item-level official snapshot is available.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping, Sequence


_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.validate_public_source_registry import (  # noqa: E402
    DEFAULT_REGISTRY,
    load_registry,
    policy_status,
)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 "
    "koipa-public-proxy-collector/1.0"
)
DEFAULT_OUTPUT_ROOT = _ROOT / "datasets" / "proxy_gold" / "public_proxy_runs"
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DISCOVERY_MAX_BYTES = 16 * 1024 * 1024
LICENSE_MAX_BYTES = 2 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class CollectionError(RuntimeError):
    """Fail-closed collection or policy error."""


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    data_go_id: str
    landing_url: str
    discovery_hosts: frozenset[str]
    download_hosts: frozenset[str]
    suffixes: frozenset[str]

    @property
    def license_url(self) -> str:
        return f"https://www.data.go.kr/catalog/{self.data_go_id}/fileData.json"


SOURCE_SPECS: dict[str, SourceSpec] = {
    "molit-aggregate-resource-surveys": SourceSpec(
        source_id="molit-aggregate-resource-surveys",
        data_go_id="15122643",
        landing_url="https://www.agris.go.kr/main/info/report",
        discovery_hosts=frozenset({"www.agris.go.kr"}),
        download_hosts=frozenset({"www.agris.go.kr"}),
        suffixes=frozenset({".pdf"}),
    ),
    "gyeonggi-disaster-safety-manuals": SourceSpec(
        source_id="gyeonggi-disaster-safety-manuals",
        data_go_id="15048571",
        landing_url=(
            "https://data.gg.go.kr/portal/data/service/selectServicePage.do?"
            "page=1&sortColumn=&sortDirection=&"
            "infId=5XP8QWO5939KP255ST221203200&infSeq=1"
        ),
        discovery_hosts=frozenset({"data.gg.go.kr"}),
        download_hosts=frozenset({"data.gg.go.kr"}),
        suffixes=frozenset({".hwp"}),
    ),
    "kma-weather-yearbooks": SourceSpec(
        source_id="kma-weather-yearbooks",
        data_go_id="15050682",
        landing_url=(
            "https://www.kma.go.kr/kma/archive/pub.jsp?"
            "field1=grp&text1=yearbook"
        ),
        discovery_hosts=frozenset({"www.kma.go.kr"}),
        download_hosts=frozenset({"www.kma.go.kr"}),
        suffixes=frozenset({".pdf"}),
    ),
}

ALL_OFFICIAL_HOSTS = frozenset(
    {"www.data.go.kr"}
    | set().union(*(spec.discovery_hosts for spec in SOURCE_SPECS.values()))
    | set().union(*(spec.download_hosts for spec in SOURCE_SPECS.values()))
)

ALLOWED_MIME_BY_SUFFIX = {
    ".pdf": frozenset(
        {"application/pdf", "application/octet-stream", "binary/octet-stream"}
    ),
    ".hwp": frozenset(
        {
            "application/x-hwp",
            "application/haansofthwp",
            "application/vnd.hancom.hwp",
            "application/octet-stream",
            "binary/octet-stream",
            "application/x-download",
        }
    ),
}

_KOGL_1_TEXTS = frozenset(
    {
        "공공저작물 : 출처표시 (제 1유형)",
        "공공저작물_출처표시",
        "공공누리 제1유형",
        "KOGL-1",
    }
)
_HTML_PREFIXES = (b"<!doctype html", b"<html", b"<head", b"<body")
_HWP_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_HWP_V3_MAGIC = b"HWP Document File"


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


def validate_https_url(url: str, allowed_hosts: Iterable[str]) -> str:
    """Validate an absolute official URL and return its normalized host."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = {value.lower().rstrip(".") for value in allowed_hosts}
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
class HttpPayload:
    body: bytes
    final_url: str
    mime: str
    charset: str | None
    status: int


class SafeHttpClient:
    """Small urllib client with persistent cookies and safe redirects."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.cookies = CookieJar()

    def open_stream(
        self,
        url: str,
        *,
        allowed_hosts: Iterable[str],
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ):
        allowed = frozenset(allowed_hosts)
        validate_https_url(url, allowed)
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            _AllowlistRedirectHandler(allowed),
        )
        response = opener.open(request, timeout=self.timeout)  # noqa: S310
        validate_https_url(response.geturl(), allowed)
        return response

    def request_bytes(
        self,
        url: str,
        *,
        allowed_hosts: Iterable[str],
        max_bytes: int,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpPayload:
        with self.open_stream(
            url,
            allowed_hosts=allowed_hosts,
            method=method,
            data=data,
            headers=headers,
        ) as response:
            declared = _content_length(response.headers)
            if declared is not None and declared > max_bytes:
                raise CollectionError(
                    f"response exceeds cap: {declared}>{max_bytes}: {url}"
                )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise CollectionError(
                    f"response exceeds cap while streaming: >{max_bytes}: {url}"
                )
            return HttpPayload(
                body=body,
                final_url=response.geturl(),
                mime=_content_type(response.headers),
                charset=_content_charset(response.headers),
                status=int(getattr(response, "status", 200)),
            )


def _header_get(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    return None if value is None else str(value)


def _content_length(headers: object) -> int | None:
    value = _header_get(headers, "Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as exc:
        raise CollectionError(f"invalid Content-Length: {value}") from exc
    if length < 0:
        raise CollectionError(f"invalid Content-Length: {value}")
    return length


def _content_type(headers: object) -> str:
    getter = getattr(headers, "get_content_type", None)
    if getter is not None:
        return str(getter()).lower()
    raw = _header_get(headers, "Content-Type") or ""
    return raw.split(";", 1)[0].strip().lower()


def _content_charset(headers: object) -> str | None:
    getter = getattr(headers, "get_content_charset", None)
    if getter is not None:
        value = getter()
        return None if value is None else str(value)
    raw = _header_get(headers, "Content-Type") or ""
    match = re.search(r"charset=([^;\s]+)", raw, re.IGNORECASE)
    return match.group(1).strip('"\'') if match else None


def _decode(payload: HttpPayload) -> str:
    candidates = [payload.charset, "utf-8", "euc-kr", "cp949"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return payload.body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.body.decode("utf-8", errors="replace")


def _looks_like_html(prefix: bytes) -> bool:
    head = prefix[:4096].lstrip().lower()
    return head.startswith(_HTML_PREFIXES) or b"<title" in head[:1024]


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise CollectionError(f"refusing to replace immutable artifact: {path}")
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def create_unique_run_dir(output_root: Path, run_id: str | None = None) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    generated = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(4)
    )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", generated):
        raise CollectionError("run_id contains unsafe characters")
    run_dir = output_root / generated
    try:
        run_dir.mkdir()
    except FileExistsError as exc:
        raise CollectionError(f"run directory already exists: {run_dir}") from exc
    return run_dir


def _snapshot(
    run_dir: Path,
    *,
    source_id: str,
    kind: str,
    sequence: int,
    payload: HttpPayload,
    suffix: str,
) -> dict:
    relative = Path("evidence") / source_id / f"{kind}-{sequence:03d}{suffix}"
    _atomic_write(run_dir / relative, payload.body)
    return {
        "url": payload.final_url,
        "snapshot_path": relative.as_posix(),
        "snapshot_sha256": sha256_bytes(payload.body),
        "response_mime": payload.mime,
        "http_status": payload.status,
    }


def normalize_catalog_license(value: object) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    compact = text.replace(" ", "")
    if text in _KOGL_1_TEXTS or (
        "출처표시" in compact and ("제1유형" in compact or "1유형" in compact)
    ):
        return "KOGL-1"
    if "이용허락범위제한없음" in compact:
        return "KOGL-0"
    return None


def verify_license_payload(payload: bytes, expected_code: str) -> dict:
    """Verify the exact data.go.kr dataset licence snapshot."""
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError("official licence evidence is not valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise CollectionError("official licence evidence root is not an object")
    observed_text = str(parsed.get("license") or "").strip()
    observed_code = normalize_catalog_license(observed_text)
    if observed_code != expected_code:
        raise CollectionError(
            "official licence mismatch: "
            f"registry={expected_code}, evidence={observed_code or observed_text or 'missing'}"
        )
    creator = parsed.get("creator")
    creator_name = (
        str(creator.get("name") or "").strip()
        if isinstance(creator, Mapping)
        else ""
    )
    return {
        "observed_license": observed_text,
        "observed_license_code": observed_code,
        "dataset_name": str(parsed.get("name") or "").strip(),
        "creator_name": creator_name,
        "catalog_modified": parsed.get("dateModified"),
    }


def select_registry_sources(
    registry: Mapping[str, object], requested_source_ids: Sequence[str]
) -> list[dict]:
    rows = registry.get("sources")
    if not isinstance(rows, list):
        raise CollectionError("registry sources must be a list")
    by_id = {
        str(row.get("source_id")): dict(row)
        for row in rows
        if isinstance(row, Mapping)
    }
    selected: list[dict] = []
    for source_id in requested_source_ids:
        if source_id not in SOURCE_SPECS:
            raise CollectionError(f"collector does not implement source: {source_id}")
        row = by_id.get(source_id)
        if row is None:
            raise CollectionError(f"source absent from registry: {source_id}")
        required, reason = policy_status(row)
        if row.get("status") != required or required == "blocked":
            raise CollectionError(
                f"source is not registry-approved: {source_id}: {reason}"
            )
        if row.get("license") not in {"KOGL-0", "KOGL-1", "KOGL-AI", "EXPLICIT-ML-TRAINING"}:
            raise CollectionError(f"source licence is not approved: {source_id}")
        selected.append(row)
    return selected


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        self._current = {key.casefold(): value or "" for key, value in attrs}
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._current is not None:
            anchor = dict(self._current)
            anchor["text"] = re.sub(r"\s+", " ", " ".join(self._text)).strip()
            self.anchors.append(anchor)
            self._current = None
            self._text = []


def safe_filename(value: str, *, fallback: str = "document") -> str:
    name = unicodedata.normalize("NFKC", html.unescape(value)).replace("\\", "/")
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name).strip(" .")
    if not name:
        name = fallback
    if len(name) > 180:
        suffix = Path(name).suffix[:16]
        name = name[: 180 - len(suffix)].rstrip() + suffix
    return name


def _document_id(source_id: str, material: object) -> str:
    digest = sha256_bytes(canonical_json_bytes([source_id, material]))[:20]
    return f"{source_id}-{digest}"


def parse_kma_page(page_html: str, page_url: str) -> list[dict]:
    parser = _AnchorParser()
    parser.feed(page_html)
    documents: list[dict] = []
    seen: set[str] = set()
    for anchor in parser.anchors:
        classes = set(anchor.get("class", "").split())
        href = anchor.get("href", "").strip()
        if "down" not in classes or not href:
            continue
        url = urllib.parse.urljoin(page_url, href)
        parsed = urllib.parse.urlsplit(url)
        filename = safe_filename(urllib.parse.unquote(Path(parsed.path).name))
        if (
            Path(parsed.path).suffix.casefold() != ".pdf"
            or not filename.casefold().startswith("yearbook_")
        ):
            continue
        validate_https_url(url, SOURCE_SPECS["kma-weather-yearbooks"].download_hosts)
        if url in seen:
            continue
        seen.add(url)
        year_match = re.search(r"(?:19|20)\d{2}", filename)
        title = (
            f"{year_match.group(0)}년 기상연감"
            if year_match
            else Path(filename).stem.replace("_", " ")
        )
        documents.append(
            {
                "source_id": "kma-weather-yearbooks",
                "title": title,
                "filename": filename,
                "format": "PDF",
                "declared_size_bytes": None,
                "landing_url": SOURCE_SPECS["kma-weather-yearbooks"].landing_url,
                "download_request": {"url": url, "method": "GET"},
                "upstream": {"archive_page_url": page_url},
                "document_id": _document_id("kma-weather-yearbooks", url),
            }
        )
    return documents


def parse_gyeonggi_page(page_html: str, page_url: str) -> list[dict]:
    parser = _AnchorParser()
    parser.feed(page_html)
    candidates: list[tuple[str, str]] = []
    for anchor in parser.anchors:
        href = html.unescape(anchor.get("href", "").strip())
        if "downloadFileData.do" in href:
            candidates.append((href, anchor.get("text", "")))

    # Some versions render attachment URLs in JavaScript/data attributes.
    for match in re.finditer(
        r"(?P<url>(?:https://data\.gg\.go\.kr)?/portal/data/file/"
        r"downloadFileData\.do\?[^\"'<>\s]+)",
        html.unescape(page_html),
        re.IGNORECASE,
    ):
        candidates.append((match.group("url"), ""))

    documents: list[dict] = []
    seen: set[str] = set()
    expected_inf_id = "5XP8QWO5939KP255ST221203200"
    for href, text in candidates:
        url = urllib.parse.urljoin(page_url, href)
        validate_https_url(
            url, SOURCE_SPECS["gyeonggi-disaster-safety-manuals"].download_hosts
        )
        parsed = urllib.parse.urlsplit(url)
        if parsed.path != "/portal/data/file/downloadFileData.do":
            continue
        query = urllib.parse.parse_qs(parsed.query)
        if (
            query.get("infId", [""])[0] != expected_inf_id
            or query.get("infSeq", [""])[0] != "1"
            or not query.get("fileSeq", [""])[0]
        ):
            continue
        normalized_url = urllib.parse.urlunsplit(parsed)
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        filename = safe_filename(text, fallback=f"manual-{query['fileSeq'][0]}.hwp")
        if Path(filename).suffix.casefold() != ".hwp":
            filename = f"{filename}.hwp"
        documents.append(
            {
                "source_id": "gyeonggi-disaster-safety-manuals",
                "title": Path(filename).stem,
                "filename": filename,
                "format": "HWP",
                "declared_size_bytes": None,
                "landing_url": SOURCE_SPECS[
                    "gyeonggi-disaster-safety-manuals"
                ].landing_url,
                "download_request": {"url": normalized_url, "method": "GET"},
                "upstream": {
                    "inf_id": expected_inf_id,
                    "inf_seq": "1",
                    "file_seq": query["fileSeq"][0],
                },
                "document_id": _document_id(
                    "gyeonggi-disaster-safety-manuals", normalized_url
                ),
            }
        )
    return documents


def parse_gyeonggi_payload(payload: Mapping[str, object], api_url: str) -> list[dict]:
    """Parse the official Gyeonggi attachment-list JSON response."""
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise CollectionError("Gyeonggi attachment API lacks data list")
    source_id = "gyeonggi-disaster-safety-manuals"
    spec = SOURCE_SPECS[source_id]
    expected_inf_id = "5XP8QWO5939KP255ST221203200"
    documents: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise CollectionError("Gyeonggi attachment row is not an object")
        inf_id = str(row.get("infId") or "").strip()
        inf_seq = str(row.get("infSeq") or "").strip()
        file_seq = str(row.get("fileSeq") or "").strip()
        if inf_id != expected_inf_id or inf_seq != "1" or not file_seq:
            raise CollectionError("Gyeonggi attachment identity mismatch")
        extension = str(row.get("fileExt") or "").strip().lower().lstrip(".")
        if extension != "hwp":
            continue
        filename = safe_filename(str(row.get("viewFileNm") or ""))
        if Path(filename).suffix.casefold() != ".hwp":
            filename = f"{filename}.hwp"
        query = urllib.parse.urlencode(
            {"infId": inf_id, "infSeq": inf_seq, "fileSeq": file_seq}
        )
        download_url = (
            "https://data.gg.go.kr/portal/data/file/downloadFileData.do?" + query
        )
        validate_https_url(download_url, spec.download_hosts)
        if download_url in seen:
            continue
        seen.add(download_url)
        try:
            declared_size = int(row.get("fileSize") or 0) or None
        except (TypeError, ValueError) as exc:
            raise CollectionError("Gyeonggi attachment has invalid file size") from exc
        documents.append(
            {
                "source_id": source_id,
                "title": Path(filename).stem,
                "filename": filename,
                "format": "HWP",
                "declared_size_bytes": declared_size,
                "landing_url": spec.landing_url,
                "download_request": {"url": download_url, "method": "GET"},
                "upstream": {
                    "inf_id": inf_id,
                    "inf_seq": inf_seq,
                    "file_seq": file_seq,
                    "attachment_api_url": api_url,
                    "view_count": row.get("viewCnt"),
                },
                "document_id": _document_id(source_id, download_url),
            }
        )
    return documents


def parse_agris_payload(payload: Mapping[str, object], page_url: str) -> list[dict]:
    rows = payload.get("new_ReportList")
    if not isinstance(rows, list):
        raise CollectionError("AGRIS response lacks new_ReportList")
    documents: list[dict] = []
    endpoint = "https://www.agris.go.kr/egov/com/nomFileDown.do"
    for row in rows:
        if not isinstance(row, Mapping):
            raise CollectionError("AGRIS report row is not an object")
        files = row.get("fileList") or []
        if not isinstance(files, list):
            raise CollectionError("AGRIS fileList is not a list")
        for file_row in files:
            if not isinstance(file_row, Mapping):
                raise CollectionError("AGRIS attachment is not an object")
            filename = safe_filename(str(file_row.get("orignlFileNm") or ""))
            if Path(filename).suffix.casefold() != ".pdf":
                continue
            file_id = str(file_row.get("atchFileId") or "").strip()
            file_path = str(file_row.get("fileStreCours") or "").strip()
            if not file_id or not file_path:
                raise CollectionError("AGRIS attachment lacks file id/path")
            try:
                declared_size = int(file_row.get("fileSize") or 0) or None
            except (TypeError, ValueError) as exc:
                raise CollectionError("AGRIS attachment has invalid file size") from exc
            form = {
                "atchFileId": file_id,
                "fileSn": str(file_row.get("fileSn") or "0"),
                "atchFileNm": filename,
                "atchFilePath": file_path,
                "fileSize": str(file_row.get("fileSize") or "0"),
                "fileGb": str(file_row.get("fileGb") or "1"),
                "type": "normal",
                "gubun": str(file_row.get("gubun") or "fileId"),
            }
            report_seq = str(row.get("reportSeq") or "").strip()
            material = [report_seq, file_id, form["fileSn"]]
            documents.append(
                {
                    "source_id": "molit-aggregate-resource-surveys",
                    "title": str(row.get("reportNm") or Path(filename).stem).strip(),
                    "filename": filename,
                    "format": "PDF",
                    "declared_size_bytes": declared_size,
                    "landing_url": SOURCE_SPECS[
                        "molit-aggregate-resource-surveys"
                    ].landing_url,
                    "download_request": {
                        "url": endpoint,
                        "method": "POST",
                        "form": form,
                    },
                    "upstream": {
                        "report_seq": report_seq,
                        "survey_year": row.get("eyear"),
                        "province": row.get("sdNm"),
                        "municipality": row.get("sggNm"),
                        "publisher": row.get("pubOrg"),
                        "executing_org": row.get("execOrg"),
                        "listing_page_url": page_url,
                    },
                    "document_id": _document_id(
                        "molit-aggregate-resource-surveys", material
                    ),
                }
            )
    return documents


def _license_evidence(
    client: SafeHttpClient,
    run_dir: Path,
    source: Mapping[str, object],
    spec: SourceSpec,
) -> dict:
    payload = client.request_bytes(
        spec.license_url,
        allowed_hosts={"www.data.go.kr"},
        max_bytes=LICENSE_MAX_BYTES,
        headers={"Accept": "application/json"},
    )
    if _looks_like_html(payload.body):
        raise CollectionError("licence endpoint returned HTML")
    verified = verify_license_payload(payload.body, str(source["license"]))
    snapshot = _snapshot(
        run_dir,
        source_id=spec.source_id,
        kind="license",
        sequence=1,
        payload=payload,
        suffix=".json",
    )
    return {
        "license_code": source["license"],
        "evidence_scope": "catalog_dataset_only_not_item_permission",
        **verified,
        **snapshot,
    }


def _bind_evidence(document: dict, evidence: Mapping[str, object]) -> None:
    discovery = document.get("discovery_evidence")
    discovery = discovery if isinstance(discovery, Mapping) else {}
    binding_material = {
        "document_id": document["document_id"],
        "download_request": document["download_request"],
        "license_snapshot_sha256": evidence["snapshot_sha256"],
    }
    document["license_evidence"] = {
        "catalog_evidence": dict(evidence),
        "item_level_evidence": {
            "status": "missing",
            "snapshot_sha256": discovery.get("snapshot_sha256"),
            "snapshot_path": discovery.get("snapshot_path"),
            "snapshot_url": discovery.get("url"),
            "observed_license": None,
            "reason": (
                "official attachment record exposes no item-level KOGL or "
                "equivalent permission; catalog licence is not inherited"
            ),
        },
        "decision": "license_hold",
        "applies_to_document_id": document["document_id"],
        "document_binding_sha256": sha256_bytes(
            canonical_json_bytes(binding_material)
        ),
    }
    document["eligibility_status"] = "license_hold"
    document["download_authorized"] = False


def _discover_agris(
    client: SafeHttpClient,
    run_dir: Path,
    spec: SourceSpec,
    evidence: Mapping[str, object],
    max_items: int | None,
) -> tuple[list[dict], list[dict], dict]:
    landing = client.request_bytes(
        spec.landing_url,
        allowed_hosts=spec.discovery_hosts,
        max_bytes=DISCOVERY_MAX_BYTES,
        headers={"Accept": "text/html"},
    )
    snapshots = [
        _snapshot(
            run_dir,
            source_id=spec.source_id,
            kind="landing",
            sequence=1,
            payload=landing,
            suffix=".html",
        )
    ]
    endpoint = "https://www.agris.go.kr/main/info/report/doSearch.do"
    documents: list[dict] = []
    available_source_records: int | None = None
    snapshot_sequence = 1
    # The approved data.go.kr dataset describes terrestrial/river/forest
    # surveys.  AGRIS also exposes a separate marine toggle; do not silently
    # broaden the registered source into that collection.
    for type_flag in ("0",):
        page = 1
        page_total = 1
        while page <= page_total:
            request_body = canonical_json_bytes(
                {
                    "typeFlag": type_flag,
                    "sdNm": "",
                    "sggNm": "",
                    "eyear": "",
                    "reportNm": "",
                    "curPage": page,
                    "perPage": 10,
                    "groupId": -1,
                }
            ).rstrip(b"\n")
            response = client.request_bytes(
                endpoint,
                allowed_hosts=spec.discovery_hosts,
                max_bytes=DISCOVERY_MAX_BYTES,
                method="POST",
                data=request_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": spec.landing_url,
                },
            )
            if _looks_like_html(response.body):
                raise CollectionError("AGRIS listing API returned HTML")
            try:
                parsed = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CollectionError("AGRIS listing API returned invalid JSON") from exc
            if not isinstance(parsed, Mapping):
                raise CollectionError("AGRIS listing root is not an object")
            snapshot_sequence += 1
            page_snapshot = _snapshot(
                run_dir,
                source_id=spec.source_id,
                kind="discovery",
                sequence=snapshot_sequence,
                payload=response,
                suffix=".json",
            )
            snapshots.append(page_snapshot)
            pager = parsed.get("PagerVO")
            if not isinstance(pager, Mapping):
                raise CollectionError("AGRIS response lacks PagerVO")
            try:
                page_total = int(pager.get("pageTot") or 1)
                available_source_records = int(pager.get("totRows") or 0)
            except (TypeError, ValueError) as exc:
                raise CollectionError("AGRIS response has invalid pager values") from exc
            if page_total > 100:
                raise CollectionError(f"AGRIS page count is implausible: {page_total}")
            page_documents = parse_agris_payload(parsed, endpoint)
            for document in page_documents:
                document["discovery_evidence"] = page_snapshot
                _bind_evidence(document, evidence)
                documents.append(document)
                if max_items is not None and len(documents) >= max_items:
                    return documents, snapshots, {
                        "available_source_record_count": available_source_records,
                        "available_attachment_count": None,
                        "truncated_by_max_items": True,
                    }
            page += 1
    return documents, snapshots, {
        "available_source_record_count": available_source_records,
        "available_attachment_count": len(documents),
        "truncated_by_max_items": False,
    }


def _discover_kma(
    client: SafeHttpClient,
    run_dir: Path,
    spec: SourceSpec,
    evidence: Mapping[str, object],
    max_items: int | None,
) -> tuple[list[dict], list[dict], dict]:
    documents: list[dict] = []
    snapshots: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 21):
        separator = "&" if "?" in spec.landing_url else "?"
        page_url = f"{spec.landing_url}{separator}page={page}"
        response = client.request_bytes(
            page_url,
            allowed_hosts=spec.discovery_hosts,
            max_bytes=DISCOVERY_MAX_BYTES,
            headers={"Accept": "text/html"},
        )
        snapshot = _snapshot(
            run_dir,
            source_id=spec.source_id,
            kind="discovery",
            sequence=page,
            payload=response,
            suffix=".html",
        )
        snapshots.append(snapshot)
        page_documents = parse_kma_page(_decode(response), page_url)
        new_documents = []
        for document in page_documents:
            request_url = str(document["download_request"]["url"])
            if request_url in seen:
                continue
            seen.add(request_url)
            document["discovery_evidence"] = snapshot
            _bind_evidence(document, evidence)
            documents.append(document)
            new_documents.append(document)
            if max_items is not None and len(documents) >= max_items:
                return documents, snapshots, {
                    "available_source_record_count": None,
                    "available_attachment_count": None,
                    "truncated_by_max_items": True,
                }
        if not new_documents:
            break
    if not documents:
        raise CollectionError("KMA archive exposed no yearbook PDF links")
    return documents, snapshots, {
        "available_source_record_count": len(documents),
        "available_attachment_count": len(documents),
        "truncated_by_max_items": False,
    }


def _discover_gyeonggi(
    client: SafeHttpClient,
    run_dir: Path,
    spec: SourceSpec,
    evidence: Mapping[str, object],
    max_items: int | None,
) -> tuple[list[dict], list[dict], dict]:
    api_url = (
        "https://data.gg.go.kr/portal/data/file/searchFileData.do?"
        "infId=5XP8QWO5939KP255ST221203200&infSeq=1"
    )
    response = client.request_bytes(
        api_url,
        allowed_hosts=spec.discovery_hosts,
        max_bytes=DISCOVERY_MAX_BYTES,
        headers={
            "Accept": "application/json",
            "Referer": spec.landing_url,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    snapshot = _snapshot(
        run_dir,
        source_id=spec.source_id,
        kind="discovery",
        sequence=1,
        payload=response,
        suffix=".json",
    )
    if _looks_like_html(response.body):
        raise CollectionError("Gyeonggi attachment API returned a blocked HTML page")
    try:
        parsed = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError("Gyeonggi attachment API returned invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise CollectionError("Gyeonggi attachment API root is not an object")
    all_documents = parse_gyeonggi_payload(parsed, api_url)
    if not all_documents:
        raise CollectionError(
            "Gyeonggi official attachment API exposed no HWP documents"
        )
    documents = (
        all_documents if max_items is None else all_documents[:max_items]
    )
    for document in documents:
        document["discovery_evidence"] = snapshot
        _bind_evidence(document, evidence)
    return documents, [snapshot], {
        "available_source_record_count": len(all_documents),
        "available_attachment_count": len(all_documents),
        "truncated_by_max_items": len(documents) < len(all_documents),
    }


DISCOVERERS = {
    "molit-aggregate-resource-surveys": _discover_agris,
    "gyeonggi-disaster-safety-manuals": _discover_gyeonggi,
    "kma-weather-yearbooks": _discover_kma,
}


def _download_request_data(request: Mapping[str, object]) -> tuple[str, bytes | None, dict[str, str]]:
    method = str(request.get("method") or "GET").upper()
    if method == "GET":
        return method, None, {}
    if method != "POST":
        raise CollectionError(f"unsupported download method: {method}")
    form = request.get("form")
    if not isinstance(form, Mapping):
        raise CollectionError("POST download lacks a form object")
    values = {str(key): str(value) for key, value in form.items()}
    return (
        method,
        urllib.parse.urlencode(values).encode("utf-8"),
        {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
    )


def _validate_file_magic(suffix: str, prefix: bytes) -> None:
    if _looks_like_html(prefix):
        raise CollectionError("HTML error page rejected as a document")
    if suffix == ".pdf" and not prefix.startswith(b"%PDF-"):
        raise CollectionError("PDF magic mismatch")
    if suffix == ".hwp" and not (
        prefix.startswith(_HWP_OLE_MAGIC) or prefix.startswith(_HWP_V3_MAGIC)
    ):
        raise CollectionError("HWP magic mismatch")


def download_document(
    client: SafeHttpClient,
    run_dir: Path,
    document: Mapping[str, object],
    *,
    max_bytes: int,
) -> dict:
    source_id = str(document["source_id"])
    spec = SOURCE_SPECS[source_id]
    filename = safe_filename(str(document["filename"]))
    suffix = Path(filename).suffix.casefold()
    if suffix not in spec.suffixes:
        raise CollectionError(f"file suffix is not allowed for {source_id}: {suffix}")
    request = document.get("download_request")
    if not isinstance(request, Mapping):
        raise CollectionError("document lacks download_request")
    url = str(request.get("url") or "")
    validate_https_url(url, spec.download_hosts)
    method, body, headers = _download_request_data(request)

    declared_manifest = document.get("declared_size_bytes")
    if isinstance(declared_manifest, int) and declared_manifest > max_bytes:
        raise CollectionError(
            f"declared file exceeds cap: {declared_manifest}>{max_bytes}"
        )

    output_name = f"{document['document_id']}-{filename}"
    relative = Path("raw") / source_id / output_name
    target = run_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{secrets.token_hex(8)}.part"
    digest = hashlib.sha256()
    total = 0
    first = b""
    try:
        with client.open_stream(
            url,
            allowed_hosts=spec.download_hosts,
            method=method,
            data=body,
            headers=headers,
        ) as response:
            response_mime = _content_type(response.headers)
            if response_mime == "text/html":
                raise CollectionError("HTML MIME rejected as a document")
            if response_mime not in ALLOWED_MIME_BY_SUFFIX[suffix]:
                raise CollectionError(
                    f"MIME not allowed for {suffix}: {response_mime or '<missing>'}"
                )
            declared = _content_length(response.headers)
            if declared is not None and declared > max_bytes:
                raise CollectionError(f"response exceeds cap: {declared}>{max_bytes}")
            final_url = response.geturl()
            validate_https_url(final_url, spec.download_hosts)
            with tmp.open("xb") as handle:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise CollectionError(
                            f"download exceeds cap while streaming: >{max_bytes}"
                        )
                    if len(first) < 4096:
                        first += chunk[: 4096 - len(first)]
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        if total == 0:
            raise CollectionError("empty document rejected")
        _validate_file_magic(suffix, first)
        if target.exists():
            raise CollectionError(f"refusing to replace downloaded artifact: {target}")
        os.replace(tmp, target)
        return {
            "status": "downloaded",
            "path": relative.as_posix(),
            "size_bytes": total,
            "sha256": digest.hexdigest(),
            "response_mime": response_mime,
            "final_url": final_url,
        }
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def collect(
    *,
    registry_path: Path,
    output_root: Path,
    source_ids: Sequence[str],
    download: bool,
    max_bytes: int,
    max_items: int | None,
    timeout: float,
    run_id: str | None = None,
) -> tuple[Path, dict]:
    if max_bytes <= 0:
        raise CollectionError("max_bytes must be positive")
    if max_items is not None and max_items <= 0:
        raise CollectionError("max_items must be positive")
    registry = load_registry(registry_path)
    selected = select_registry_sources(registry, source_ids)
    run_dir = create_unique_run_dir(output_root, run_id=run_id)
    client = SafeHttpClient(timeout=timeout)
    started_at = utc_now()
    manifest: dict = {
        "schema_version": "1.0",
        "run_id": run_dir.name,
        "mode": "download" if download else "discover_only",
        "started_at": started_at,
        "registry_path": str(registry_path.resolve()),
        "registry_sha256": sha256_bytes(registry_path.read_bytes()),
        "collector_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "requested_source_ids": list(source_ids),
        "safety": {
            "https_only": True,
            "allowed_hosts": sorted(ALL_OFFICIAL_HOSTS),
            "max_download_bytes": max_bytes,
            "atomic_writes": True,
            "html_error_pages_rejected": True,
            "download_requires_explicit_flag": True,
        },
        "sources": [],
        "documents": [],
    }
    all_documents: list[dict] = []
    failures = 0
    for source in selected:
        source_id = str(source["source_id"])
        spec = SOURCE_SPECS[source_id]
        source_result: dict = {
            "source_id": source_id,
            "registry_status": source["status"],
            "registry_license": source["license"],
            "landing_url": spec.landing_url,
        }
        try:
            evidence = _license_evidence(client, run_dir, source, spec)
            source_result["catalog_license_evidence"] = evidence
            discoverer = DISCOVERERS[source_id]
            documents, discovery_snapshots, discovery_stats = discoverer(
                client, run_dir, spec, evidence, max_items
            )
            available_records = discovery_stats[
                "available_source_record_count"
            ]
            expected_records = int(source.get("expected_document_count") or 0)
            source_result.update(
                {
                    "status": "discovered_license_hold",
                    "document_count": len(documents),
                    "available_source_record_count": available_records,
                    "available_attachment_count": discovery_stats[
                        "available_attachment_count"
                    ],
                    "registry_expected_document_count": source.get(
                        "expected_document_count"
                    ),
                    "registry_count_comparison_basis": "source_records",
                    "discovery_truncated_by_max_items": discovery_stats[
                        "truncated_by_max_items"
                    ],
                    "discovered_minus_registry_expected": (
                        None
                        if available_records is None
                        else int(available_records) - expected_records
                    ),
                    "discovery_snapshots": discovery_snapshots,
                }
            )
            all_documents.extend(documents)
        except (CollectionError, OSError, urllib.error.URLError) as exc:
            failures += 1
            source_result.update(
                {
                    "status": "blocked",
                    "document_count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        manifest["sources"].append(source_result)

    download_failures = 0
    license_holds = 0
    for document in all_documents:
        if document.get("download_authorized") is not True:
            license_holds += 1
            document["download_result"] = {
                "status": "skipped_license_hold" if download else "not_requested",
                "reason": "item-level licence evidence is missing",
            }
        elif download:
            try:
                document["download_result"] = download_document(
                    client, run_dir, document, max_bytes=max_bytes
                )
            except (CollectionError, OSError, urllib.error.URLError) as exc:
                download_failures += 1
                document["download_result"] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        else:
            document["download_result"] = {"status": "not_requested"}
    manifest["documents"] = sorted(
        all_documents, key=lambda item: (item["source_id"], item["document_id"])
    )
    manifest["completed_at"] = utc_now()
    if failures or download_failures:
        manifest["status"] = "partial"
    elif license_holds:
        manifest["status"] = "license_hold"
    else:
        manifest["status"] = "complete"
    manifest["summary"] = {
        "source_count": len(selected),
        "source_failures": failures,
        "documents_discovered": len(all_documents),
        "documents_downloaded": sum(
            item["download_result"]["status"] == "downloaded"
            for item in all_documents
        ),
        "download_failures": download_failures,
        "documents_on_license_hold": license_holds,
    }
    _atomic_write(run_dir / "manifest.json", canonical_json_bytes(manifest))
    return run_dir, manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover approved official public proxy documents safely"
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=tuple(SOURCE_SPECS),
        help="registry source_id; repeatable (default: all three)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--discover-only",
        action="store_true",
        help="write evidence snapshots and manifest only (default)",
    )
    mode.add_argument(
        "--download",
        action="store_true",
        help="explicitly permit attachment downloads",
    )
    parser.add_argument("--max-mib", type=int, default=512)
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="per-source discovery/download cap for a small probe",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source_ids = args.sources or list(SOURCE_SPECS)
    try:
        run_dir, manifest = collect(
            registry_path=args.registry,
            output_root=args.output_root,
            source_ids=source_ids,
            download=bool(args.download),
            max_bytes=args.max_mib * 1024 * 1024,
            max_items=args.max_items,
            timeout=args.timeout,
            run_id=args.run_id,
        )
    except (CollectionError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    summary = manifest["summary"]
    print(
        f"[{manifest['status']}] {run_dir / 'manifest.json'} "
        f"discovered={summary['documents_discovered']} "
        f"downloaded={summary['documents_downloaded']} "
        f"source_failures={summary['source_failures']}"
    )
    for source in manifest["sources"]:
        if source["status"] == "blocked":
            print(
                f"[BLOCKED] {source['source_id']}: {source['error']}",
                file=sys.stderr,
            )
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
