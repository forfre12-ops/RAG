"""
P5 PoC — E2E 스모크 테스트.
샘플 텍스트로 /classify 호출 → 응답 검증.

사용:
  python scripts/p5_e2e_smoke.py --url http://localhost:8000 --api-key lloydk_dev_apikey
"""
import argparse
import json
import sys
import httpx


SAMPLE = (
    "[가상기업A 사업개발부 내부 검토]\n"
    "본 문서는 2026년 신규 사업 진출 전략에 대한 미공개 분석이며, "
    "주요 매출 추정, 마진 구조, 핵심 기술 차별화 요인을 포함한다. "
    "특허출원 전 자료이므로 외부 유출 시 막대한 손해배상 위험이 있다."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="lloydk_dev_apikey")
    args = ap.parse_args()

    payload = {
        "doc_id": "smoke-001",
        "tenant_id": "poc",
        "title": "신규 사업 전략 검토",
        "content": SAMPLE,
        "use_rag": False,
        "return_evidence": True,
    }

    with httpx.Client(timeout=60.0) as cli:
        r = cli.get(f"{args.url}/api/v1/healthz")
        r.raise_for_status()
        print("healthz:", r.json())

        r = cli.post(
            f"{args.url}/api/v1/classify",
            headers={"X-API-Key": args.api_key, "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code != 200:
        print("STATUS:", r.status_code, "BODY:", r.text)
        sys.exit(1)

    data = r.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["label"] in {"TS", "S1", "S2", "S3"}
    print(f"\n[OK] label={data['label']}, conf={data['confidence']:.3f}, "
          f"model={data['model_version']}")


if __name__ == "__main__":
    main()
