"""Revise the remaining S2/S3 proxy candidates, preserving their originals."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/"datasets"/"proxy_gold"/"single_document_candidates"
REV=DIR/"revisions"
S2={
"GOLD-CAND-S2-INC-011":"현장 사고 대응과 재발 방지 조치",
"GOLD-CAND-S2-OPS-034":"운영 개선의 적용 범위와 효과 확인",
"GOLD-CAND-S2-OPS-046":"운영 전환 과정과 역할 인계",
"GOLD-CAND-S2-OPS-082":"고객지원 지식베이스 개편 결과",
"GOLD-CAND-S2-QA-085":"현장 품질 이슈의 재발 방지 점검",
"GOLD-CAND-S2-IT-088":"협업도구 권한 정리와 전환 확인",
"GOLD-CAND-S2-HR-091":"신규 입사자 보안 안내 과정 개선",
}
S3={"GOLD-CAND-S3-COMM-094":"서비스 점검 일정 확인 방법","GOLD-CAND-S3-HR-097":"외부 교육 신청과 수료 확인"}

def s2_add(focus:str)->str:
 return f"""

## 적용 범위와 확인 근거

이 문서는 **{focus}**에 관한 운영 기록을 보완한다. 기록의 목적은 조치가 있었다는 사실만 남기는 것이 아니라, 어느 대상에 어떤 기준으로 적용했는지와 아직 확인하지 못한 범위를 구분하는 데 있다. 공통 조치가 개별 현장의 모든 조건을 대신하지 않으므로, 적용 대상·제외 대상·확인 시점을 함께 보존한다.

| 확인 항목 | 확인 방법 | 완료로 보지 않는 경우 |
| --- | --- | --- |
| 실행 여부 | 담당자 기록과 시스템 이력 대조 | 구두 보고만 있고 증빙이 없는 경우 |
| 효과 | 전후 표본·예외 사례 비교 | 기준 기간 또는 표본이 다른 경우 |
| 인계 | 책임자·다음 점검일 확인 | 임시 담당자에게만 전달된 경우 |

## 운영 결과의 해석과 예외

결과 수치가 좋아졌더라도 원인을 단정하지 않는다. 동일 시기에 바뀐 안내문, 인력 배치, 시스템 설정, 교육 순서가 함께 영향을 주었을 수 있기 때문이다. 반대로 일부 예외가 남아 있다고 해서 전체 조치가 무효가 되는 것도 아니다. 예외는 유형·발생 조건·추가 확인 필요 여부로 분류해 다음 점검의 입력으로 사용한다.

임시 우회 절차와 내부 역할 배치는 일반 공개 안내에 포함하지 않는다. 외부 공유가 필요한 경우에는 이용자에게 필요한 사실만 새로 작성하며, 이 문서에 있는 운영 순서·내부 판단 기준·취약 지점은 제외한다.

## 재점검과 인계 기준

다음 점검에서는 이전 결과를 복제하지 말고, 유지할 조건과 새로 확인할 조건을 먼저 적는다. 담당자가 변경되거나 범위가 확대되면 기존 완료 표시는 자동으로 연장되지 않는다. 로그·표본·담당자 확인이 서로 다르면 한 기록을 지우지 않고 불일치 자체를 보류 항목으로 남긴다.

종료 전에는 실행 증빙, 예외 인계, 다음 점검일, 자료 보존 위치를 함께 확인한다. 이후 사실이 추가되면 기존 기록을 덮어쓰지 않고 변경 사유와 영향 범위를 v3 이후의 개정 이력으로 남긴다. 이 문서는 사람 검수 전의 합성 Proxy Gold 후보라는 상태를 유지한다.

### 검토자 확인

검토자는 조치의 결과뿐 아니라 증빙의 존재와 적용 범위를 함께 확인한다. 표본이 제한적이면 그 제한을 종료 판단에도 적고, 전체 운영 표준으로 확대하기 전에는 별도 검토를 수행한다.

### 자료 보존과 변경 추적

관련 원문, 점검표, 로그, 교육·공지 자료, 담당자 확인은 같은 관리번호 아래 연결한다. 이후 수정이 필요한 경우에는 현재 문서를 통째로 바꾸지 않고, 수정된 사실·수정 이유·영향을 받는 적용 범위를 새 이력에 기록한다. 이 기록은 다음 담당자가 과거 조치의 맥락을 재구성하고, 같은 문제가 다시 발생했을 때 무엇을 먼저 확인해야 하는지 판단하도록 돕는다.

