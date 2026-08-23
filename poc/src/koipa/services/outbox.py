"""P1-E3: Webhook Outbox 패턴 — KL 콜백 신뢰성.

문제: KL 측 webhook 호출 실패 시 단순 retry 만으로는 transactional consistency 보장 어려움.

해결: outbox 테이블/큐에 send-pending 메시지 적재 → worker가 폴링·재시도·DLQ 이동.

백엔드:
- ``InMemoryOutboxStore`` — 단일 프로세스용. CI·dryrun·로컬 PoC 기본.
- ``RedisOutboxStore`` — 다중 워커 운영. ZSET(score=next_retry_at) + HASH(payload)
  + STREAM(DLQ) 조합. WATCH/MULTI/EXEC로 race-free dequeue.

팩토리:
- ``get_outbox_store()`` — env ``OUTBOX_BACKEND=redis|memory`` 우선, 미지정 시
  settings.redis_url 가용하면 redis, 아니면 in-memory. 연결 실패 시 in-memory 폴백.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# 클라우드 메타데이터 SSRF — 어느 배포에서도 정상 webhook 대상이 아니다. 항상 거부.
# 169.254.169.254 = AWS/Azure/GCP IMDS(v4, link-local이라 아래 범주 차단에도 걸림),
# fd00:ec2::254 = AWS IMDS(IPv6 ULA — is_private라 범주만으론 opt-in에 걸려 명시 denylist 필요).
# ipaddress 객체로 비교(문자열 표기 차이에 강건).
_METADATA_IPS = frozenset(
    {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}
)
# GCP는 IP 대신 호스트명으로도 IMDS 접근 가능 — 호스트명 리터럴도 차단.
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata"})


def _ip_block_reason(ip) -> Optional[str]:
    """해석된 IP가 webhook 대상으로 부적합하면 사유 문자열, 아니면 None (SSRF 판정 단일 소스).

    enqueue(리터럴 IP)·전송 시점(호스트명 해석 IP) 양쪽이 같은 규칙을 쓰게 한다. IPv4-mapped
    IPv6는 내장 v4로 정규화. 항상 거부: loopback·메타데이터·link-local·reserved·unspecified·
    multicast. 사설IP(RFC1918/4193)는 폐쇄망 정상 콜백이라 opt-in(outbox_block_private_ips)일 때만.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_loopback:
        return "loopback"
    if ip in _METADATA_IPS:
        return "cloud-metadata"
    if ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast:
        return "link-local/reserved/unspecified/multicast"
    block_private = False
    try:
        from koipa.config import settings  # noqa: PLC0415
        block_private = bool(getattr(settings, "outbox_block_private_ips", False))
    except Exception:  # noqa: BLE001
        block_private = False
    if block_private and ip.is_private:
        return "private (outbox_block_private_ips)"
    return None


