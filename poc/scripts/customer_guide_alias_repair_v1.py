"""Reviewed, label-blind removal of cosmetic target aliases for 64 frozen parents.

No new document or grade assignment. Units and distinct substantive nouns remain.
Unknown bodies, unreviewed Latin spans and ambiguous targets fail closed.
"""
from __future__ import annotations

import copy
import re

from koipa.policy_facts import require, text_digest

REVIEW_VERSION = "customer-guide-alias-repair-reviewed-v1"
PARENT_AUTHORING_SOURCE_SHA256 = "1bc15aef70701a86723b6ea326ad0b71cba78d4146f7808e9d9e0e535f638940"

# Explicit per-parent, per-span authored review. No grade/answer field is read.
# A review is not a human signature or independent semantic certification.
REVIEWED_PARENTS = {
  "family-atrium-energy-display": {
    "body_sha256": "dc674f2244e28538c94b04cbd901535f5f130e6ce1e9071c4e6bcefacec9d52d",
    "edits": [],
    "parent_doc_id": "doc-7b3f4dd02e395e661245276a",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-bleaching-exposure": {
    "body_sha256": "e981fec3e48868e6a200704da1bde2f82a740167fc284e269cf8820bd48180ee",
    "edits": [],
    "parent_doc_id": "doc-2af16934ef18795c32fe566b",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-bloom-probe-order": {
    "body_sha256": "f459fd4d78d8de7d621ea36d4d5be95537867810a45fc10f87d6d8caacd31d3b",
    "edits": [
      {
        "after": "분할은",
        "alias": "K",
        "before": "분할 K는",
        "end": 115,
        "noun": "분할",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 110
      }
    ],
    "parent_doc_id": "doc-8ec96814913fa2ce4e5e7cbb",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-bundle-exit-clause": {
    "body_sha256": "93ef2f6dd87e3627c61da27bf5c57f32ac5c7446e8ab21109c03dbdb9d81ed7c",
    "edits": [
      {
        "after": "거래군에는",
        "alias": "C",
        "before": "거래군 C에는",
        "end": 112,
        "noun": "거래군",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 105
      }
    ],
    "parent_doc_id": "doc-f53c6424e528e8c719b5d37a",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-candidate-prune-order": {
    "body_sha256": "60d95c93eb297db523ce4825fc410e031ea7d705f0da8314b37dc565847895d7",
    "edits": [
      {
        "after": "분할은",
        "alias": "V",
        "before": "분할 V는",
        "end": 109,
        "noun": "분할",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 104
      }
    ],
    "parent_doc_id": "doc-8903ad30405d9c35c017aaf0",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-catalogue-range-open": {
    "body_sha256": "8658ae136ca664e53fb5597c5d711e48963ade57265ca0f9e0322d096beb724a",
    "edits": [],
    "parent_doc_id": "doc-33d25e914f794dde4c987d7f",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-ceramic-ramp-knee": {
    "body_sha256": "4a9087bde3dbf0ccfdad305eaadceb259b5e8cccf742600280d0dbf37a73833f",
    "edits": [
      {
        "after": "분말은",
        "alias": "R",
        "before": "분말 R은",
        "end": 110,
        "noun": "분말",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 105
      }
    ],
    "parent_doc_id": "doc-aed7ba142a868702b95c256a",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-channel-incentive-return": {
    "body_sha256": "bb05788a7ead234d252eb7f89bcd9ebc40bcd66b8c7d4e6bf63e154917f9974a",
    "edits": [
      {
        "after": "경로는",
        "alias": "F",
        "before": "경로 F는",
        "end": 133,
        "noun": "경로",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 128
      }
    ],
    "parent_doc_id": "doc-0e841fbeccdb32bfa05146e4",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-cleanroom-return-slot": {
    "body_sha256": "c5509c3390d575273244fc707446cb870edf2822e4b094df3b0260485a27f542",
    "edits": [
      {
        "after": "구역은",
        "alias": "D",
        "before": "구역 D는",
        "end": 111,
        "noun": "구역",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 106
      }
    ],
    "parent_doc_id": "doc-d766ff8d7bd95c6a34488925",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-coldchain-clock-gap": {
    "body_sha256": "6aec606bf4fd15e7974c03b5f7b119f387dfd840d6e70b6408c38de1cc65cfbd",
    "edits": [],
    "parent_doc_id": "doc-5222c3507ee284d84e125687",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-compass-table-demo": {
    "body_sha256": "b00efcad753b078716b24e23a4abed547660f580fb7ebc686c4e0c81e9945db8",
    "edits": [],
    "parent_doc_id": "doc-9a97b3cc7f728db39a60c4e4",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-conductivity-label": {
    "body_sha256": "829342955c86b73f63cc21cb79fde55404783f7a236aed97b8787b9df3b51d3a",
    "edits": [],
    "parent_doc_id": "doc-99608f42745364786f7ea239",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-consignment-location": {
    "body_sha256": "26e9bc200199431fbd08c0c1ed05a367bc507cf939cf175ab14ac349f8caad34",
    "edits": [],
    "parent_doc_id": "doc-a7d15928744ae4f0550f3bb3",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-decimal-roundtrip": {
    "body_sha256": "95eb21d8b2b4e83dff874f2f62da0ab406355f4e26460f571d162c1683b47b44",
    "edits": [],
    "parent_doc_id": "doc-f5e62c6b42731051e4db9e5f",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-discount-base-recheck": {
    "body_sha256": "042374c668c122b8df1192ac993f1ad1a8b5e1eb192121951626df01bc56541e",
    "edits": [],
    "parent_doc_id": "doc-e40bbf9cc8cfdf508f1a23cf",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-dock-contact-mark": {
    "body_sha256": "e5dfa59337354f42728395a846dc2d6d7525b9ea204a46fd254e6b68fc3dc495",
    "edits": [],
    "parent_doc_id": "doc-5ca8542886204e2239fae7f4",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-droplet-hysteresis": {
    "body_sha256": "d5c809f7459dd31faf7912a723738d08917fee7036e1cd5842dbe1054946a260",
    "edits": [
      {
        "after": "표면에",
        "alias": "P",
        "before": "표면 P에",
        "end": 27,
        "noun": "표면",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 22
      },
      {
        "after": "표면은",
        "alias": "P",
        "before": "표면 P는",
        "end": 116,
        "noun": "표면",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 111
      }
    ],
    "parent_doc_id": "doc-07873551d0ea29ecda92df27",
    "preserved_single_latin": [
      {
        "end": 123,
        "quantity_quote": "2μL",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 122,
        "text": "L"
      }
    ],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-echo-gate-separation": {
    "body_sha256": "43753e079edd55668e7b423116528db94175970a13cce315c563b74b5270bef3",
    "edits": [],
    "parent_doc_id": "doc-2d9ce0cf0b41a3db0140fdd9",
    "preserved_single_latin": [
      {
        "end": 58,
        "quantity_quote": "12μs",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 57,
        "text": "s"
      },
      {
        "end": 70,
        "quantity_quote": "19μs",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 69,
        "text": "s"
      },
      {
        "end": 81,
        "quantity_quote": "7μs",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 80,
        "text": "s"
      }
    ],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-enzyme-addition-lag": {
    "body_sha256": "588315cc5bb79e96eb17f161aa3c1021cde749fbced26c9c92f24cf7a9d9d0f9",
    "edits": [
      {
        "after": "효소는",
        "alias": "E",
        "before": "효소 E는",
        "end": 110,
        "noun": "효소",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 105
      },
      {
        "after": "기질 배치에",
        "alias": "M",
        "before": "기질 배치 M에",
        "end": 225,
        "noun": "기질 배치",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 217
      }
    ],
    "parent_doc_id": "doc-95d3eb8964051bbd96b6cb90",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-event-dedup-expiry": {
    "body_sha256": "a26044471e8e8afd76723d4b7092c357b8298d1690dcaf1de385636e6045edac",
    "edits": [],
    "parent_doc_id": "doc-48746f6cc6b75d14cf635d26",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-export-permission-list": {
    "body_sha256": "774ce77c4016e1063b52c52af737cdb3a01346e6a3c2864d6703279b78d7bb2c",
    "edits": [],
    "parent_doc_id": "doc-a311e969428c3049bec8a93d",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-fair-leaflet-count": {
    "body_sha256": "df2beae041b29413330930615cade42bd53fc14b90236f5ed6ba7e7dc5d3ba6e",
    "edits": [],
    "parent_doc_id": "doc-b042b11a6d17f7aea7194534",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-feature-flag-cohort": {
    "body_sha256": "59b342bc3109eb79e8afdbdbf954676a7fd47357993770cc024557bf6c5465e8",
    "edits": [],
    "parent_doc_id": "doc-72325363f322a37c85076284",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-fountain-public-sign": {
    "body_sha256": "d98d84eeb435701a68b3f643e1aa1e80e18b9846342e407874226357c03a692a",
    "edits": [],
    "parent_doc_id": "doc-9869e61f60a228b31ef62412",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-freight-weight-unit": {
    "body_sha256": "d64d3c6c23bdcc055a0a283ab7344ffb54b7387b0ed026a87addae49c73f7a98",
    "edits": [],
    "parent_doc_id": "doc-fa23de38474b3fbd018b5991",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-furnace-standby-chain": {
    "body_sha256": "025610c8d61f2a44c87c3c87109caa7c34d7c01bfd5218f0b128c5e3ce296f7e",
    "edits": [
      {
        "after": "라인은",
        "alias": "T",
        "before": "라인 T는",
        "end": 129,
        "noun": "라인",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 124
      }
    ],
    "parent_doc_id": "doc-2e0de533b526e0dc1b2222fc",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-hoist-transfer-window": {
    "body_sha256": "df3734916e09fa0c7b14a9fb0d0ea4839e871a98e39700bb5ec0fed706e6104c",
    "edits": [
      {
        "after": "구역은",
        "alias": "G",
        "before": "구역 G는",
        "end": 107,
        "noun": "구역",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 102
      }
    ],
    "parent_doc_id": "doc-6ad9285f9f05e1a93717d162",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-humidity-exhibit-span": {
    "body_sha256": "2d0b4df5e289d832ad71cc6fbdcd366956ce3a19c1ce7cd019543185948b27fa",
    "edits": [],
    "parent_doc_id": "doc-0ab2a9859a99081e2bc61da0",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-interview-consent-ledger": {
    "body_sha256": "a9de844d6a5919e6b28ea493df74912e01b3e4ce9d3f987fdc7b870266ac9d42",
    "edits": [],
    "parent_doc_id": "doc-492e2cfdad256cca48ef8538",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-lease-clock-origin": {
    "body_sha256": "1a38adaa367957b101a8407b6948e88c655a7dfd5dd3989acccceaecdd962208",
    "edits": [],
    "parent_doc_id": "doc-4ad1d93ff48dc28f724d3de3",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-lens-screen-demo": {
    "body_sha256": "ce75f829c5bc10934fa911b4cb4a21c514da7af572cd590e928d34b0d8028108",
    "edits": [],
    "parent_doc_id": "doc-ce3c63ec2018275b562e0fb1",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-license-renewal-seats": {
    "body_sha256": "405a2d994224383dc9b0f4edd4313e043209d34a95e8ddfca25e0c4fab837291",
    "edits": [],
    "parent_doc_id": "doc-b3b9a9f83e7fb891f4ce5d94",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-linen-lot-return": {
    "body_sha256": "ef858e92485fb6875debfddbf575b37880411c961a5972e0588d7eb6a2ddba09",
    "edits": [],
    "parent_doc_id": "doc-06fe699d95ce7f318a995de7",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-markdown-link-lab": {
    "body_sha256": "0fd9e664660f7c9acae1870d236510668f72c9e6d0604e36475029b5410acfce",
    "edits": [],
    "parent_doc_id": "doc-58b134b24484e11774b704c6",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-multipart-commit-map": {
    "body_sha256": "08aa5156cca9e240974d16558918f152df9a3836c11878ad00ee87282621d44f",
    "edits": [
      {
        "after": "저장 경로는",
        "alias": "M",
        "before": "저장 경로 M은",
        "end": 138,
        "noun": "저장 경로",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 130
      }
    ],
    "parent_doc_id": "doc-97bedc91744d3481fee0b93b",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-nonresponse-followup": {
    "body_sha256": "9c125fecb95e2ec9e55e108cc59d1834e8bf2f225b35c70470ff375538d6a2d8",
    "edits": [],
    "parent_doc_id": "doc-3638333f4c12207010a0395e",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-null-sort-order": {
    "body_sha256": "a805470ffd5e72b20bce46abd40a9eec4d455515ccec2d9ef46652d77e2f8a06",
    "edits": [],
    "parent_doc_id": "doc-bbc8fcbc16a84abe67657597",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-parcel-seal-handover": {
    "body_sha256": "12fec2fcdb5338b75d019ddb87fbde2ab9fb111d4cb5e97c682728a3efba5253",
    "edits": [],
    "parent_doc_id": "doc-b6e0cd7279f2b549ff30e8e9",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-parking-common-guide": {
    "body_sha256": "12b96c2f6df0c1c1624384ca9a700169572554d8c717cd796f4dc46c98480cf1",
    "edits": [],
    "parent_doc_id": "doc-72466becd83c00cdf411afc9",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-pipette-sample-map": {
    "body_sha256": "be4a572e826449b54fb741fa237bb9021b4cf568671c0e78f454d827afb51a26",
    "edits": [],
    "parent_doc_id": "doc-2197050ac1fe3e8061545a6e",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-plural-resource-check": {
    "body_sha256": "d4289c15a8fcd493248971217ba17f7c896f9667ae2acdcba588168e2e96b031",
    "edits": [],
    "parent_doc_id": "doc-93e1545c7784b5b4b430a284",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-polarized-crystal-demo": {
    "body_sha256": "4a66c2c82ed404a03b78adbee3b5cbedb00a3ace702b854b36caeac1a4272c29",
    "edits": [],
    "parent_doc_id": "doc-2f914cb7cd6b556f599baa76",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-powder-charge-decay": {
    "body_sha256": "eaeb9702ac6a1f6ebf2cf0f533edc633a9176163992f0cefd2613c423cf5e336",
    "edits": [
      {
        "after": "분말이",
        "alias": "W",
        "before": "분말 W가",
        "end": 126,
        "noun": "분말",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 121
      }
    ],
    "parent_doc_id": "doc-ce62764da1782b118d686aad",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-printer-queue-guide": {
    "body_sha256": "cedf5982c3fd4571e664e37ed4ca40465f6435fae16f341eb7e07e325e393f18",
    "edits": [],
    "parent_doc_id": "doc-9f551ed382ba9aac80684199",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-public-response-scale": {
    "body_sha256": "0616a307a55be7c653526deb1fec6644af5b8e5ee730740c96450d8293eb4d04",
    "edits": [],
    "parent_doc_id": "doc-080541b70d5c3780befa771f",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-pump-spectrum-window": {
    "body_sha256": "a9bf50d4e86bdb25b56ae0df522d5a6825c895b2dcc1ac8d78198268819305d5",
    "edits": [],
    "parent_doc_id": "doc-c3470c6eddd28eeb5c1ea385",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-quota-sample-shift": {
    "body_sha256": "1398becc5382c2709fb1c9dd0df23f10e7d3ddc6caaf7578a048a5679f141ac4",
    "edits": [],
    "parent_doc_id": "doc-f2545285bfc078b47a8fa7e5",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-radiograph-envelope": {
    "body_sha256": "7398f685f592accea3263126bc91e08fa3bf247016043d898813e46f08db9627",
    "edits": [],
    "parent_doc_id": "doc-b8661d6d81368905dfbda2bc",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-relative-path-demo": {
    "body_sha256": "f370eb1d90dd5a6cf507bdbc1e30baca09e2d90e3efc85e9fc240aa1d25780bd",
    "edits": [],
    "parent_doc_id": "doc-ae2ecea912f691fafe6bc1d8",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-rotor-weight-log": {
    "body_sha256": "9812f9b61807677a087d352cbf5c8db2f866cbaf4c3f6a99d239923c8f5f1b14",
    "edits": [],
    "parent_doc_id": "doc-93bb80f29d4df78921460d66",
    "preserved_single_latin": [
      {
        "end": 50,
        "quantity_quote": "18.6g",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 49,
        "text": "g"
      },
      {
        "end": 59,
        "quantity_quote": "2.4g",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 58,
        "text": "g"
      },
      {
        "end": 77,
        "quantity_quote": "21.0g",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 76,
        "text": "g"
      }
    ],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-schedule-calendar-gap": {
    "body_sha256": "a01e0bc7a876f89e8ff7fba4dce401485801d3f197eb2b9cc2c05909618ffe8a",
    "edits": [],
    "parent_doc_id": "doc-87f6370e6256bdfa7ec30301",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-screen-zoom-guide": {
    "body_sha256": "5822959a9d49d797c39e5152bd04fa6e617981d3c0e66c1b0534bf2aafba78b2",
    "edits": [],
    "parent_doc_id": "doc-a86e54cec33b9b874b5410ee",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-settling-window": {
    "body_sha256": "10540c0106396a0f86ad834c121828464ed5ef9e99cf3805506c25fba0317ba6",
    "edits": [],
    "parent_doc_id": "doc-0cb148c6a6ed3845ccb9f0d8",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-slot-replenish-wave": {
    "body_sha256": "c5b838500dc99f3813f7743ea78b5379d27c687c13900d7a8cea880aa75638b8",
    "edits": [
      {
        "after": "구역은",
        "alias": "S",
        "before": "구역 S는",
        "end": 120,
        "noun": "구역",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 115
      }
    ],
    "parent_doc_id": "doc-827ddd7648c5e1bfa7ff8b8a",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-snapshot-cut-index": {
    "body_sha256": "ac740340c7bbf504480c0d68458dfad85c9f32be138c52caf8af6fa2c8f22020",
    "edits": [
      {
        "after": "저장소는",
        "alias": "Q",
        "before": "저장소 Q는",
        "end": 117,
        "noun": "저장소",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 111
      }
    ],
    "parent_doc_id": "doc-cb5dd36d9ce00e1b3a76f933",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-spare-part-alias": {
    "body_sha256": "10f1440f7152544871fdd1bfec8c5644a552de1decc09d30c12a606f7c779bd5",
    "edits": [],
    "parent_doc_id": "doc-9dabe4b1467a71b5fafdf44a",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-staff-meal-demand": {
    "body_sha256": "d53081f59e862c279ff4d5ac73451ef465cae1f75cb319d41efce377e29aae28",
    "edits": [],
    "parent_doc_id": "doc-8d6ca21a1d545fe51589f21e",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-standby-load-history": {
    "body_sha256": "f741d7703937f88a8a753d21b6274045b15cb17ef9d3213676382a0ba0e448b6",
    "edits": [],
    "parent_doc_id": "doc-9c8ae0d2cd1bfb9e436c3871",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-strain-gauge-offset": {
    "body_sha256": "562c39449f9c1451b2f23fcb7cdc969f950d2ed0c3f6d8a2a6f6a94b4764ec88",
    "edits": [],
    "parent_doc_id": "doc-daa056e38c2b7fb881a6059c",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-subscription-downgrade": {
    "body_sha256": "06b4dae028f6539552c30a1f0a431e879386e0e1ab7d7d329817bd97083b4a06",
    "edits": [
      {
        "after": "상품은",
        "alias": "L",
        "before": "상품 L은",
        "end": 112,
        "noun": "상품",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 107
      }
    ],
    "parent_doc_id": "doc-01b428b0a2b89b18647cf956",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-supplier-delay-window": {
    "body_sha256": "f04ef10268c3bf10f948a8d0df2858be52d04232a329be0871f5f2364c80c447",
    "edits": [
      {
        "after": "공급군에는",
        "alias": "N",
        "before": "공급군 N에는",
        "end": 124,
        "noun": "공급군",
        "reason": "문서 안에서 동일 명사에 하나의 대상만 있으며 익명 식별 문자를 제거하고 명사와 필요한 조사를 유지한다.",
        "start": 117
      }
    ],
    "parent_doc_id": "doc-8c7e6b190a8ec0586d1dbebe",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-tender-revision-map": {
    "body_sha256": "cfd91249e6da70f987830f701dcd765d997342dbf2e71248ce461ecbf6262ebf",
    "edits": [],
    "parent_doc_id": "doc-064ad9badaaa1fbdd300c5c4",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-visitor-route-sign": {
    "body_sha256": "849c5bf2d6f82a15da2a1cda3bb063434a86b4e0337c95e26662a9dfee92a8f5",
    "edits": [],
    "parent_doc_id": "doc-9ed6478b0539bdf3b020dd3d",
    "preserved_single_latin": [
      {
        "end": 50,
        "quantity_quote": "45m",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 49,
        "text": "m"
      },
      {
        "end": 61,
        "quantity_quote": "30m",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 60,
        "text": "m"
      },
      {
        "end": 77,
        "quantity_quote": "75m",
        "reason": "숫자와 붙은 측정 단위로 대상 별칭이 아니며 그대로 보존한다.",
        "start": 76,
        "text": "m"
      }
    ],
    "review_status": "authored_scope_review_not_human_signoff"
  },
  "family-work-permit-dates": {
    "body_sha256": "6c12331cd9696a94a7ea9d74932839d95a20ddd5a7814a824707337641c4749a",
    "edits": [],
    "parent_doc_id": "doc-f0ff101c492e8573de893fb1",
    "preserved_single_latin": [],
    "review_status": "authored_scope_review_not_human_signoff"
  }
}

