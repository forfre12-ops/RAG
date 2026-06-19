"""Content 기반 라이브 데모 — :8010 (upload/MinIO 불필요).

classify(content)→confirm→relabel을 실 HTTP로 시연해 이번 세션 작업(FNR-override·escalation·
게이트·active-learning)이 실 API 경로에서 동작함을 실증. PG만 있으면 됨(저장).
"""
from __future__ import annotations

import io
import json
import sys
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

BASE = "http://localhost:8010"
H = {"X-API-Key": "devkey"}
T = "default"
GDESC = {"TS": "특급기밀", "S1": "1급비밀", "S2": "2급대외비", "S3": "공개"}

SAMPLES = [
    ("비밀(고등급 기대)",
     "마스터 키 롤링 절차: HSM에서 root CA 개인키를 90일마다 교체한다. "
     "EUV 노광 레시피와 DRAM 셀 공정 파라미터(sccm, Torr)는 사외 유출 절대 금지 — 특급 영업비밀."),
    ("공개(S3 기대)",
     "본 보도자료는 당사의 신제품 출시를 일반에 공지합니다. 자세한 내용은 홈페이지 공개 게시판을 참고하세요."),
]


def main() -> int:
    with httpx.Client(timeout=180.0) as cli:
        print("=" * 64)
        print("  LLOYDK 라이브 데모 — classify(content) → confirm → relabel")
        print("=" * 64)
        first = None
        for name, text in SAMPLES:
            did = str(uuid.uuid4())
            r = cli.post(f"{BASE}/api/v1/classify", headers=H,
                         json={"doc_id": did, "tenant_id": T, "use_rag": False, "content": text})
            if r.status_code >= 400:
                print(f"[{name}] 분류 실패 {r.status_code}: {r.text[:200]}")
                continue
            d = r.json()
            print(f"\n[{name}]")
            print(f"  • 등급      : {d['label']} ({GDESC.get(d['label'],'')})  conf={d['confidence']:.3f}  model={d['model_version']}")
            print(f"  • 점수      : " + "  ".join(f"{k}={v:.3f}" for k, v in (d.get('scores') or {}).items()))
            ef = d.get("evaluation_factors") or {}
            if ef:
                print(f"  • 3요건 SVM : S={ef.get('secrecy')} V={ef.get('value')} M={ef.get('management')}")
            ws = d.get("warnings") or []
            for w in ws:
                print(f"  • ⚑ warning : {w}")
            if d.get("status"):
                print(f"  • status    : {d['status']}")
            if first is None:
                first = (did, d)

        if first is None:
            print("\n[중단] 분류 성공 케이스 없음")
            return 1

        did, d = first
        label = d["label"]
        inf = d.get("inference_id")
        # ③ confirm
        print(f"\n[③ confirm] doc={did[:8]} label={label} 승인")
        r = cli.post(f"{BASE}/api/v1/confirm", headers=H,
                     json={"doc_id": did, "inference_id": inf, "confirmed_label": label,
                           "note": "검토자 확인",
                           "actor": {"user_id": "reviewer-1", "tenant_id": T, "role": "reviewer"}})
        print(f"  → {r.status_code}: {r.text[:200]}")
        # ④ relabel 상향
        higher = {"S3": "S2", "S2": "S1", "S1": "TS", "TS": "TS"}[label]
        print(f"\n[④ relabel] {label} → {higher} 상향(active-learning 적재)")
        r = cli.post(f"{BASE}/api/v1/relabel", headers=H,
                     json={"doc_id": did, "inference_id": inf, "original_label": label,
                           "corrected_label": higher, "reason": "검토자 판단 상향",
                           "actor": {"user_id": "reviewer-2", "tenant_id": T, "role": "reviewer"}})
        print(f"  → {r.status_code}: {r.text[:300]}")
        print("\n" + "=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
