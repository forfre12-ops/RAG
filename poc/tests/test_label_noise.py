"""analyze_label_noise: 공개 판례인데 비-S3 라벨을 노이즈로 집계하는지 검증."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "analyze_label_noise", Path(__file__).resolve().parent.parent / "scripts" / "analyze_label_noise.py"
)
aln = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aln)


def test_ruling_detection_by_source_and_markers():
    assert aln._is_ruling({"source": "판례", "text": "x"})
    assert aln._is_ruling({"source": "기타", "text": "거절사정 【주 문】 상고를 기각한다. 대법원 선고"})
    assert not aln._is_ruling({"source": "public_scenario", "text": "거절사정 대법원 선고"})
    assert not aln._is_ruling({"source": "금융보고서", "text": "기준금리 인하 코멘트"})


def test_noise_counts_only_untrusted_non_s3_rulings():
    rows = [
        {"doc_id": "a", "source": "판례", "label": "S2", "label_source": "llm_judge_primary", "text": "판결"},
        {"doc_id": "b", "source": "판례", "label": "S1", "label_source": "llm_judge_consensus", "text": "판결"},
        {"doc_id": "c", "source": "판례", "label": "S3", "label_source": "llm_judge_primary", "text": "판결"},
        # 신뢰 출처(public_definitive)는 비-S3여도 노이즈에서 제외
        {"doc_id": "d", "source": "판례", "label": "S2", "label_source": "public_definitive", "text": "판결"},
        # 판례 아님 → 분석 대상 아님
        {"doc_id": "e", "source": "public_scenario", "label": "S2", "label_source": "llm_judge_primary", "text": "x"},
    ]
    res = aln.analyze(rows)
    assert res["ruling_scored"] == 3       # a, b, c (d는 신뢰출처 제외, e는 비판례)
    assert res["noise_count"] == 2         # a(S2), b(S1)
    assert res["noise_by_label"] == {"S2": 1, "S1": 1}
    assert abs(res["noise_rate"] - round(2 / 3, 4)) < 1e-9  # 스크립트가 4자리 반올림
