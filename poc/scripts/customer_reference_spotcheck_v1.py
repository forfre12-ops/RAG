"""Preselected, non-blind second AI review; never a benchmark release."""

from __future__ import annotations

from pathlib import Path
import sys

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

DEFAULT_PARENT = POC / "reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6"
DEFAULT_ADOPTION = POC / "reports/CUSTOMER_REFERENCE_ADOPTION_20260915/adoption_v1"
SOURCE_SHA = "7e8ea7552e272f5391f1d95113176f955f8828352d38f45cfc24b979eb3c8f95"
ADOPTION_SHA = "7c1d0f2f7004e33fd7ac7225fa1788f908973146725562dcfb2a34f32b326ce1"
POLICY_SHA = "e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9"
LEDGER_SHA = "1f9ceca8e52e1487d8f5e54eedc5dac7b10e8038e984c36f4068eb63b8a6a694"

# Fixed before rereading the 40. Within each grade: first 10 accepted originals
# sorted by SHA256(UTF-8 doc_id), then doc_id. No redraw after observing findings.
SELECTED = {
    "TS": "doc-487de0af0dd97c2393c55059 doc-adf966e3c6b0022240f43b6a doc-8ec96814913fa2ce4e5e7cbb doc-0e841fbeccdb32bfa05146e4 doc-7547dbf931bcab493e28f08b doc-a78947b9b0516088eb32f01b doc-8c7e6b190a8ec0586d1dbebe doc-5ec7aeacd4cee10265070df2 doc-7d5d6aab8e832065aa9b0838 doc-11dbcc666223ac077805ec46".split(),
    "S1": "doc-878118e40b07ac473b974abc doc-0f006e679e2513e5909509e9 doc-62549e30bb12b6dc4551cf15 doc-bb4cdc0da2c6d00f2653dd52 doc-ae12ad614782f2146e375d74 doc-c17c729f4dbee603eaad85e3 doc-bbc8fcbc16a84abe67657597 doc-2b0687293c217ff43065f040 doc-f5e62c6b42731051e4db9e5f doc-a999d64ef33b294190566c40".split(),
    "S2": "doc-dd43e10b5426b22fbbcd7f91 doc-542215b705451a4fa39e4579 doc-34979c8ac8bd1182110cf231 doc-5222c3507ee284d84e125687 doc-35d2981156b0abd601a81b66 doc-5026ffc2d5a6b8c2ce148363 doc-06fe699d95ce7f318a995de7 doc-5cc8d82f9cf912455e9291d1 doc-2acde9710e22f3ef7fff77bb doc-c9556c5b89ae30bcadf7ead6".split(),
    "S3": "doc-ac34287a00a7457de269a822 doc-61b8b87b478c560b9764f050 doc-893599aec5a9691d2da4451c doc-55c5baec97b6656f576e5bea doc-e895204a52f7b42ac7fd8a5a doc-58b134b24484e11774b704c6 doc-4f58e6b315f949738acbeec6 doc-45c19ab5476aae2622a2647b doc-72466becd83c00cdf411afc9 doc-33d25e914f794dde4c987d7f".split(),
}
