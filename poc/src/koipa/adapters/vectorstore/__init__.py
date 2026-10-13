"""벡터 저장소 — 유사 문서 조회 전용.

[2026-09-09] 되살린 자리다. 2026-09-04(319069b9)에 RAG 를 걷으면서 이 패키지를 통째로
지웠는데, 고객사가 유사문서 검색을 요구해 다시 세운다. **그때 것을 그대로 되돌리지는
않았다** — 옛 PgVectorStore 는 컬렉션·alias(blue-green 재색인)·하이브리드 채널·병렬
힌트까지 461줄이었고, 그 대부분은 질의응답(/answer)을 위한 것이었다. 지금 필요한 것은
"문서 하나와 비슷한 문서를 찾는다" 하나뿐이라 그만큼만 만든다.
"""

from koipa.adapters.vectorstore.document_vectors import (
    DocumentVectorStore,
    SimilarDocument,
)

__all__ = ["DocumentVectorStore", "SimilarDocument"]
