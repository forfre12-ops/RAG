"""Create auditable v3 content revisions for the seven TS proxy candidates.

The originals remain untouched.  Each v3 revision adds the records a reviewer
would need to understand scope, evidence, exceptions, and release criteria.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
REV = DIR / "revisions"

CASES = {
    "GOLD-CAND-TS-MFG-017": {
        "focus": "파일럿 제조 조건과 변경 전후의 결함 분포", "evidence": "시편별 측정 원시값, 장비 교정 이력, 공정 투입 순서",
        "exception": "교체 시편 2개는 표면 오염 경보로 비교군에서 제외", "trigger": "같은 결함 위치가 두 로트에서 반복되거나 평균값은 정상인데 최대값이 한계에 접근하는 경우",
        "handoff": "공정개발 책임자와 품질보증 책임자가 원시 로그·결함 이미지·재시험 계획을 함께 확인",
    },
    "GOLD-CAND-TS-SEC-009": {
        "focus": "설계 저장소 접근 흔적과 비상 계정의 복구 경로", "evidence": "인증 로그, 권한 변경 이력, 서명키 접근 감사 기록",
        "exception": "보존 기간 밖의 과거 로그는 사실 미확인 범위로 남김", "trigger": "비상 계정에서 설계 저장소 또는 서명 경로에 재접근이 탐지되는 경우",
        "handoff": "제품보안과 개발플랫폼 책임자가 권한 회수·키 교체·월간 점검 결과를 공동 확인",
    },
    "GOLD-CAND-TS-TEC-042": {
        "focus": "공정 재현 시험의 조건 조합과 실패 위치", "evidence": "시험 의뢰서, 장비 조건 로그, 시편 사진, 측정 원시 데이터",
        "exception": "측정 중 장비 경고가 발생한 구간은 결론 계산에서 분리", "trigger": "재시험에서 최대 변형 위치가 바뀌거나 조건 조합별 산포가 기준보다 넓어지는 경우",
        "handoff": "신뢰성평가와 모듈설계가 시편 추적표와 재시험 조건표를 기준으로 다음 설계를 확정",
    },
    "GOLD-CAND-TS-ENG-053": {
        "focus": "적층 공정 이상 원인 가설과 긴급 통제의 효과", "evidence": "현장 회의록, 생산 이력, 공정 파라미터 추이, 검사 이미지",
        "exception": "야간 교대조의 수기 기록은 전산 로그와 대조 전까지 보조 증빙으로 취급", "trigger": "통제 후에도 같은 로트 조건에서 이상 신호가 재발하거나 가설별 반증 결과가 충돌하는 경우",
        "handoff": "제조기술·품질·설비 담당자가 통제 해제 전 반증 시험과 작업자 전달사항을 함께 서명",
    },
    "GOLD-CAND-TS-RND-061": {
        "focus": "전극 형성 조건의 순서 효과와 파일럿 결과", "evidence": "실험계획서, 시편 추적표, 장비 교정값, 저항·표면 신호 원시값",
        "exception": "시편 교체와 대기시간이 발생한 시험은 별도 식별자로 유지", "trigger": "추가 원료 배치에서 동일 경향이 유지되지 않거나 처리시간 보정 후 산포가 확대되는 경우",
        "handoff": "소재공정개발과 품질보증이 다음 실험의 유지 조건·변경 조건을 분리해 확인",
    },
    "GOLD-CAND-TS-SEC-064": {
        "focus": "핵심 설계 저장소와 빌드 서명 경로의 권한 분리", "evidence": "계정 목록, 그룹 권한 대조표, 서명 도구 감사 로그, 비상 계정 승인 기록",
        "exception": "90일 이전 일부 로그는 보존 정책상 직접 확인하지 못함", "trigger": "비상 계정의 권한 재발급, 서명 권한과 저장소 권한의 동시 보유, 미승인 역할 변경이 발생하는 경우",
        "handoff": "보안운영·개발플랫폼·릴리스 책임자가 월간 교차점검의 예외 목록을 재승인",
    },
    "GOLD-CAND-TS-QA-068": {
        "focus": "고밀도 모듈 열변형의 재현 조건과 접합면 관찰", "evidence": "시편 조립 이력, 온도 사이클 로그, 변형 측정값, 파괴 관찰 이미지",
        "exception": "시편 수가 설계안별 여섯 개라서 통계적 일반화 범위는 제한", "trigger": "냉각 속도 변경 후 실패 위치가 이동하거나 잔류 응력과 변형량의 방향이 일치하지 않는 경우",
        "handoff": "신뢰성평가와 모듈설계가 이미지 원본·시편 보관 위치·재현 시험계획을 연계",
    },
}


def _source(doc_id: str) -> Path:
    found = list(DIR.glob(f"{doc_id}_*.md"))
    if len(found) != 1:
        raise ValueError(f"expected one source for {doc_id}")
    return found[0]


def _annex(case: dict) -> str:
    return f"""

## 검토 범위와 증빙 연결 보완

이 문서의 판단 대상은 **{case['focus']}**이다. 본문에 적힌 결론은 특정 시점의 제한된 시험·조사 범위에서만 유효하며, 비슷한 이름의 다른 공정·계정·시편에 자동 적용하지 않는다. 검토자는 요약 수치만 보지 않고, 어떤 사실이 원시 기록으로 확인됐는지와 어떤 내용이 가설 또는 후속 확인 사항인지를 분리해 읽어야 한다.

| 증빙 묶음 | 확인 목적 | 검토 시 유의점 |
| --- | --- | --- |
| {case['evidence']} | 관찰값과 조치의 선후 관계 확인 | 원본 생성 시각과 문서번호가 일치하는지 대조 |
| 변경·승인 기록 | 누가 어떤 조건을 바꿨는지 확인 | 구두 전달은 단독 확정 근거로 사용하지 않음 |
| 재시험 또는 재점검 기록 | 같은 조건에서 경향이 유지되는지 확인 | 결과가 다르면 기존 결론을 덮어쓰지 않고 원인을 추가 기록 |

