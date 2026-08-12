# -*- coding: utf-8 -*-
"""누출 검사 게이트 — 학습 전에 잡아야 하는 두 가지를 잡는지 본다.

실측 배경: v4_3 학습셋이 길이만으로 등급 100% 적중이었고 등급별 고정 문단이 전건에
붙어 있었다. 모델은 내용 대신 그것을 외웠고 같은 생성기 평가셋에서 F1 0.99, 실문서에서
0.61(S1 미탐 64%)이었다. 두 신호 모두 사람이 문서를 읽어서는 안 보인다.
"""
import pytest

from koipa.dataset_leakage import (
    DatasetLeakageError,
    audit,
    check_or_raise,
    length_only_accuracy,
)


def _doc(grade: str, filler: str, chars: int) -> str:
    body = (filler + " ") * 400
    return body[:chars]


class TestLengthLeak:
    def test_길이가_등급마다_갈리면_적중률이_1에_가깝다(self):
        pairs = []
        for grade, base in (("TS", 3300), ("S1", 3500), ("S2", 3200), ("S3", 3000)):
            pairs += [(base + i, grade) for i in range(30)]
        assert length_only_accuracy(pairs) > 0.9

    def test_길이가_겹치면_무작위_수준이다(self):
        pairs = []
        for i in range(30):
            for grade in ("TS", "S1", "S2", "S3"):
                pairs.append((1000 + i * 40 + hash(grade) % 7, grade))
        assert length_only_accuracy(pairs) < 0.5

    def test_길이_누출이_크면_차단된다(self):
        docs = []
        for grade, size in (("TS", 3300), ("S1", 3500), ("S2", 3200), ("S3", 3000)):
            docs += [(grade, _doc(grade, f"본문 {grade} 서술 {i}", size + i)) for i in range(25)]
        with pytest.raises(DatasetLeakageError, match="길이만으로"):
            check_or_raise(docs, label="길이누출")


class TestGradeTell:
    def test_등급별_고정문장이_전건에_있으면_차단된다(self):
        tail = {
            "TS": "열람 범위는 지정 담당자로 제한하고 반출은 승인 기록을 남긴다.",
            "S1": "현장 조건과 예외 처리 순서는 외부 공개본에 포함되지 않는다.",
            "S2": "자료 접근은 부서와 협력 범위로 제한하고 공유 이력은 남긴다.",
            "S3": "접근 제한과 반출 승인 같은 관리 조건을 적용하지 않는다.",
        }
        docs = []
        for i in range(30):
            for grade in ("TS", "S1", "S2", "S3"):
                # 길이는 고르게 흩어 길이 누출과 분리한다
                body = f"사례 {i} 상황 서술입니다. " * (5 + (i + len(grade)) % 9)
                docs.append((grade, body + tail[grade]))
        with pytest.raises(DatasetLeakageError, match="등급 전용 문장"):
            check_or_raise(docs, label="문장누출")

    def test_고정문장이_없으면_통과한다(self):
        # 본문에 등급 단서를 넣지 않고 길이도 등급과 무관하게 흩는다
        docs = []
        for i in range(30):
            for k, grade in enumerate(("TS", "S1", "S2", "S3")):
                body = f"사례 {i * 4 + k} 의 사실관계를 정리한 기록입니다. " * (5 + (i * 7 + k * 3) % 13)
                docs.append((grade, body))
        report = check_or_raise(docs, label="정상")
        assert report["tell_coverage"] <= 0.10


class TestGradeTokenExposure:
    def test_검수후보는_본문_등급노출을_막는다(self):
        docs = [("S1", f"이 문서의 등급 제안 사유: S1 입니다. 사례 {i}. " * 6) for i in range(12)]
        docs += [("S3", f"공개 안내 자료 사례 {i} 서술. " * 9) for i in range(12)]
        with pytest.raises(DatasetLeakageError, match="등급 문자열"):
            check_or_raise(docs, label="검수후보", allow_grade_token=False)

    def test_학습셋은_기본적으로_등급언급을_허용한다(self):
        # 일부 문서만 등급을 언급하고, 길이·문구는 등급과 무관하게 흩는다.
        docs = []
        for i in range(30):
            for k, grade in enumerate(("TS", "S1", "S2", "S3")):
                body = f"사례 {i * 4 + k} 의 검토 기록입니다. " * (5 + (i * 7 + k * 3) % 13)
                if i % 5 == 0:  # 일부 본문이 등급 문자열을 자연스럽게 언급
                    body += "관련 규정에서 S1 기준을 함께 검토했습니다."
                docs.append((grade, body))
        report = check_or_raise(docs, label="학습셋")
        assert report["grade_token_exposed"] > 0
        with pytest.raises(DatasetLeakageError, match="등급 문자열"):
            check_or_raise(docs, label="검수후보", allow_grade_token=False)


class TestAudit:
    def test_문서가_없으면_빈_보고를_준다(self):
        assert audit([]) == {"documents": 0}

    def test_보고에_등급별_길이가_들어간다(self):
        docs = [(g, f"{g} 사례 서술 " * 20) for g in ("TS", "S1", "S2", "S3")] * 5
        report = audit(docs)
        assert report["documents"] == 20
        assert set(report["length_by_grade"]) == {"TS", "S1", "S2", "S3"}
        assert report["length_by_grade"]["TS"]["n"] == 5
