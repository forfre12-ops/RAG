"""'공개 의도가 명시/암시되지만 구체적 사업수치를 담은 문서' = S3 파일럿 생성.

golden100 S3 오분류 21건의 정확한 실패 패턴(제목에 공개 의도가 있어도 본문의 구체적
숫자·비율 때문에 S1/S2로 과대분류)을 겨냥한다. 철칙:
  - 회사명·업종·문서종류·공개신호 표현방식을 전부 다양화한다(템플릿 형제 방지).
  - "외부 공개용" 같은 한 문구를 반복 강제하지 않는다 — 자연스러운 공개 맥락도 섞는다.
  - 구체적 수치(매출·비율·가격·성능지표)는 반드시 포함한다(이게 회피할 특징이 아니라
    재현해야 할 특징이다 — 이 숫자가 있어도 S3로 배우게 하는 게 목적).
"""
import json
import re
import sys
import time
import urllib.request
import concurrent.futures as cf

sys.stdout.reconfigure(encoding="utf-8")

COMPANIES = ["가상기업A", "테크노바", "그린웨이브", "선진바이오", "메타포지",
             "한빛로보틱스", "블루오션물류", "스마트그리드코리아", "네오팜", "퓨처모빌리티"]
INDUSTRIES = ["제조", "IT/SaaS", "바이오헬스케어", "물류", "에너지", "로보틱스",
              "핀테크", "유통", "제약", "모빌리티"]
DOC_TYPES = ["제품 브로슈어", "웨비나 발표자료", "제품 카탈로그", "보도자료",
             "고객 자주묻는질문(FAQ)", "투자자·파트너 소개자료", "컨퍼런스 발표논문",
             "사회공헌 활동 보고서(공개본)", "홈페이지 게시용 서비스 소개서",
             "전시회 배포용 리플렛"]
# 공개신호를 매번 다른 방식으로 — 명시적 문구, 맥락적 암시, 아예 무언급(장르 자체로 공개 자명) 셋을 섞는다
PUBLIC_SIGNAL_STYLES = [
    "문서 말미에 '외부 배포·공개 가능' 문구를 자연스럽게 넣는다",
    "'전시회 부스에서 나눠준 자료', '홈페이지에서 누구나 다운로드 가능' 처럼 배포 경로로 공개성을 암시한다",
    "'보도자료로 배포됨', '언론사에 공개된 내용' 처럼 배포 채널로 암시한다",
    "공개 의도를 직접 문장으로 쓰지 않는다 — 문서 장르 자체(브로슈어·카탈로그 등)로 공개성이 자명하게 한다",
    "'고객 문의 응대용으로 누구나 요청하면 받을 수 있다' 처럼 접근성으로 암시한다",
]
NUMBER_KINDS = ["매출액·성장률", "ROI·투자수익률", "제품 성능개선율(%)", "가격·할인율",
                "고객사 수·시장점유율", "생산량·처리속도"]

SYS = ("너는 한국 기업의 마케팅·홍보·IR 부서 실무자다. 실제 외부 배포 목적의 공개 문서를 "
       "작성한다. 이 문서는 영업비밀이 아니라 처음부터 공개할 목적으로 만들어졌다. "
       "사용자가 지정한 정확한 수치를 반드시 그대로(반올림·변형 없이) 문서에 자연스럽게 녹여 써라. "
       "그 수치는 이미 공개됐거나 마케팅 목적의 성과 지표이지 미공개 영업비밀 데이터가 아니다. "
       "'기밀', '비밀', '대외비', '1급/2급/3급' 같은 등급 단어는 절대 쓰지 않는다.")

import random as _random
_rng = _random.Random(20260919)


def _random_numbers():
    # 매번 다른 숫자 조합을 미리 정해서 프롬프트에 직접 박는다 — LLM이 스스로 고르게 하면
    # (실측) 예시로 준 숫자를 그대로 베껴써 79% 반복되는 걸 오늘 직접 확인했다.
    return {
        "매출": f"{_rng.randint(15, 850)}억원",
        "성장률": f"{_rng.randint(3, 47)}%",
        "성능개선율": f"{_rng.randint(8, 62)}%",
        "고객사수": f"{_rng.randint(3, 210)}개사",
        "가격": f"{_rng.randint(5, 990)}만원",
        "시장점유율": f"{_rng.randint(2, 38)}%",
        "생산량": f"{_rng.randint(10, 480)}만대",
        "ROI": f"{_rng.randint(6, 55)}%",
    }


def gen_one(idx):
    company = COMPANIES[idx % len(COMPANIES)]
    industry = INDUSTRIES[idx % len(INDUSTRIES)]
    doc_type = DOC_TYPES[idx % len(DOC_TYPES)]
    signal_style = PUBLIC_SIGNAL_STYLES[idx % len(PUBLIC_SIGNAL_STYLES)]
    number_kind = NUMBER_KINDS[idx % len(NUMBER_KINDS)]
    nums = _random_numbers()
    # number_kind 주제와 관련된 2~3개 수치만 뽑아 구체적으로 지정(전부 다 넣으면 부자연스러움)
    keys = list(nums.keys())
    _rng.shuffle(keys)
    chosen = {k: nums[k] for k in keys[:3]}
    number_spec = ", ".join(f"{k}={v}" for k, v in chosen.items())

    user = (f"업종: {industry}\n회사명: {company}\n문서종류: {doc_type}\n"
            f"공개성 표현방식: {signal_style}\n"
            f"반드시 그대로 사용할 정확한 수치(변형 금지): {number_spec}\n\n"
            f"위 조건으로 현실적인 {doc_type} 한 건을 한국어로 작성해라(400~700자). "
            f"지정된 수치를 반드시 정확히 그대로 포함해라. "
            f"JSON 한 줄로만 출력: {{\"title\":\"제목\",\"body\":\"본문\"}}.")
    payload = json.dumps({
        "model": "qwen3:14b", "think": False, "stream": False,
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        "options": {"temperature": 0.95},
    }).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        obj = json.loads(m.group(0))
        return {
            "doc_id": f"pilot-pub-s3-{idx:03d}",
            "title": obj.get("title", ""),
            "text": f"{obj.get('title','')}\n\n{obj.get('body','')}".strip(),
            "label": "S3",
            "s_lv": 0, "v_lv": 0, "m_lv": 0,
            "domain": industry, "document_type": doc_type,
            "company": company, "public_signal_style": signal_style,
            "assigned_numbers": chosen,
            "label_source": "pilot_public_with_numbers_s3",
            "document_origin": "synthetic",
        }
    except Exception as e:
        print(f"  [실패 idx={idx}] {repr(e)[:150]}", file=sys.stderr)
        return None


N = 28
t0 = time.perf_counter()
results = []
with cf.ThreadPoolExecutor(max_workers=6) as ex:
    futs = [ex.submit(gen_one, i) for i in range(N)]
    for f in cf.as_completed(futs):
        r = f.result()
        if r:
            results.append(r)
        print(f"  {len(results)}/{N} 완료 · {time.perf_counter()-t0:.0f}s")

OUT = r"C:\Users\tio\AppData\Local\Temp\claude\f--antigravity-rag\b4af2725-ee90-4704-b834-0138a41218a5\scratchpad\pilot_public_with_numbers_s3_v2.jsonl"
with open(OUT, "w", encoding="utf-8") as f:
    for r in results:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print(f"\n생성 완료: {len(results)}/{N}건 -> {OUT}")
