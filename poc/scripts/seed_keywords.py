"""KEYWORD_SEEDS를 DB `tb_level_keywords` 테이블에 시드.

DB 연결 안 되면 JSON 파일로 dump (수동 적재용).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.modules.m3_labeling.seeds import (  # noqa: E402
    FACTOR_SEEDS,
    KEYWORD_SEEDS,
    to_canonical_factor,
)

OUT_JSON = _HERE.parent / "datasets" / "level_keywords_seed.json"


def to_db() -> int:
    try:
        from sqlalchemy import create_engine, text

        from koipa.config import settings
    except Exception as exc:  # noqa: BLE001
        print(f"[seed] cannot import db deps: {exc}", file=sys.stderr)
        return 1

    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.begin() as conn:
            # 실 스키마 이름은 db/models.py 의 __tablename__ 과 같아야 한다 — 무접두 이름을 쓰던
            # 시절 seed --db 가 UndefinedTable 로 조용히 exit 2 하던 잠복 버그가 있었다.
            # [2026-09-11] 표준 명명(7b3e9d2a4f10) — 대응표는 koipa/db/standard_names.py.
            level_map = dict(
                conn.execute(text("SELECT grd_cd, grd_sn FROM tad_cm_clsf_grd_mng")).all()
            )
            factor_map = dict(
                conn.execute(text("SELECT rqmt_cd, rqmt_sn FROM tad_em_evl_rqmt_mng")).all()
            )

            inserted = 0
            for seed in KEYWORD_SEEDS:
                lvl = level_map.get(seed["grade"])
                # 레거시 4요소 태그 → 정본 3요건(S·V·M)으로 정규화 후 DB factor_id 조회
                fct = factor_map.get(to_canonical_factor(seed["factor"]))
                if not lvl:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO tad_gm_grd_kywd_mng (grd_sn, kywd_nm, ptn_type_nm, rqmt_sn, wgvl_cfc, src_nm)
                        VALUES (:level_id, :keyword, :pattern_type, :factor_id, :weight, 'seed_v1')
                        """
                    ),
                    {
                        "level_id": lvl,
                        "keyword": seed["keyword"],
                        "pattern_type": seed.get("pattern_type", "exact"),
                        "factor_id": fct,
                        "weight": seed["weight"],
                    },
                )
                inserted += 1
        print(f"[seed] DB inserted {inserted} keywords")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[seed] DB error: {exc}", file=sys.stderr)
        return 2


def to_json() -> int:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"factors": FACTOR_SEEDS, "keywords": KEYWORD_SEEDS}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[seed] JSON written: {OUT_JSON} ({len(KEYWORD_SEEDS)} keywords)")
    return 0


def main() -> int:
    if "--db" in sys.argv:
        return to_db()
    return to_json()


if __name__ == "__main__":
    raise SystemExit(main())