SINGLE_LATIN = re.compile(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])")
QUANTITY = re.compile(r"\d+(?:\.\d+)?(?:μL|μs|g|m)(?![A-Za-z])")
TARGET_PAIR = re.compile(r"([가-힣]+(?: [가-힣]+)?) ([A-Z])(?=[은는이가을를에와과도\s.,])")


def _particle(noun, suffix):
    require(bool(noun) and "가" <= noun[-1] <= "힣", "alias_noun_invalid")
    final = (ord(noun[-1])-ord("가")) % 28 != 0
    if suffix in {"은", "는"}:
        return "은" if final else "는"
    if suffix in {"이", "가"}:
        return "이" if final else "가"
    require(suffix in {"에", "에는"}, "alias_unreviewed_particle")
    return suffix


def reject_ambiguous_targets(text):
    nouns = {}
    for match in TARGET_PAIR.finditer(text):
        noun, alias = match.groups()
        # Prefix adjectives may differ; the final target noun must still be unique.
        nouns.setdefault(noun.split()[-1], set()).add(alias)
    require(all(len(values) == 1 for values in nouns.values()), "alias_multiple_targets_ambiguous")


def _checked_review(family_id, text):
    require(type(family_id) is str and family_id in REVIEWED_PARENTS, "alias_unreviewed_parent")
    require(type(text) is str and bool(text), "alias_body_required")
    reject_ambiguous_targets(text)
    review = REVIEWED_PARENTS[family_id]
    require(text_digest(text) == review["body_sha256"], "alias_parent_body_mismatch")
    edits = copy.deepcopy(review["edits"])
    cursor, reviewed_latin = 0, set()
    alias_by_noun = {}
    for edit in edits:
        start, end = edit["start"], edit["end"]
        require(type(start) is int and type(end) is int and cursor <= start < end <= len(text), "alias_span_invalid")
        require(text[start:end] == edit["before"] and len(edit["reason"]) >= 20, "alias_span_source_mismatch")
        noun, alias = edit["noun"], edit["alias"]
        require(type(alias) is str and re.fullmatch(r"[A-Z]", alias), "alias_letter_invalid")
        prefix = noun+" "+alias
        require(edit["before"].startswith(prefix), "alias_noun_prefix_mismatch")
        suffix = edit["before"][len(prefix):]
        require(edit["after"] == noun+_particle(noun, suffix), "alias_noncosmetic_replacement")
        alias_by_noun.setdefault(noun, set()).add(alias)
        require(len(alias_by_noun[noun]) == 1, "alias_multiple_targets_ambiguous")
        latin = [m for m in SINGLE_LATIN.finditer(text, start, end)]
        require(len(latin) == 1 and latin[0].group() == alias, "alias_span_latin_mismatch")
        reviewed_latin.add(latin[0].span())
        cursor = end
    for kept in review["preserved_single_latin"]:
        start, end = kept["start"], kept["end"]
        require(type(start) is int and type(end) is int and text[start:end] == kept["text"], "alias_kept_span_mismatch")
        matches = [m for m in QUANTITY.finditer(text) if m.start() <= start < end <= m.end()]
        require(len(matches) == 1 and matches[0].group() == kept["quantity_quote"], "alias_unit_not_verified")
        require((start, end) not in reviewed_latin, "alias_review_span_overlap")
        reviewed_latin.add((start, end))
    require(reviewed_latin == {m.span() for m in SINGLE_LATIN.finditer(text)}, "alias_unreviewed_latin_span")
    return review, edits