증빙은 하나의 표에 모아 보이더라도 서로 같은 신뢰도를 갖지 않는다. 시스템이 남긴 로그, 계측 장비의 원시값, 담당자의 수기 메모, 사후 회의 정리는 각각 확인 가능한 범위가 다르다. 따라서 회의에서 정리한 설명이 원시값보다 앞서는 식의 인용은 허용하지 않는다. 열람·복사·발췌 요청에는 문서번호와 업무 목적을 남기고, 원시 데이터의 별도 반출은 책임자의 승인 절차를 따른다.

## 예외 처리와 변경 이력

이번 검토에서 **{case['exception']}**. 이 예외는 결과를 보기 좋게 만들기 위해 삭제한 항목이 아니라, 비교 전제가 달라진 이유를 남긴 것이다. 제외된 기록도 삭제하지 않고 원본 식별자·제외 사유·판단자를 연결해 보존한다. 이후 같은 조건의 자료가 보강되면 해당 항목은 새 검토 회차에서 다시 비교할 수 있다.

조건, 권한, 장비 상태, 시편 취급 순서 중 하나라도 바뀌면 기존 수치를 그대로 재사용하지 않는다. 변경 담당자는 변경 전후 값뿐 아니라 변경 이유, 영향을 받을 수 있는 지표, 되돌림 조건을 기록한다. 특히 단기 통제는 영구 조치로 표시하지 않으며, 담당자·만료 시점·재확인 일자가 정해져야만 종료 판단을 할 수 있다.

## 판정 기준과 보류 조건

다음 경우에는 본 문서의 조건부 결론을 해제하지 않고 즉시 재검토로 전환한다. **{case['trigger']}**. 재검토는 단순히 같은 시험을 반복하는 것이 아니라, 유지할 조건과 새로 확인할 조건을 분리한 계획서를 먼저 작성하는 방식으로 한다. 관찰되지 않은 위험을 “문제가 없었다”로 바꾸지 않으며, 확인 범위 밖의 항목은 미확인으로 남긴다.

생산 확대, 외부 설명, 공급사·협력사와의 자료 교환은 위 보류 조건이 해소되기 전에는 이 문서의 수치나 도표를 근거로 진행하지 않는다. 외부 설명이 필요한 경우에는 공개 가능한 사실만 별도 문서로 재작성하며, 본문에 포함된 조합 조건·내부 대응 순서·취약 지점은 포함하지 않는다.

## 인계·보존·재현 확인

후속 작업의 인계 기준은 **{case['handoff']}**하는 것이다. 인계받는 담당자는 원시 자료의 위치, 현재 결론의 제한사항, 다음 확인이 필요한 조건을 동시에 확인한다. 문서 본문만 전달하고 근거 파일을 분리하면, 나중에 결론을 재현하거나 수정할 수 없게 된다.

보존본에는 원문, 추출·측정 원시 데이터, 변경 이력, 검토 의견, 재검토 결과를 같은 문서번호 아래 연결한다. 이후 사실관계가 바뀌면 기존 본문을 조용히 수정하지 않고, 새 개정본에서 변경 사유·변경 범위·기존 결론에 미치는 영향을 남긴다. 이 원칙은 합성 후보의 검수에도 동일하게 적용되며, 본 문서는 여전히 사람 검수 전의 Proxy Gold 후보라는 상태를 유지한다.

### 검토자 최종 확인

검토자는 결론 문장만 승인하지 않고, 문서번호가 같은 원시 기록을 실제로 열어 보았는지 확인한다. 확인하지 못한 근거가 있으면 승인으로 표시하지 않고 보류 사유에 적는다. 재검토가 끝난 뒤에도 이 기록은 삭제하지 않으며, 다음 개정본이 어떤 사실을 추가·수정했는지 추적할 수 있도록 보존한다.

확인 시에는 자료의 존재만 보지 않고, 해당 자료가 어떤 판단을 지지하는지와 반대로 해석될 가능성도 함께 적는다. 이 절차를 생략하면 단일 로그나 단일 측정값이 전체 결론을 대표하는 것처럼 보일 수 있다. 최종 상태가 보류인 경우에도 필요한 증빙과 다음 판단 시점을 남긴다.
"""


def main() -> int:
    REV.mkdir(exist_ok=True)
    prepared: list[tuple[str, Path, Path, dict, str]] = []
    for doc_id, case in CASES.items():
        meta_path = DIR / f"{doc_id}.metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        revised = (_source(doc_id).read_text(encoding="utf-8").rstrip() + _annex(case)).strip() + "\n"
        path = REV / f"{doc_id}.v3.md"
        if len(revised) < 4_000:
            raise ValueError(f"revised content still too short: {doc_id} {len(revised)}")
        existing = str(meta.get("content_revision_path") or "")
        expected = str(path.relative_to(DIR)).replace("\\", "/")
        if existing and existing != expected:
            raise FileExistsError(f"different revision already set: {doc_id}")
        # This generator owns only its matching v3 path.  A rerun may extend a
        # partially generated v3 document after a validation-rule change.
        prepared.append((doc_id, meta_path, path, meta, revised))

    for doc_id, meta_path, path, meta, revised in prepared:
        path.write_text(revised, encoding="utf-8")
        expected = str(path.relative_to(DIR)).replace("\\", "/")
        meta["content_revision"] = "v3"
        meta["content_revision_path"] = expected
        meta["content_revision_note"] = "TS evidence, exception, decision, and handoff expansion"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{doc_id}\t{len(revised)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
