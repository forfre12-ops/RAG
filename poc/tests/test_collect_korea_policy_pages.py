"""Safety, licence, extraction, and artifact tests for korea.kr collection."""

from __future__ import annotations

import hashlib
import io
import json
from email.message import Message
from pathlib import Path

import pytest

from scripts import collect_korea_policy_pages as collector


RICH_PARAGRAPHS = [
    "정부는 여름철 폭염이 장기화되는 상황에 대비해 관계기관 비상대응체계를 가동하고 지역별 취약계층 보호 현황을 매일 점검한다. 현장 담당자는 냉방설비 상태와 응급연락망을 확인하고 필요한 지원을 즉시 연계한다.",
    "이번 대책은 노인과 장애인, 옥외근로자 등 위험에 쉽게 노출되는 주민을 중심으로 구성됐다. 지방정부는 무더위쉼터 운영시간을 늘리고 방문 건강관리와 안부 확인 횟수를 단계적으로 확대한다.",
    "질병관리 기관은 온열질환 감시자료를 분석해 발생 추세와 지역별 위험도를 공개한다. 의료기관에서 접수한 사례는 개인정보를 제거한 뒤 통계로 집계하며 이상 징후가 나타나면 경보 수준을 조정한다.",
    "재난안전 부서는 전력과 통신, 교통 분야의 대응 상황도 함께 살핀다. 정전이나 시설 장애가 발생하면 복구 인력을 우선 배치하고 병원과 복지시설에 필요한 비상전원을 지원할 계획이다.",
    "농촌 지역에서는 낮 시간대 야외작업을 줄이고 마을방송으로 행동요령을 안내한다. 작업장 관리자는 충분한 물과 휴식공간을 확보하고 근로자의 건강 이상 여부를 작업 전후에 확인해야 한다.",
    "교육기관은 방학 중 돌봄교실과 급식시설의 실내온도를 점검한다. 학생들이 이동하는 시간에는 그늘이 있는 동선을 안내하고 체육활동은 기상 여건에 따라 실내 프로그램으로 전환한다.",
    "보건 당국은 응급환자 발생에 대비해 권역별 병상과 구급차 운용 정보를 공유한다. 신고가 접수되면 환자의 상태와 이동거리를 고려해 수용 가능한 의료기관을 신속하게 선정한다.",
    "사회복지 담당자는 고립 위험이 높은 가구를 별도 명단으로 관리하되 접근 권한과 보관기간을 제한한다. 확인 과정에서 긴급한 도움이 필요하다고 판단되면 생계와 주거, 의료 지원을 동시에 검토한다.",
    "관계부처 합동점검에서는 계획의 이행 여부뿐 아니라 주민이 실제로 서비스를 이용할 수 있는지도 확인한다. 현장의 불편 사항은 일일 상황회의에서 공유하고 다음 점검 항목에 반영한다.",
    "정부는 대책 종료 후 지원 실적과 피해 현황을 비교해 개선과제를 도출할 예정이다. 평가 결과는 다음 연도 재난 대응지침과 예산 편성에 활용하고 주요 통계와 조치 결과를 국민에게 공개한다.",
    "기상청은 관측 지점별 최고기온과 습도, 열대야 지속시간을 분석해 위험 정보를 제공한다. 예보의 불확실성이 큰 경우에는 단일 수치 대신 가능한 범위를 제시하고 최신 자료가 들어올 때마다 전망을 갱신한다.",
    "현장 대응반은 조치 과정에서 확인된 시설별 보완사항과 완료 시점을 기록한다. 긴급 보수가 끝난 뒤에도 정상 작동 여부를 다시 확인하고 반복되는 문제는 중장기 개선사업으로 전환해 관리한다.",
    "국민은 재난문자와 정부 누리집에서 지역별 행동요령과 쉼터 위치를 확인할 수 있다. 관계기관은 고령자도 쉽게 이해할 수 있도록 안내 문장을 간결하게 정비하고 다국어 자료와 수어 영상도 함께 제공한다.",
]
RICH_BODY = "\n\n".join(RICH_PARAGRAPHS)


def _license_block(code: str = "1") -> str:
    if code == "0":
        return """
        <div class="type">
          <img alt="공공누리 공공저작물 자유이용허락 0유형 자유이용">
          <strong>'텍스트'에 한하여 조건 없이 자유롭게 이용이 가능합니다.</strong>
        </div>
        """
    if code == "4":
        return """
        <div class="type">
          <img alt="공공누리 공공저작물 자유이용허락 4유형 출처표시 변경금지">
          <strong>'텍스트'에 한하여 공공누리 조건에 따라 비상업적으로 자유이용이 가능합니다.</strong>
        </div>
        """
    return """
    <div class="type">
      <img alt="공공누리 공공저작물 자유이용허락 1유형 출처표시">
      <strong>'텍스트'에 한하여 공공누리 출처표시의 조건에 따라 자유이용이 가능합니다.</strong>
    </div>
    """


