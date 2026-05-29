"""D3: docker-compose.yml의 nginx-mtls profile 정적 검증.

profile=mtls로 옵트인. 기본 `docker compose up`에서는 안 올라오고
`--profile mtls` 명시했을 때만 활성.
"""

from __future__ import annotations

from pathlib import Path

import yaml


COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _load():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_nginx_mtls_service_exists():
    doc = _load()
    services = doc.get("services", {})
    assert "nginx-mtls" in services, "nginx-mtls 서비스 정의 필요"


def test_nginx_mtls_is_opt_in_profile():
    """profile=mtls가 지정돼 기본 up에서 안 올라오게 강제."""
    svc = _load()["services"]["nginx-mtls"]
    profiles = svc.get("profiles", [])
    assert "mtls" in profiles, "mtls profile에 등록돼야 함 (기본 up 제외)"


def test_nginx_mtls_mounts_conf_and_certs():
    svc = _load()["services"]["nginx-mtls"]
    vols = svc.get("volumes", [])
    joined = " ".join(vols)
    assert "infra/mtls/nginx.mtls.conf" in joined
    assert "infra/mtls/certs" in joined
    assert "/etc/nginx/mtls" in joined


def test_default_up_excludes_mtls():
    """profile이 있는 서비스는 명시적으로 --profile 지정해야 올라옴.

    여기서는 정적 yaml만 검증 — profile 키가 들어가 있으면 기본 up에 제외됨.
    """
    doc = _load()
    services = doc["services"]
    default_services = [name for name, cfg in services.items()
                        if not cfg.get("profiles")]
    assert "nginx-mtls" not in default_services
    # 기존 핵심 서비스는 default에 있어야 함 (regression guard)
    for must in ("postgres", "elasticsearch", "redis", "minio", "api", "worker"):
        assert must in default_services, f"{must}는 기본 up에서 빠지면 안 됨"