운영 문서의 길이를 늘리는 것이 목표는 아니다. 실제로 필요한 판단 근거가 빠지지 않도록 범위, 예외, 증빙, 인계의 네 요소를 남기는 것이 목적이다. 그 네 요소 중 하나라도 확인되지 않으면 완료 대신 보류 상태로 표시한다.
재점검 결과도 같은 기준으로 연결한다.
확인 책임자와 확인일은 누락하지 않는다.
모든 예외는 다음 검토 의제로 남긴다.

### 업무 적용 전 확인 사항

적용 전에는 문서에 적힌 대상·기간·책임자가 현재 업무와 일치하는지 확인한다. 비슷한 사례라도 고객 환경, 장비 상태, 담당 부서, 계약 조건이 다르면 같은 조치를 자동 적용하지 않는다. 적용 후에는 실행 사실, 결과 관찰, 남은 예외를 분리해 기록하고, 하나의 결과값으로 전체 효과를 주장하지 않는다.

문서가 다른 부서로 전달될 때에는 필요한 사실과 내부 운영 정보의 경계를 다시 확인한다. 공개 가능한 안내와 내부 판단 기준을 한 파일에 섞지 않으며, 외부 설명이 필요하면 목적에 맞는 별도 자료를 작성한다. 이 구분은 자료 관리의 편의가 아니라 운영상 민감 정보가 의도치 않게 확산되는 것을 막기 위한 통제다.
"""

def s3_add(focus:str)->str:
 return f"""

## 적용 대상과 공개 범위

이 안내문은 **{focus}**을 위한 공개 정보다. 누구나 확인할 수 있는 절차·일정·공식 문의 경로만 다루며, 계정 정보, 개별 계약, 내부 운영 기준, 다른 이용자의 상태는 포함하지 않는다. 게시된 날짜와 최신 수정 시각을 함께 확인하고, 과거 공지를 현재 상태로 해석하지 않는다.

## 이용자 확인 순서

안내를 이용하기 전에는 적용 대상과 공식 공지 채널을 확인한다. 안내 시간 중에는 공지의 갱신 내용을 기준으로 판단하며, 개인 식별 정보나 인증 정보는 어떤 문의 채널에도 남기지 않는다. 안내 이후 문제가 남으면 발생 시점과 공개 기능처럼 필요한 최소 정보만 정리해 지정된 지원 창구에 문의한다.

## 예외 문의와 문서 이력

개별 계약·기술 환경·계정 상태처럼 확인이 필요한 문의는 이 문서로 추정 답변하지 않는다. 담당 부서의 본인 확인 또는 별도 검토 절차로 연결한다. 공개 문서의 내용이 바뀌면 이전 안내를 조용히 교체하지 않고 변경일·변경 이유·적용 범위를 기록한다. 이 문서는 실서비스 공지가 아니라 공개성 경계 검수를 위한 합성 후보이며, 사람 검수 전에는 골든 정답지로 사용하지 않는다.

### 문의 전 유의사항

문의에는 필요한 최소 사실만 포함한다. 비밀번호, 인증 코드, 다른 이용자의 정보, 내부 화면의 상세 캡처는 제출하지 않는다. 일반 안내로 해결되지 않는 경우에는 공식 지원 창구가 요청하는 본인 확인 절차를 따르며, 공개 게시물의 댓글이나 외부 채널에서 개별 처리 결과를 요구하지 않는다.
안내의 적용 범위를 벗어난 요청은 담당 부서 검토 후 답변한다.
항상 최신 공지를 기준으로 확인한다.
기록은 공개 채널에 남는다.
문의는 지정 창구로 한다.
"""

def main()->int:
 REV.mkdir(exist_ok=True); prepared=[]
 for group,minimum in ((S2,4000),(S3,2500)):
  for doc_id,focus in group.items():
   meta_path=DIR/f"{doc_id}.metadata.json"; meta=json.loads(meta_path.read_text(encoding='utf-8'))
   if meta.get('content_revision_path'): raise FileExistsError(doc_id)
   src=list(DIR.glob(f"{doc_id}_*.md"))
   if len(src)!=1: raise ValueError(doc_id)
   text=(src[0].read_text(encoding='utf-8').rstrip()+(s2_add(focus) if doc_id in S2 else s3_add(focus))).strip()+"\n"; path=REV/f"{doc_id}.v3.md"
   if path.exists() or len(text)<minimum: raise ValueError(f"{doc_id} {len(text)}")
   prepared.append((doc_id,meta_path,meta,path,text))
 for doc_id,meta_path,meta,path,text in prepared:
  path.write_text(text,encoding='utf-8');meta['content_revision']='v3';meta['content_revision_path']=str(path.relative_to(DIR)).replace('\\','/');meta['content_revision_note']='S2/S3 scope, exception, and reviewability expansion';meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(doc_id,len(text))
 return 0
if __name__=='__main__':raise SystemExit(main())
