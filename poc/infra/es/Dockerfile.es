# Elasticsearch 8.15.3 + analysis-nori (한국어 분석기) 동봉 이미지.
#
# 기존 docker-compose.yml 는 image: 줄로 vanilla ES 8.15.3 을 받아왔으나
# Nori plugin 이 미포함이라 한국어 인덱스의 nori_tokenizer 사용 시 실패.
# Phase 0.2 (5070 Ti 풀가동 계획) 에서 본 Dockerfile 로 빌드 전환.
#
# 빌드:
#   docker compose build elasticsearch
# 검증:
#   docker compose up -d elasticsearch
#   curl http://localhost:9200/_cat/plugins  → analysis-nori 출력
#
# 우리 userdict_ko.txt (poc/infra/es/userdict_ko.txt) 는 docker-compose 의
# volumes 에서 마운트되므로 이 Dockerfile 에는 포함하지 않음.

FROM docker.elastic.co/elasticsearch/elasticsearch:8.15.3

# --batch: 라이선스 동의 인터랙티브 프롬프트 자동 통과
RUN bin/elasticsearch-plugin install --batch analysis-nori && \
    bin/elasticsearch-plugin install --batch repository-s3
