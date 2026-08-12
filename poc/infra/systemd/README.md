# Koipa DR 백업 스케줄링 (호스트 systemd / cron)

원문 스토리지 볼륨은 이전에 **백업이 전혀 없었고**, PG 백업 스크립트도 cron 에 배선돼 있지
않아 "누가 host cron 을 걸어야만" 도는 상태였다. 이 디렉터리는 그 배선을 turnkey 로 제공한다.

백업 진입점은 `scripts/backup_dr.py` 하나다 — 실행 중 모든 koipa 스택을 자동탐지해
PG 덤프 + 원문 스토리지 아카이브를 `backups/` 에 만들고, retention·JSON 리포트까지 처리한다.
**fail-closed**(하나라도 실패하면 non-zero)라서 systemd/cron 이 실패를 놓치지 않는다.

## 왜 Celery beat 가 아니라 호스트 스케줄러인가
백업은 `docker exec <postgres> pg_dump` / `docker exec <worker> tar` 로 **호스트의 docker
소켓**을 쓴다. 하드닝된 worker 컨테이너에는 docker 소켓이 없다(있으면 컨테이너 탈출급 권한
상승). 그래서 백업은 beat_schedule 이 아니라 **호스트 systemd 타이머(또는 cron)** 로 돈다.

## systemd (권장)
```bash
sudo cp infra/systemd/koipa-dr-backup.{service,timer} /etc/systemd/system/
# .service 의 WorkingDirectory 를 실제 poc 경로로 수정
sudo systemctl daemon-reload
sudo systemctl enable --now koipa-dr-backup.timer
systemctl list-timers koipa-dr-backup.timer   # 다음 실행 시각
journalctl -u koipa-dr-backup -n 50           # 실행/실패 로그
```

## cron (대안)
```cron
# crontab -e — 매일 02:00, 로그 append
0 2 * * * cd /opt/koipa/poc && /usr/bin/python3 scripts/backup_dr.py >> /var/log/koipa-dr-backup.log 2>&1
```

## 오프사이트(두 번째 매체) 사본
폐쇄망은 MinIO 를 안 쓴다. 별도 디스크/NAS 마운트로 사본을 두려면:
```bash
python3 scripts/backup_dr.py --second-media-dir /mnt/backup-nas
```
(systemd 는 `.service` 의 `ExecStart` 끝에 같은 인자를 붙인다.)

## 복구 / 검증
- 복구 리허설(RTO 4h 측정): `python3 scripts/dr_drill.py`
- 백업 신선도 점검: `python3 scripts/dr_restore_check.py --storage-dir backups/storage`
- 실복구: `python3 scripts/dr_restore.py --target postgres` / `--target storage`

⚠️ 원문 스토리지 아카이브는 **at-rest 암호문 그대로**다. 평문 복원하려면 `.env` 의
`STORAGE_ENCRYPTION_KEY` 를 백업과 **별도로** 안전 보관해야 한다(키가 없으면 복호 불가).