def _detail_html(*, code: str = "0", body: str = RICH_BODY) -> bytes:
    paragraphs = "".join(f"<p>{part}</p>" for part in body.split("\n\n"))
    return f"""<!doctype html>
    <html><head>
      <meta property="og:title" content="폭염 대응 종합대책 발표">
      <script type="application/ld+json">{{"datePublished":"2026-08-07T10:00:00+09:00"}}</script>
    </head><body>
      <div class="article_body"><div class="view_cont" data-title="행정안전부">
        {paragraphs}
        <figure><img alt="현장 사진"><figcaption>삭제해야 할 사진 설명</figcaption></figure>
        <p class="photo_caption">삭제해야 할 별도 캡션</p>
        <p class="remark">이 자료는 전재한 보도자료입니다.</p>
      </div></div>
      <div class="law_copy_wrap">{_license_block(code)}</div>
    </body></html>""".encode()


def _listing_html(news_ids: list[str]) -> bytes:
    rows = "".join(
        f"""<li><a href="/briefing/pressReleaseView.do?newsId={news_id}">
        <span class="text"><strong>자료 {index}</strong>
        <span class="lead">{RICH_PARAGRAPHS[index % len(RICH_PARAGRAPHS)]}</span>
        </span></a></li>"""
        for index, news_id in enumerate(news_ids)
    )
    return f"""<!doctype html><html><body>
      <a href="/briefing/pressReleaseView.do?newsId=999999999">side bar</a>
      <div class="list_type"><ul>{rows}</ul></div>
      <div class="paging"><a onclick="pageLink(1513)">last</a></div>
    </body></html>""".encode()


def _payload(body: bytes, url: str) -> collector.HtmlPayload:
    return collector.HtmlPayload(
        body=body,
        final_url=url,
        mime="text/html",
        charset="utf-8",
        status=200,
    )


class _FakeClient:
    def __init__(self, listing: bytes, details: dict[str, bytes]) -> None:
        self.listing = listing
        self.details = details
        self.calls: list[str] = []

    def request_html(self, url: str, **_kwargs: object) -> collector.HtmlPayload:
        self.calls.append(url)
        parsed = collector.urllib.parse.urlsplit(url)
        if parsed.path.endswith("List.do"):
            return _payload(self.listing, url)
        news_id = collector.urllib.parse.parse_qs(parsed.query)["newsId"][0]
        return _payload(self.details[news_id], url)


