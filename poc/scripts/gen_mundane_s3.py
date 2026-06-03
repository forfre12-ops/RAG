"""비민감 일상/행정 사내문서 S3 코퍼스 생성 (doc/32 후속 — S3 갭 메우기).
모델의 S3가 '판례체 문체'에만 갇혀 일상문서를 과분류(80%)하는 문제를 고치기 위해,
'평범한 업무문서 = 비밀 아님(S3)'를 가르칠 학습데이터를 만든다.
생성: Ollama qwen3:14b, 동시요청 병렬(ThreadPool)로 GPU 포화 → 순차 대비 ~6-8배.
철칙 = 등급명 미선언 + 민감내용 0. label 전부 S3.
출력: datasets/mundane_s3/raw.jsonl  ({text,label,source,domain,doc_type})
주의: 합성이지만 '비민감 음성예시'라 secret 합성과 달리 위험 낮음. 테스트는 별도 손작성셋으로 분리(정직성)."""
from __future__ import annotations
import argparse, json, re, sys, urllib.request, time
import concurrent.futures as cf
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

CATS = ["총무·시설","인사·복지","IT·시스템 사용","사내행사·동호회","교육·온보딩",
        "일반 공지·안내","공개 채용공고","홍보·보도자료(공개)","일정·예약","사내 게시판"]
DOCTYPES = ["공지문","안내문","비민감 사내규정/지침","FAQ","사내 이메일","비민감 회의록","사용 매뉴얼/가이드","게시판 글"]
TOPICS = ["헌혈 행사","주차장 안내","구내식당 메뉴","동호회 모집","연차/반차 사용절차","워크숍 일정",
 "사내 교육 수강","그룹웨어 사용법","복지포인트 안내","경조사 지원 안내","사내 도서관","건강검진 일정",
 "출입증 발급","회의실 예약","택배·우편 수령","사무용품 신청","엘리베이터 점검","정기 소방훈련",
 "신입사원 환영","공개 채용 공고","신제품 출시 보도자료","사내 봉사활동","휴게실 이용","통근버스 노선"]

SYS = ("너는 한국 회사의 '평범한' 내부 운영 문서를 쓴다. 반드시 비밀이 아닌 일상적·행정적 문서만 만든다. "
 "절대 금지: '기밀','비밀','대외비','공개','1급/2급/3급','등급' 같은 단어, 그리고 가격·원가·마진·미공개 전략·"
 "핵심기술 수치·고객명단·소스코드 등 민감정보. 누구나 사내에서 자유롭게 봐도 되는 무난한 내용만 써라.")


def gen_one(idx, temp=0.95):
    cat = CATS[idx % len(CATS)]; dt = DOCTYPES[(idx // 2) % len(DOCTYPES)]; topic = TOPICS[idx % len(TOPICS)]
    user = (f"주제: {topic}\n분류: {cat}\n문서형식: {dt}\n위 조건으로 현실적인 사내 {dt} 한 건을 한국어로 작성해. "
            f'JSON 한 줄로만 출력: {{"title":"제목","body":"본문 200~600자"}}. 본문에 등급/기밀 단어 절대 금지.')
    payload = json.dumps({"model": "qwen3:14b",
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        "stream": False, "think": False, "options": {"temperature": temp}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            out = json.loads(r.read())["message"]["content"]
    except Exception:
        return None
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    title, body = (d.get("title") or "").strip(), (d.get("body") or "").strip()
    if len(body) < 80:
        return None
    if re.search(r"기밀|대외비|[1-3]\s*급|등급|비밀", title + body):  # 누설 가드
        return None
    return {"text": f"{title}\n\n{body}", "label": "S3", "source": "mundane_synth",
            "domain": cat, "doc_type": dt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--workers", type=int, default=8, help="동시 요청 수 (GPU 포화)")
    ap.add_argument("--out", default="datasets/mundane_s3/raw.jsonl")
    a = ap.parse_args()

    rows = []; t0 = time.time(); done = 0
    target_tasks = int(a.n * 1.5)  # 거부분 여유
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(gen_one, i) for i in range(target_tasks)]
        for fut in cf.as_completed(futs):
            done += 1
            r = fut.result()
            if r:
                rows.append(r)
                if len(rows) % 50 == 0:
                    print(f"  {len(rows)}/{a.n}  ({(time.time()-t0)/len(rows):.2f}s/건, 누적 {time.time()-t0:.0f}s)", file=sys.stderr)
            if len(rows) >= a.n:
                break
        for f in futs:
            f.cancel()

    rows = rows[:a.n]
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"생성 {len(rows)}건 ({time.time()-t0:.0f}s, 유효율 {len(rows)/max(1,done):.0%}) → {out}")
    for r in rows[:3]:
        print("  -", r["domain"], "/", r["doc_type"], ":", r["text"][:90].replace(chr(10), " "))


if __name__ == "__main__":
    raise SystemExit(main())
