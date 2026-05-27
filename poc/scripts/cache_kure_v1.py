"""KURE-v1·BGE-M3 임베딩 모델 가중치를 로컬 캐시에 사전 다운로드.

용도:
- W6 직전 임베딩 풀 가동 준비 — HashEmbedding 폴백 없이 KURE-v1로 정확도 검증
- 폐쇄망 번들 빌드 — 가중치를 미리 받아 번들에 포함
- 일반화 납품 — 운영망에 인터넷 없어도 동작하도록 캐시 채워두기

사용:
  python scripts/cache_kure_v1.py                 # KURE-v1 + BGE-M3 둘 다 캐시
  python scripts/cache_kure_v1.py --models KURE   # KURE-v1만
  python scripts/cache_kure_v1.py --dry-run       # 다운로드 안 하고 캐시 위치만 출력

캐시 위치: ~/.cache/huggingface/hub/models--<org>--<model>
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("cache_kure_v1")

DEFAULT_MODELS = {
    "KURE": "nlpai-lab/KURE-v1",
    "BGE-M3": "BAAI/bge-m3",
}


def cache_model(model_id: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """sentence-transformers로 모델을 로드 (자동 캐시)."""
    if dry_run:
        cache_root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
        org, name = model_id.split("/", 1) if "/" in model_id else ("", model_id)
        target = cache_root / f"models--{org}--{name}".replace("/", "--")
        exists = target.exists()
        return True, f"dry-run: would cache to {target} (exists={exists})"

    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    except ImportError:
        return False, "sentence-transformers not installed (pip install sentence-transformers)"

    try:
        logger.info("loading %s — this may take a few minutes on first run...", model_id)
        model = SentenceTransformer(model_id)
        dim = model.get_sentence_embedding_dimension()
        # 한 번 embed 호출해 토크나이저까지 캐시
        _ = model.encode(["smoke test 한국어 가이드 본문"], normalize_embeddings=True)
        return True, f"cached OK — dim={dim}"
    except Exception as exc:  # noqa: BLE001
        return False, f"download failed: {type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Pre-cache HF embedding models for offline use")
    p.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS.keys()),
                   help=f"alias or full model_id. Default aliases: {list(DEFAULT_MODELS.keys())}")
    p.add_argument("--dry-run", action="store_true", help="확인만, 다운로드 안 함")
    args = p.parse_args(argv)

    failures = 0
    for spec in args.models:
        model_id = DEFAULT_MODELS.get(spec, spec)
        ok, detail = cache_model(model_id, dry_run=args.dry_run)
        status = "✓" if ok else "✗"
        logger.info("%s %s — %s", status, model_id, detail)
        if not ok:
            failures += 1

    if failures:
        logger.error("%d model(s) failed to cache", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
