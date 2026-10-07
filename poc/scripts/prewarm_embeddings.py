"""B4 — 시드 v3 + S10 평가셋 임베딩 사전 캐시.

용도:
- EmbeddingCache 어덙터의 디스크 캐시에 자주 사용되는 텍스트의 임베딩을 미리 채움
- 폐쇄망 운영 시 초기 cold-start 비용 절감
- semantic 라벨러 (m3_labeling B2) 성능 안정화

사용:
  python scripts/prewarm_embeddings.py                       # 기본 (시드 v3 + S10 평가셋)
  python scripts/prewarm_embeddings.py --include-synth-5k    # + labeled_5k 본문도 임베딩
  python scripts/prewarm_embeddings.py --dry-run             # 어떤 텍스트가 캐시될지만 출력
  python scripts/prewarm_embeddings.py --cache-dir reports/perf/emb_cache

본 스크립트는 외부 API 호출 0 — 임베딩 provider는 settings.embedding_provider 기준.
hash provider면 결정론적 (실제 모델 캐시는 아니지만 EmbeddingCache 동작 검증).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Windows cp949 콘솔 UTF-8 자동 전환
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
POC_ROOT = HERE.parent
sys.path.insert(0, str(POC_ROOT / "src"))


def _collect_texts(include_synth: bool, synth_path: Path | None) -> list[str]:
    texts: list[str] = []

    # 1. 시드 키워드 v3 모두
    try:
        from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS  # noqa: PLC0415

        for k in KEYWORD_SEEDS:
            kw = k.get("keyword")
            if kw:
                texts.append(kw)
    except Exception as exc:  # noqa: BLE001
        print(f"  [seeds] 로드 실패: {exc}")

    # 2. S10 RAG 평가셋 본문 (시드 키워드 + 마커 합성)
    try:
        from koipa.perf.scenarios import _build_s10_eval_set  # noqa: PLC0415

        for content, _ in _build_s10_eval_set(n_per_grade=25):
            texts.append(content)
    except Exception as exc:  # noqa: BLE001
        print(f"  [S10] 평가셋 빌드 실패: {exc}")

    # 3. 선택 — labeled_5k 본문
    if include_synth and synth_path and synth_path.exists():
        for jsonl_name in ("train.jsonl", "val.jsonl", "test.jsonl"):
            p = synth_path / jsonl_name
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                text = row.get("text", "")
                if text:
                    # 텍스트 앞부분만 (긴 본문은 청크 단위로 따로 처리해야 정확)
                    texts.append(text[:1000])

    # 중복 제거 (순서 보존)
    seen: set[str] = set()
    uniq: list[str] = []
    for t in texts:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description="임베딩 사전 캐시 빌드")
    ap.add_argument("--include-synth-5k", action="store_true", help="datasets/labeled_5k 본문도 포함")
    ap.add_argument("--synth-path", default=str(POC_ROOT / "datasets" / "labeled_5k"))
    ap.add_argument("--cache-dir", default=str(POC_ROOT / "reports" / "perf" / "emb_cache"))
    ap.add_argument("--dry-run", action="store_true", help="텍스트 수만 출력하고 종료")
    args = ap.parse_args()

    print("=" * 70)
    print("KOIPA PSH — 임베딩 사전 캐시 빌드 (B4)")
    print("=" * 70)

    texts = _collect_texts(args.include_synth_5k, Path(args.synth_path))
    print(f"  수집 텍스트: {len(texts)} 건 (중복 제거)")
    if args.dry_run:
        print("  --dry-run: 임베딩 생성 생략")
        for i, t in enumerate(texts[:5]):
            print(f"    [{i}] {t[:60]}{'...' if len(t)>60 else ''}")
        if len(texts) > 5:
            print(f"    ... +{len(texts) - 5} 건")
        return 0

    # Provider 빌드
    try:
        from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
        from koipa.adapters.embedding.cache_layer import CachedEmbedding  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: embedding adapter import 실패: {exc}")
        return 1

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 기본 EmbeddingProvider (settings 기반, hash 또는 KURE-v1)
    base = build_embedder()
    provider_name = type(base).__name__
    print(f"  Provider: {provider_name}")
    cache = CachedEmbedding(underlying=base, disk_path=str(cache_dir / "emb_cache.json"))

    # 배치로 임베딩 생성
    t0 = time.perf_counter()
    try:
        result = cache.embed(texts)
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: 임베딩 생성 실패: {exc}")
        return 1
    elapsed = time.perf_counter() - t0

    vec_count = len(result.vectors) if hasattr(result, "vectors") else len(texts)
    print(f"  생성: {vec_count} 임베딩 / {elapsed:.2f}s")

    # 캐시 디스크 적재 (cache_layer에 자동 저장)
    if hasattr(cache, "flush_disk"):
        try:
            cache.flush_disk()
            print(f"  디스크 캐시 → {cache_dir / 'emb_cache.json'}")
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN: 디스크 캐시 flush 실패: {exc}")

    # 통계
    if hasattr(cache, "stats"):
        s = cache.stats()
        print(f"  Stats: {s}")

    print("=" * 70)
    print("완료. PSH 다음 실행 시 임베딩 hit율 향상 기대.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
