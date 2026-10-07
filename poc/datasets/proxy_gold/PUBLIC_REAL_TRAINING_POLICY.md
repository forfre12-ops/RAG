# Public-real training policy

This policy applies only to text collected from an individual Korea Policy
Briefing detail page that carries its own supported KOG-L marker.

## Permission boundary

- Accepted for training: KOG-L 0, KOG-L 1, and the KOG-L AI type.
- KOG-L 1 is used only with source attribution.  The permission basis is the
  Korea Culture Information Service's *2025 Q3 public-copyright issue report*,
  whose table 4 states that type 1 works may be used as AI-training data when
  attribution is provided:
  <https://www.kogl.or.kr/namoEditor/binary/files/000001/2025%EB%85%84_3%EB%B6%84%EA%B8%B0_%EA%B3%B5%EA%B3%B5%EC%A0%80%EC%9E%91%EB%AC%BC_%EC%9D%B4%EC%8A%88%EB%A6%AC%ED%8F%AC%ED%8A%B8_-_AI_%EC%8B%9C%EB%8C%80_%EA%B3%B5%EA%B3%B5%EC%A0%80%EC%9E%91%EB%AC%BC%EC%9D%B4_%EB%82%98%EC%95%84%EA%B0%80%EC%95%BC_%ED%95%A0_%EB%B0%A9%ED%96%A5_4.pdf>
- KOG-L 2, 3, or 4, an ambiguous marker, a listing-level marker without an
  item-level marker, or a missing text-free-use sentence fails closed.
- Only the licensed text is retained. Images, captions, video, attachments,
  author profiles, and copyright boilerplate are excluded.
- Every retained record keeps its source URL, agency, publication date,
  licence code, exact licence snippet, and licence-evidence hash.

## Split and claim boundary

- These records are public, so their supervised label is S3. They improve
  public-document style coverage and the false-positive boundary; they do not
  represent customer-internal TS/S1 documents.
- They are training-only and never count toward the frozen 1,000-record proxy
  evaluation, a human-reviewed golden set, or customer-real accuracy evidence.
- The development public-S3 v1 corpus and the sealed blind v2 corpus are both
  exclusion boundaries. Training assembly rejects overlap in `doc_id`,
  `document_family_id`, or normalized text hash.
- A training record may be released only through an immutable assembler run
  that revalidates source manifests, record hashes, training permission,
  uniqueness, attribution, and the blocked-corpus boundary.