def _validate_target_url(target_url: str) -> None:
    """[QW SSRF] webhook 대상 URL 가드 — 부적합 시 ValueError(enqueue 차단).

    항상 거부: http/https 외 스킴(file://·gopher://·data: 등 내부 파일·프로토콜 악용),
    호스트 없음, loopback(127.0.0.0/8·::1·localhost — 서비스 자기자신 타격), **클라우드 메타데이터/
    예약대역**(link-local 169.254/16·fe80::/10=IMDS·reserved·unspecified 0.0.0.0·multicast — 어느
    배포에서도 정상 콜백 아님, 자격증명 탈취 SSRF 표면). 사설IP(RFC1918/4193)만 폐쇄망 정상 콜백이라
    기본 허용하되 settings.outbox_block_private_ips=True면 거부(외부망 배포용). IPv4-mapped IPv6
    (::ffff:a.b.c.d)는 내장 v4로 정규화해 우회 차단. DNS 리바인딩은 전송 시점 워커 책임(enqueue 표면만).
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    if not target_url or not isinstance(target_url, str):
        raise ValueError("outbox target_url required")
    parsed = urlparse(target_url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"outbox target_url scheme not allowed: {parsed.scheme!r} (http/https only)")
    host = parsed.hostname
    if not host:
        raise ValueError("outbox target_url has no host")
    host_l = host.lower()
    if host_l in ("localhost", "ip6-localhost"):
        raise ValueError("outbox target_url loopback host not allowed")
    if host_l in _METADATA_HOSTS:
        raise ValueError("outbox target_url cloud-metadata host not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # 호스트명(IP 아님) — 전송 시점에 _assert_send_target_safe가 해석 IP 재검사
    if ip is not None:
        reason = _ip_block_reason(ip)
        if reason:
            raise ValueError(f"outbox target_url {reason} address not allowed (SSRF)")


@dataclass
class OutboxMessage:
    id: str
    target_url: str
    payload: dict
    method: str = "POST"
    headers: dict = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 5
    created_at: float = field(default_factory=time.time)
    last_error: Optional[str] = None
    next_retry_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "OutboxMessage":
        d = json.loads(raw)
        return cls(**d)


class OutboxStore:
    """Outbox 추상. 구현체: InMemory / Redis."""

    name = "base"

    def enqueue(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        raise NotImplementedError

    def update(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError


class InMemoryOutboxStore(OutboxStore):
    name = "memory"

    # in-flight visibility timeout — dequeue된 메시지는 이 시간 동안 보이지 않음
    # (Redis 백엔드의 invisible_until=now+600 과 동일). 워커 크래시 대비 자동 재진입.
    VISIBILITY_TIMEOUT_SEC = 600

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, OutboxMessage] = {}
        self._dlq: dict[str, OutboxMessage] = {}

    def enqueue(self, msg: OutboxMessage) -> None:
        with self._lock:
            self._items[msg.id] = msg

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        """ready 메시지의 **스냅샷(deepcopy)** 을 반환하며 in-flight 표시한다.

        M-outbox-inflight: Redis 백엔드와 동일하게, 반환 직전 락 아래에서
        ``next_retry_at = now + VISIBILITY_TIMEOUT_SEC`` 로 bump 해 두 번째
        워커가 같은 메시지를 즉시 다시 집지 못하게 한다(중복 webhook 차단).

        #35 (attempts 락 밖 변형 + visibility timeout 중 중복전송 차단):
        예전 구현은 store 가 보관한 *동일 객체 참조* 를 반환했다. 그러면
        deliver_once 가 락 밖에서 ``msg.attempts += 1`` / ``msg.next_retry_at=...``
        를 직접 변형할 때, visibility timeout 후 같은 객체를 다시 집은 다른
        워커와 attempts 가 경쟁하고(데이터 레이스), 성공 제거 직전 두 번째
        deliver 가 끼어들 수 있었다. 이를 막기 위해:
          1) 보관 객체는 락 안에서만 변형하고(아래 in-flight bump),
          2) 호출부에는 **deepcopy 스냅샷** 만 넘긴다. 호출부가 스냅샷을 락 밖에서
             아무리 변형해도 store 내부 상태와 경쟁하지 않는다. 실제 상태 반영은
             update()/move_to_dlq() 가 락 안에서 원자적으로 수행한다.
        """
        now = time.time()
        invisible_until = now + self.VISIBILITY_TIMEOUT_SEC
        snapshots: list[OutboxMessage] = []
        with self._lock:
            ready = [m for m in self._items.values() if m.next_retry_at <= now]
            ready.sort(key=lambda m: m.next_retry_at)
            picked = ready[:limit]
            for m in picked:
                # in-flight 표시 — 보관 객체에만 적용(락 안). visibility window 동안
                # 두 번째 dequeue 가 같은 메시지를 못 집는다.
                m.next_retry_at = invisible_until
                # 호출부에는 분리된 deepcopy 스냅샷을 전달(#35: 락 밖 변형 격리).
                snapshots.append(copy.deepcopy(m))
        return snapshots

    def update(self, msg: OutboxMessage) -> None:
        """deliver_once 가 만든 스냅샷의 새 상태를 락 안에서 원자적으로 반영.

        #35: attempts 증가/next_retry_at 갱신/성공 제거를 store 가 락 안에서
        한 번에 처리한다. 호출부(deliver_once)는 락 밖에서 스냅샷을 변형한 뒤
        이 원자 메서드만 호출한다. 메시지가 이미 제거(성공/DLQ)됐으면 무시해
        visibility window 내 중복 deliver 가 죽은 메시지를 되살리지 못하게 한다.
        """
        with self._lock:
            stored = self._items.get(msg.id)
            if stored is None:
                # 이미 성공 제거/ DLQ 이동된 메시지 — 스테일 업데이트 무시.
                return
            if msg.next_retry_at == float("inf"):
                # 성공 처리 → 즉시 제거(락 안 원자).
                self._items.pop(msg.id, None)
            else:
                merged = copy.deepcopy(msg)
                # #35: attempts 는 store 가 권위(authoritative)·단조 증가. 스테일
                # 스냅샷이 락 밖에서 들고 온 낮은 attempts 로 카운터를 되돌리지
                # 못하게 한다(visibility window 중 재진입/스테일 update 방어).
                merged.attempts = max(stored.attempts, msg.attempts)
                self._items[msg.id] = merged

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        with self._lock:
            # #35: 이미 제거된 메시지면 중복 DLQ 적재 방지(원자 처리).
            if msg.id not in self._items and msg.id in self._dlq:
                return
            self._items.pop(msg.id, None)
            self._dlq[msg.id] = copy.deepcopy(msg)

    def stats(self) -> dict:
        with self._lock:
            return {"backend": "memory", "pending": len(self._items), "dlq": len(self._dlq)}


# ---------------------------------------------------------------------------
# Redis 백엔드
# ---------------------------------------------------------------------------

_OUTBOX_PENDING_ZSET = "koipa:outbox:pending"     # ZSET: msg_id → score=next_retry_at
_OUTBOX_PAYLOAD_HASH = "koipa:outbox:payload"     # HASH: msg_id → JSON
_OUTBOX_DLQ_STREAM = "koipa:outbox:dlq"           # STREAM: 영구 보존
_OUTBOX_MSG_TTL_SEC = 7 * 24 * 60 * 60             # payload 보존 7일 (재시도 윈도우 + 디버깅)


class RedisOutboxStore(OutboxStore):
    """Redis 기반 — ZSET(스케줄) + HASH(payload) + STREAM(DLQ).

    Why ZSET: score=next_retry_at로 ready 메시지를 O(log N) 조회.
    Why HASH: 본문은 HASH에 분리 보관 → ZSET은 가벼움 유지.
    Why STREAM(DLQ): 영구 audit trail + consumer group으로 운영자 처리 가능.

    동시성: dequeue_ready는 WATCH + ZRANGEBYSCORE + ZREM으로 같은 메시지를
    두 워커가 동시에 잡지 못하게 보장. 실패하면 다른 워커가 재시도 윈도우에서 다시 잡음.
    """

    name = "redis"

    def __init__(self, redis_url: str, *, msg_ttl_seconds: int = _OUTBOX_MSG_TTL_SEC):
        import redis  # noqa: PLC0415

        self._redis_module = redis
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client.ping()
        self._ttl = msg_ttl_seconds
        self._url = redis_url
        logger.info("RedisOutboxStore connected: url=%s ttl=%ds", redis_url, msg_ttl_seconds)

    def enqueue(self, msg: OutboxMessage) -> None:
        with self._client.pipeline() as pipe:
            pipe.hset(_OUTBOX_PAYLOAD_HASH, msg.id, msg.to_json())
            pipe.zadd(_OUTBOX_PENDING_ZSET, {msg.id: msg.next_retry_at or time.time()})
            pipe.execute()

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        """ZRANGEBYSCORE로 ready 후보 → WATCH + 개별 ZREM으로 락 획득.

        획득한 메시지는 dequeue 후 즉시 score를 매우 큰 값(처리 중)으로 갱신해
        다른 워커가 중복 처리 못 하게 한다. 호출자가 update()로 next_retry_at을
        다시 정상값으로 되돌리거나 move_to_dlq로 옮긴다.
        """
        now = time.time()
        ids = self._client.zrangebyscore(
            _OUTBOX_PENDING_ZSET, min=0, max=now, start=0, num=limit,
        )
        out: list[OutboxMessage] = []
        # in-flight 표시 (10분 후 자동 재진입) — 워커 크래시 대비 visibility timeout
        invisible_until = now + 600
        for mid in ids:
            with self._client.pipeline() as pipe:
                try:
                    pipe.watch(_OUTBOX_PENDING_ZSET)
                    score = pipe.zscore(_OUTBOX_PENDING_ZSET, mid)
                    if score is None or score > now:
                        pipe.unwatch()
                        continue
                    raw = pipe.hget(_OUTBOX_PAYLOAD_HASH, mid)
                    if raw is None:
                        # payload 사라짐 — zset 정리
                        pipe.multi()
                        pipe.zrem(_OUTBOX_PENDING_ZSET, mid)
                        pipe.execute()
                        continue
                    pipe.multi()
                    pipe.zadd(_OUTBOX_PENDING_ZSET, {mid: invisible_until})
                    pipe.execute()
                    out.append(OutboxMessage.from_json(raw))
                except self._redis_module.WatchError:
                    continue
        return out

    def update(self, msg: OutboxMessage) -> None:
        if msg.next_retry_at == float("inf"):
            # 성공 → 영구 제거
            with self._client.pipeline() as pipe:
                pipe.hdel(_OUTBOX_PAYLOAD_HASH, msg.id)
                pipe.zrem(_OUTBOX_PENDING_ZSET, msg.id)
                pipe.execute()
            return
        with self._client.pipeline() as pipe:
            pipe.hset(_OUTBOX_PAYLOAD_HASH, msg.id, msg.to_json())
            pipe.zadd(_OUTBOX_PENDING_ZSET, {msg.id: msg.next_retry_at})
            pipe.execute()

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        """STREAM에 append + pending에서 제거.

        STREAM 보존 정책은 운영자 결정 (XSETID/MAXLEN). 본 함수는 unbounded append.
        """
        entry = {
            "msg_id": msg.id,
            "target_url": msg.target_url,
            "attempts": str(msg.attempts),
            "last_error": msg.last_error or "",
            "payload_json": json.dumps(msg.payload, default=str, ensure_ascii=False),
        }
        with self._client.pipeline() as pipe:
            pipe.xadd(_OUTBOX_DLQ_STREAM, entry)
            pipe.hdel(_OUTBOX_PAYLOAD_HASH, msg.id)
            pipe.zrem(_OUTBOX_PENDING_ZSET, msg.id)
            pipe.execute()

    def stats(self) -> dict:
        pending = self._client.zcard(_OUTBOX_PENDING_ZSET)
        try:
            dlq = self._client.xlen(_OUTBOX_DLQ_STREAM)
        except self._redis_module.ResponseError:
            # stream 미생성 시
            dlq = 0
        return {"backend": "redis", "pending": int(pending), "dlq": int(dlq)}


# ---------------------------------------------------------------------------
# 비즈니스 API
# ---------------------------------------------------------------------------


def publish(
    store: OutboxStore,
    *,
    target_url: str,
    payload: dict,
    headers: Optional[dict] = None,
    max_attempts: int = 5,
) -> OutboxMessage:
    _validate_target_url(target_url)  # [QW SSRF] 부적합 대상은 enqueue 전 차단
    msg = OutboxMessage(
        id=str(uuid.uuid4()),
        target_url=target_url,
        payload=payload,
        headers=headers or {},
        max_attempts=max_attempts,
        next_retry_at=time.time(),
    )
    store.enqueue(msg)
    return msg


def _backoff_seconds(attempts: int) -> float:
    return min(2 ** attempts, 600)


def deliver_once(
    store: OutboxStore,
    *,
    http_send: Callable[[str, str, dict, dict], int],
    limit: int = 10,
) -> dict:
    """ready 메시지 한 batch 전송. Worker(또는 Celery beat)가 주기 호출.

    Args:
        store: OutboxStore 구현체
        http_send: callable(url, method, headers, payload) -> http_status_code
        limit: 1회 처리 최대 건수

    Returns:
        {"sent": n, "failed": n, "dlq": n, "ready": n_dequeued}
    """
    ready = store.dequeue_ready(limit=limit)
    sent = failed = dlq = 0
    for msg in ready:
        msg.attempts += 1
        success = False
        try:
            status = http_send(msg.target_url, msg.method, msg.headers, msg.payload)
            if 200 <= status < 300:
                success = True
            else:
                msg.last_error = f"http_{status}"
        except Exception as e:  # noqa: BLE001
            msg.last_error = f"{type(e).__name__}:{e}"[:200]

        if success:
            msg.last_error = None
            msg.next_retry_at = float("inf")
            store.update(msg)
            sent += 1
            continue

        if msg.attempts >= msg.max_attempts:
            logger.warning(
                "outbox DLQ: id=%s url=%s attempts=%d err=%s",
                msg.id, msg.target_url, msg.attempts, msg.last_error,
            )
            store.move_to_dlq(msg)
            dlq += 1
            # NEW-H2: OutboxDlqGrowing 알람용 메트릭. best-effort(프로메테우스 미가용 무시).
            try:
                from koipa.api.prom_metrics import OUTBOX_DLQ_TOTAL  # noqa: PLC0415

                OUTBOX_DLQ_TOTAL.inc()
            except Exception:  # noqa: BLE001
                pass
        else:
            msg.next_retry_at = time.time() + _backoff_seconds(msg.attempts)
            store.update(msg)
            failed += 1
    return {"sent": sent, "failed": failed, "dlq": dlq, "ready": len(ready)}


def _assert_send_target_safe(url: str) -> None:
    """[SSRF/DNS-rebinding] 전송 직전 대상 URL을 재검증 — enqueue↔전송 시간차 우회 차단.

    enqueue 가드(_validate_target_url)는 최초 문자열만 봤다: 호스트명이면 IP를 몰라 통과했고,
    그 사이 DNS가 내부/메타데이터 IP로 리바인딩되면 우회됐다. 전송 직전 호스트명을 실제 해석해
    **모든 결과 IP**를 _ip_block_reason으로 검사한다(하나라도 차단대역이면 거부). 리터럴 IP·스킴은
    _validate_target_url이 재확인. 해석 실패는 거부(안전). 해석↔httpx 연결 사이 잔여 TOCTOU는
    폐쇄망 위협모델상 수용(진짜 IP-pinning은 커스텀 transport 필요 — 별도).
    """
    import socket  # noqa: PLC0415
    from urllib.parse import urlparse  # noqa: PLC0415

    _validate_target_url(url)  # 스킴/호스트/리터럴IP·메타호스트 재확인
    host = urlparse(url.strip()).hostname
    if not host:
        raise ValueError("outbox send target has no host")
    try:
        ipaddress.ip_address(host)
        return  # 리터럴 IP — _validate_target_url이 이미 대역 검사함
    except ValueError:
        pass  # 호스트명 — 아래에서 해석해 IP 검사
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError(f"outbox send target DNS resolve failed: {host} ({exc})") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        reason = _ip_block_reason(ip)
        if reason:
            raise ValueError(
                f"outbox send target {host} resolves to blocked {reason} address {addr} "
                f"(DNS-rebinding SSRF)"
            )


def http_send_via_httpx(url: str, method: str, headers: dict, payload: dict) -> int:
    """기본 송신자 — httpx. 전송 시점 SSRF 재검증(DNS 리바인딩)+리다이렉트/프록시 우회 차단."""
    import httpx  # type: ignore

    _assert_send_target_safe(url)  # DNS 리바인딩 가드 — 해석 IP 재검사(enqueue 이후 변조 차단)
    # follow_redirects=False: 30x로 내부/메타데이터로 재유도되는 우회 차단(명시 고정, 기본에 의존 안 함).
    # trust_env=False: HTTP(S)_PROXY 등 환경 프록시를 통한 SSRF 우회 차단(웹훅은 대상에 직접 송신).
    with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False) as client:
        resp = client.request(method, url, headers=headers, json=payload)
        return resp.status_code


# ---------------------------------------------------------------------------
# 팩토리
# ---------------------------------------------------------------------------


def _select_backend(redis_url: str | None, env_backend: str | None) -> str:
    if env_backend:
        choice = env_backend.strip().lower()
        if choice in ("redis", "memory"):
            return choice
        logger.warning("OUTBOX_BACKEND 알 수 없는 값=%s — memory 사용", env_backend)
        return "memory"
    if redis_url:
        return "redis"
    return "memory"


_default_store: OutboxStore | None = None
_default_lock = threading.Lock()


def publish_callback(callback_url: str | None, payload: dict) -> bool:
    """비동기 분류 완료/실패 결과를 callback_url 로 발사(outbox 적재 — 재시도·DLQ 신뢰전달).

    KL 콜백 계약의 단일 진입점 — in-process(AsyncClassifyService)·celery 워커(classify_async)
    양쪽이 이 함수로 동일 계약을 발사한다. best-effort: 적재 실패가 분류 결과(이미 job_store 에
    영속)를 폐기하지 않게 예외는 삼키고 로깅만. callback_url 이 없으면 no-op(False 반환).
    """
    if not callback_url:
        return False
    try:
        publish(get_outbox_store(), target_url=callback_url, payload=payload)
        logger.info("async callback enqueued to outbox: url=%s job=%s", callback_url, payload.get("job_id"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "async callback enqueue failed (non-critical): url=%s err=%s",
            callback_url, type(exc).__name__, exc_info=True,
        )
        return False


def get_outbox_store(
    redis_url: Optional[str] = None,
    *,
    env_backend: Optional[str] = None,
) -> OutboxStore:
    """Outbox 팩토리. Redis 연결 실패 시 InMemory 폴백."""
    global _default_store
    if _default_store is not None:
        return _default_store

    with _default_lock:
        if _default_store is not None:
            return _default_store

        if redis_url is None:
            try:
                from koipa.config import settings  # noqa: PLC0415
                redis_url = settings.redis_url
            except Exception as exc:  # noqa: BLE001
                logger.warning("outbox: settings 로드 실패 — memory err=%s", type(exc).__name__)
                _default_store = InMemoryOutboxStore()
                return _default_store

        if env_backend is None:
            env_backend = os.environ.get("OUTBOX_BACKEND")

        backend = _select_backend(redis_url, env_backend)
        if backend == "memory":
            logger.info("outbox backend: in-memory")
            _default_store = InMemoryOutboxStore()
            return _default_store

        try:
            _default_store = RedisOutboxStore(redis_url)
            return _default_store
        except ImportError as exc:
            logger.error("outbox redis 요청됐으나 redis 라이브러리 부재 — memory 폴백 err=%s", exc)
            _default_store = InMemoryOutboxStore()
            return _default_store
        except Exception as exc:  # noqa: BLE001
            logger.error("outbox redis 연결 실패 — memory 폴백 url=%s err=%s",
                         redis_url, type(exc).__name__)
            _default_store = InMemoryOutboxStore()
            return _default_store


def reset_default_store() -> None:
    """테스트용."""
    global _default_store
    with _default_lock:
        _default_store = None