def transform(family_id, text):
    """Input is only a reviewed family key and body. No grade-dependent branch."""
    review, edits = _checked_review(family_id, text)
    pieces, cursor, shift = [], 0, 0
    for edit in edits:
        pieces.extend([text[cursor:edit["start"]], edit["after"]])
        edit["target_start"] = edit["start"]+shift
        edit["target_end"] = edit["target_start"]+len(edit["after"])
        shift += len(edit["after"])-len(edit["before"])
        cursor = edit["end"]
    pieces.append(text[cursor:])
    result = "".join(pieces)
    require(text.splitlines()[0] == result.splitlines()[0], "alias_title_changed")
    require(re.findall(r"\d+(?:\.\d+)?", text) == re.findall(r"\d+(?:\.\d+)?", result), "alias_numbers_changed")
    source_cursor, target_cursor = 0, 0
    unchanged_segments = []
    for edit in edits:
        old_segment = text[source_cursor:edit["start"]]
        new_segment = result[target_cursor:edit["target_start"]]
        require(old_segment.encode("utf-8") == new_segment.encode("utf-8"), "alias_outside_span_changed")
        unchanged_segments.append({"source_start": source_cursor, "source_end": edit["start"],
            "target_start": target_cursor, "target_end": edit["target_start"], "sha256": text_digest(old_segment)})
        source_cursor, target_cursor = edit["end"], edit["target_end"]
    require(text[source_cursor:].encode("utf-8") == result[target_cursor:].encode("utf-8"), "alias_outside_span_changed")
    unchanged_segments.append({"source_start": source_cursor, "source_end": len(text),
        "target_start": target_cursor, "target_end": len(result), "sha256": text_digest(text[source_cursor:])})
    return {"text": result, "body_sha256": text_digest(result), "edits": edits, "changed": result != text,
            "parent_body_sha256": review["body_sha256"], "review_version": REVIEW_VERSION,
            "unchanged_segments": unchanged_segments, "whole_semantic_equivalence_certified": False}


def rebind_claims(claims, source, variant):
    edits, target = variant["edits"], variant["text"]

    def offset(position):
        require(not any(e["start"] < position < e["end"] for e in edits), "alias_claim_cuts_edit")
        return position+sum(len(e["after"])-len(e["before"]) for e in edits if e["end"] <= position)

    rebound = []
    for claim in claims:
        row = claim.model_dump() if hasattr(claim, "model_dump") else copy.deepcopy(claim)
        require(source[row["start"]:row["end"]] == row["quote"] == row["claim"], "alias_claim_source_mismatch")
        start, end = offset(row["start"]), offset(row["end"])
        quote = target[start:end]
        rebound.append({**row, "start": start, "end": end, "quote": quote, "claim": quote, "sha256": text_digest(quote)})
    return rebound