class _FakeResponse(io.BytesIO):
    def __init__(
        self,
        body: bytes,
        *,
        url: str,
        content_type: str = "text/html; charset=UTF-8",
        content_length: int | None = None,
    ) -> None:
        super().__init__(body)
        self._url = url
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _FakeOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def open(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return self.response


def test_cli_defaults_to_five_page_discovery_and_download_is_explicit() -> None:
    args = collector.parse_args([])
    assert args.limit == 5
    assert not args.download
    assert not args.discover_only
    assert collector.parse_args(["--download"]).download
    with pytest.raises(SystemExit):
        collector.parse_args(["--discover-only", "--download"])
    with pytest.raises(SystemExit):
        collector.parse_args(["--limit", "301"])
    with pytest.raises(SystemExit):
        collector.parse_args(["--start-list-page", "0"])


def test_start_list_page_selects_a_disjoint_listing_window(tmp_path: Path) -> None:
    news_id = "156773766"
    args = collector.parse_args(
        [
            "--discover-only",
            "--start-list-page",
            "16",
            "--list-pages",
            "2",
            "--limit",
            "1",
            "--delay-seconds",
            "0",
            "--output-root",
            str(tmp_path),
            "--run-id",
            "window-test",
        ]
    )
    client = _FakeClient(_listing_html([news_id]), {news_id: _detail_html()})
    _run_dir, manifest = collector.collect(args, client=client)
    listing_calls = [url for url in client.calls if "List.do" in url]
    assert "pageIndex=16" in listing_calls[0]
    assert "pageIndex=17" in listing_calls[1]
    assert manifest["request"]["start_list_page"] == 16


@pytest.mark.parametrize(
    "url",
    [
        "http://www.korea.kr/news/policyNewsList.do",
        "https://admin.korea.kr/news/policyNewsList.do",
        "https://user:pass@www.korea.kr/news/policyNewsList.do",
        "https://www.korea.kr:444/news/policyNewsList.do",
    ],
)
def test_url_validation_rejects_non_allowlisted_targets(url: str) -> None:
    with pytest.raises(collector.CollectionError):
        collector.validate_https_url(url)


def test_http_client_checks_redirect_path_mime_html_magic_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.korea.kr/briefing/pressReleaseList.do?pageIndex=1"

    def install(response: _FakeResponse) -> None:
        monkeypatch.setattr(
            collector.urllib.request,
            "build_opener",
            lambda *_args: _FakeOpener(response),
        )

    install(
        _FakeResponse(
            b"<!doctype html><html></html>",
            url="https://www.korea.kr/unexpected.do",
        )
    )
    with pytest.raises(collector.CollectionError, match="redirect path"):
        collector.SafeHttpClient().request_html(
            url, max_bytes=1024, expected_paths={"/briefing/pressReleaseList.do"}
        )
    install(
        _FakeResponse(
            b"<!doctype html><html></html>",
            url=url,
            content_type="application/pdf",
        )
    )
    with pytest.raises(collector.CollectionError, match="MIME"):
        collector.SafeHttpClient().request_html(
            url, max_bytes=1024, expected_paths={"/briefing/pressReleaseList.do"}
        )

    install(_FakeResponse(b"not html", url=url))
    with pytest.raises(collector.CollectionError, match="not an HTML"):
        collector.SafeHttpClient().request_html(
            url, max_bytes=1024, expected_paths={"/briefing/pressReleaseList.do"}
        )

    install(_FakeResponse(b"<!doctype html>", url=url, content_length=2048))
    with pytest.raises(collector.CollectionError, match="byte cap"):
        collector.SafeHttpClient().request_html(
            url, max_bytes=1024, expected_paths={"/briefing/pressReleaseList.do"}
        )


def test_http_client_turns_transport_failure_into_collection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingOpener:
        def open(self, *_args: object, **_kwargs: object) -> object:
            raise collector.urllib.error.URLError("temporary disconnect")

    monkeypatch.setattr(
        collector.urllib.request,
        "build_opener",
        lambda *_args: FailingOpener(),
    )
    monkeypatch.setattr(collector.time, "sleep", lambda *_args: None)
    with pytest.raises(collector.CollectionError, match="transport failed"):
        collector.SafeHttpClient().request_html(
            "https://www.korea.kr/briefing/pressReleaseList.do?pageIndex=1",
            max_bytes=1024,
            expected_paths={"/briefing/pressReleaseList.do"},
        )


def test_listing_parser_uses_only_main_list_and_reports_inventory_size() -> None:
    news_ids = ["156773766", "156773765"]
    rows, last_page = collector.parse_listing(
        _listing_html(news_ids).decode(), collector.COLLECTION_KINDS["press_release"]
    )
    assert [row["news_id"] for row in rows] == news_ids
    assert last_page == 1513
    assert all(row["fetch_url"].startswith("https://m.korea.kr/") for row in rows)


def test_page_level_kogl_0_and_kogl_1_are_distinguished_for_training() -> None:
    zero = collector.extract_license_evidence(_detail_html(code="0"))
    one = collector.extract_license_evidence(_detail_html(code="1"))
    assert zero.code == "KOGL-0"
    assert zero.training_use_permitted
    assert zero.status == "training_eligible"
    assert one.code == "KOGL-1"
    assert one.training_use_permitted
    assert one.status == "training_eligible"
    assert one.evaluation_use_permitted
    assert "source attribution" in one.permission_basis
    assert one.sha256 == hashlib.sha256(one.exact_html.encode()).hexdigest()


def test_blocked_or_incomplete_item_licence_fails_closed() -> None:
    with pytest.raises(collector.CollectionError, match="licence rejected"):
        collector.extract_license_evidence(_detail_html(code="4"))
    missing_text_permission = """<!doctype html><div class="type">
      <img alt="공공누리 공공저작물 자유이용허락 1유형 출처표시"></div>""".encode()
    with pytest.raises(collector.CollectionError, match="licence rejected"):
        collector.extract_license_evidence(missing_text_permission)
    conflicting = _detail_html(code="0") + _license_block("4").encode()
    with pytest.raises(collector.CollectionError, match="blocked KOG-L"):
        collector.extract_license_evidence(conflicting)


def test_body_parser_excludes_images_captions_and_copyright_boilerplate() -> None:
    metadata = collector.extract_page_metadata(_detail_html().decode())
    assert metadata["title"] == "폭염 대응 종합대책 발표"
    assert metadata["source_agency"] == "행정안전부"
    assert metadata["body"] == RICH_BODY
    assert "현장 사진" not in metadata["body"]
    assert "삭제해야 할" not in metadata["body"]
    assert "전재한 보도자료" not in metadata["body"]


def test_policy_news_body_parser_reads_structural_agency_link() -> None:
    body = "정책 본문 " * 100
    policy_html = f"""<!doctype html><html><head>
      <meta property="og:title" content="정책뉴스 제목">
      <script type="application/ld+json">{{
        "datePublished": "2026-08-07T17:11:00+09:00"
      }}</script></head><body>
      <div class="article_head"><div class="info">
        <a class="gotosite" href="/news/ministryNewsList.do?repCode=A00012">
          보건복지부
        </a>
      </div></div>
      <div class="article_body"><div class="view_cont" itemprop="articleBody">
        <p>{body}</p>
      </div></div></body></html>"""

    metadata = collector.extract_page_metadata(policy_html)

    assert metadata["title"] == "정책뉴스 제목"
    assert metadata["source_agency"] == "보건복지부"
    assert metadata["published_at"] == "2026-08-07T17:11:00+09:00"
    assert metadata["body"].startswith("정책 본문")


def test_sections_are_exact_source_spans_within_required_bounds() -> None:
    body = "\n\n".join([RICH_BODY, RICH_BODY, RICH_BODY])
    sections = collector.section_text(body)
    assert len(sections) >= 2
    for section in sections:
        assert 1200 <= len(section["text"]) <= 3200
        assert body[section["body_start"] : section["body_end"]] == section["text"]


def test_kogl_one_is_training_and_evaluation_eligible_with_attribution() -> None:
    listing = {
        "news_id": "156773766",
        "collection_kind": "press_release",
        "source_reference": (
            "https://www.korea.kr/briefing/pressReleaseView.do?newsId=156773766"
        ),
    }
    payload = _payload(
        _detail_html(),
        "https://m.korea.kr/briefing/pressReleaseView.do?newsId=156773766",
    )
    metadata = collector.extract_page_metadata(payload.text)
    section = collector.section_text(metadata["body"])[0]
    zero = collector.make_proxy_record(
        listing=listing,
        metadata=metadata,
        section=section,
        section_index=1,
        payload=payload,
        licence=collector.extract_license_evidence(_detail_html(code="0")),
        retrieved_at="2026-08-08T00:00:00+00:00",
    )
    one = collector.make_proxy_record(
        listing=listing,
        metadata=metadata,
        section=section,
        section_index=1,
        payload=payload,
        licence=collector.extract_license_evidence(_detail_html(code="1")),
        retrieved_at="2026-08-08T00:00:00+00:00",
    )
    assert zero["proxy_candidate_validation"]["ok"]
    assert one["proxy_candidate_validation"]["ok"]
    assert one["proxy_evaluation_validation"]["ok"]
    assert one["proxy_training_validation"]["ok"]
    assert one["training_use_permitted"] is True
    assert one["license_status"] == "training_eligible"
    assert one["proxy_training_validation"]["errors"] == []
    assert one["evaluation_availability"] == "eligible"


def test_discovery_does_not_persist_raw_html_or_text(tmp_path: Path) -> None:
    news_id = "156773766"
    args = collector.parse_args(
        [
            "--limit",
            "1",
            "--delay-seconds",
            "0",
            "--output-root",
            str(tmp_path),
            "--run-id",
            "discover-test",
        ]
    )
    run_dir, manifest = collector.collect(
        args,
        client=_FakeClient(_listing_html([news_id]), {news_id: _detail_html()}),
    )
    assert manifest["mode"] == "discover_only"
    assert (run_dir / "manifest.json").is_file()
    assert not (run_dir / "raw_html").exists()
    assert not (run_dir / "text").exists()
    assert not (run_dir / "records.jsonl").exists()


def test_download_persists_hashed_raw_html_text_and_records_atomically(
    tmp_path: Path,
) -> None:
    news_id = "156773766"
    detail = _detail_html()
    args = collector.parse_args(
        [
            "--download",
            "--limit",
            "1",
            "--delay-seconds",
            "0",
            "--output-root",
            str(tmp_path),
            "--run-id",
            "download-test",
        ]
    )
    run_dir, manifest = collector.collect(
        args,
        client=_FakeClient(_listing_html([news_id]), {news_id: detail}),
    )
    raw_path = run_dir / "raw_html" / f"{news_id}.html"
    assert raw_path.read_bytes() == detail
    assert manifest["pages"][0]["raw_html_sha256"] == hashlib.sha256(detail).hexdigest()
    assert (run_dir / "text" / f"{news_id}.txt").read_text(
        encoding="utf-8"
    ).strip() == RICH_BODY
    records = [
        json.loads(line)
        for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records
    assert all(row["source_agency"] == "행정안전부" for row in records)
    assert not list(run_dir.rglob("*.part"))


def test_unique_run_and_atomic_output_refuse_replacement(tmp_path: Path) -> None:
    run_dir = collector.create_unique_run_dir(tmp_path, "fixed")
    with pytest.raises(collector.CollectionError, match="already exists"):
        collector.create_unique_run_dir(tmp_path, "fixed")
    target = run_dir / "manifest.json"
    collector.atomic_write(target, b"{}\n")
    with pytest.raises(collector.CollectionError, match="refusing to replace"):
        collector.atomic_write(target, b"different")
