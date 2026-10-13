# -*- coding: utf-8 -*-
"""API 항목(입력·응답)과 DB 컬럼 중 '아무도 안 쓰는 것'을 항목·컬럼 단위로 센다.

왜 필요한가(2026-09-26). "API정의서·DB 에 필요 없는 항목·컬럼을 다 확인해 리스트업하라"는 범위 질문이었다.
기존 `audit_unused.py` 는 세 가지가 약했다.
  · 컬럼을 속성 이름 하나로 세어 표가 다른 같은 이름(created_at·status·doc_id…)을 합쳤다 — 분모가 224 가 아니라 175 였다.
  · 이름이 소스 어디에든 한 번 나오면 '쓰임'으로 셌다 — `title`·`doc_id` 처럼 흔한 이름은 다른 곳의 같은 이름 때문에 죽은 것이 산 것으로 보였다.
  · 저장소 메서드 안의 생성자 한 줄을 '쓰기'로 셌다 — 그 메서드를 부르는 곳이 없어도, 부르면서 그 인자를 안 넘겨도.

무엇을 하는가(전부 정적 분석 · DB 접속 불필요 · 대상은 `git ls-files -z` 로 정한다)
  ① DB 컬럼(ORM 표·컬럼 전수)  읽기·쓰기 신호를 표 단위로 센다(속성 읽기·생성자 키워드·대입·원시 SQL·문자열 키).
  ② 값 흐름  컬럼을 채우는 값이 함수 매개변수에서 온다면 그 함수의 호출자가 인자를 실제로 넘기는지 본다.
  ③ 호출자 없는 정의  제품코드·스크립트에서 이름이 한 번도 안 불리는 정의(시험만 부르는 것 포함).
  ④ API 응답 항목  응답 모델을 만드는 지점(생성자 키워드)마다 어떤 필드를 채우는지 대조한다 — 같은 스키마를 여러 작업이 쓰면
     '이 작업에서는 항상 비는 필드'가 드러난다(GET /classify/{doc_id} 가 ClassifyResponse 22개 중 11개를 안 채운다).
  ⑤ API 요청 항목  요청 모델을 타입 주석으로 따라가 어떤 필드가 읽히는지 본다.

⚠ 이 스크립트는 판정하지 않는다. 남는 후보를 사람이 읽고 결정한다. 알려진 한계:
  · 값이 딕셔너리·`**kwargs`·`model_validate(dict)` 로 만들어지는 응답 모델은 ④ 에서 판정 불가로 따로 센다.
  · ⑤ 는 `req.actor.user_id` 같은 연쇄 접근과 변수 이름이 바뀐 전달을 못 따라가 '읽기 0' 후보가 과하게 나온다 — 후보는 반드시 열어 볼 것.
  · 외부(KL) 호출자는 저장소에 흔적이 없다. '호출자 0' 은 '우리 코드·콘솔·스크립트가 안 부른다'이지 '외부도 안 쓴다'가 아니다.
  · 실 DB 값(NULL 비율)은 보지 않는다 — 로컬 DB 는 행이 몇 개뿐이라 근거가 못 된다(감사로그 target 컬럼은 4만 행이 전부 NULL 이었지만
    호출자는 있었다).

사용:
    cd poc && TESTING=1 python scripts/audit_unused_fields.py            # 요약과 후보 목록
    cd poc && TESTING=1 python scripts/audit_unused_fields.py --json out.json
"""
from __future__ import annotations

import argparse
import ast
import collections
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        setattr(sys, _s, io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

os.environ.setdefault("TESTING", "1")
os.environ["DEPLOY_PROFILE"] = "full-train"  # 라우트는 배포 프로파일에 딸려 있다 — koipa import 전에 고정

_POC = Path(__file__).resolve().parent.parent
_ROOT = _POC.parent
sys.path.insert(0, str(_POC / "src"))

# 정의(정본)라서 '참조'로 세면 안 되는 파일
_DEFINITIONS = {"poc/src/koipa/db/models.py", "poc/src/koipa/db/standard_names.py"}


def _files() -> list[str]:
    raw = subprocess.run(["git", "ls-files", "-z"], capture_output=True, cwd=_ROOT).stdout.decode("utf-8")
    return [f for f in raw.split("\0") if f]


def _parse(rel: str):
    try:
        return ast.parse((_ROOT / rel).read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return None  # 작업트리에서 지워졌지만 아직 커밋 안 된 추적 파일


class Facts:
    """한 코퍼스의 AST 사실."""

    def __init__(self, files: list[str]):
        self.load_attr = collections.Counter()
        self.store_attr = collections.Counter()
        self.kw = collections.Counter()
        self.kw_callee = collections.Counter()
        self.strs = collections.Counter()
        self.sql_blobs: list[tuple[str, str]] = []
        for rel in files:
            tree = _parse(rel)
            if tree is None:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Attribute):
                    (self.store_attr if isinstance(n.ctx, ast.Store) else self.load_attr)[n.attr] += 1
                elif isinstance(n, ast.Call):
                    callee = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else "?")
                    for k in n.keywords:
                        if k.arg:
                            self.kw[k.arg] += 1
                            self.kw_callee[(callee, k.arg)] += 1
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    v = n.value
                    if len(v) <= 60 and "\n" not in v:
                        self.strs[v] += 1
                    if len(v) > 20 and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)\b", v, re.I):
                        self.sql_blobs.append((rel, v))


# ── ① DB 컬럼 ───────────────────────────────────────────────────────────
def orm_columns():
    from koipa.db.models import Base  # noqa: PLC0415

    cls_of = {m.local_table.name: m.class_.__name__ for m in Base.registry.mappers}
    rows = []
    for t in Base.metadata.sorted_tables:
        attr_of = {}
        for m in Base.registry.mappers:
            if m.local_table is t:
                for a in m.column_attrs:
                    for c in a.columns:
                        attr_of[c.name] = a.key
        for c in t.columns:
            rows.append({"table": t.name, "cls": cls_of.get(t.name), "col": c.name, "attr": attr_of.get(c.name, c.name),
                         "pk": c.primary_key, "fk": bool(c.foreign_keys), "computed": c.computed is not None,
                         "server_default": c.server_default is not None})
    return rows


def db_signals(cols, prod: Facts):
    attr_tables = collections.defaultdict(set)
    for c in cols:
        attr_tables[c["attr"]].add(c["table"])
    out = []
    for c in cols:
        a, d, cls = c["attr"], c["col"], c["cls"]
        sql_r = sum(1 for _r, s in prod.sql_blobs if c["table"] in s and re.search(rf"(?<![A-Za-z0-9_]){d}(?![A-Za-z0-9_])", s)
                    and not re.search(r"\b(INSERT|UPDATE)\b", s, re.I))
        sql_w = sum(1 for _r, s in prod.sql_blobs if c["table"] in s and re.search(rf"(?<![A-Za-z0-9_]){d}(?![A-Za-z0-9_])", s)
                    and re.search(r"\b(INSERT|UPDATE)\b", s, re.I))
        read = prod.load_attr[a] + prod.strs[a] + (prod.strs[d] if d != a else 0) + sql_r
        write = prod.kw_callee[(cls, a)] + prod.store_attr[a] + sql_w + sum(prod.kw_callee[(x, a)] for x in ("values", "update", "set"))
        out.append({**c, "read": read, "write": write, "shared_name": len(attr_tables[a]) > 1})
    return out


# ── ② 값 흐름 ───────────────────────────────────────────────────────────
def param_flow(cols, prod_files: list[str], caller_files: list[str]):
    cls_cols = collections.defaultdict(dict)
    attr_cols = collections.defaultdict(list)   # 대입(obj.attr = ...) 은 객체 타입을 모르므로 속성 이름으로 후보 표를 모은다
    for c in cols:
        cls_cols[c["cls"]][c["attr"]] = (c["table"], c["col"])
        attr_cols[c["attr"]].append((c["table"], c["col"]))
    calls = collections.defaultdict(list)
    for rel in caller_files:
        tree = _parse(rel)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                nm = n.func.attr if isinstance(n.func, ast.Attribute) else (n.func.id if isinstance(n.func, ast.Name) else None)
                if nm:
                    calls[nm].append((rel, n))
    # 같은 이름의 정의가 여럿이면(upsert·record·create …) 호출자를 이름으로만 골라 남의 호출이 섞인다 — 표시만 해 둔다.
    def_count = collections.Counter()
    for rel in prod_files:
        tree = _parse(rel)
        if tree is not None:
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    def_count[fn.name] += 1
    cls_by_table = {c["table"]: c["cls"] for c in cols}
    found = {}
    for rel in prod_files:
        tree = _parse(rel)
        if tree is None:
            continue
        text = (_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            pos = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
            params = pos + [a.arg for a in fn.args.kwonlyargs]
            sites = []   # (표, 컬럼, 속성, 오른쪽 식)
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    callee = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else "")
                    for k in n.keywords:
                        if callee in cls_cols and k.arg in cls_cols[callee]:
                            t_, c_ = cls_cols[callee][k.arg]
                            sites.append((t_, c_, k.arg, k.value))
                elif isinstance(n, ast.Assign):
                    for tg in n.targets:
                        if isinstance(tg, ast.Attribute) and tg.attr in attr_cols and not (isinstance(tg.value, ast.Name) and tg.value.id == "self"):
                            for t_, c_ in attr_cols[tg.attr]:
                                # 객체 타입을 모르므로, 그 ORM 클래스 이름이 같은 파일에 나오는 표로만 좁힌다
                                if re.search(rf"\b{cls_by_table[t_]}\b", text):
                                    sites.append((t_, c_, tg.attr, n.value))
            for tbl, col, attr, rhs in sites:
                used = sorted({x.id for x in ast.walk(rhs) if isinstance(x, ast.Name) and x.id in params})
                if not used:
                    continue  # 상수·내부 계산 — 함수가 스스로 채운다
                cs = [(r, c) for r, c in calls.get(fn.name, [])
                      # 같은 이름의 다른 함수 호출을 거른다: 이 함수의 매개변수 이름도, 위치 인자도, ** 전개도 없으면 남의 호출
                      if any(kk.arg in params for kk in c.keywords) or len(c.args) >= 1 or any(kk.arg is None for kk in c.keywords)]
                passing = 0
                for _r, c in cs:
                    kw = {kk.arg: kk.value for kk in c.keywords if kk.arg}
                    for u in used:
                        idx = pos.index(u) if u in pos else None
                        if u in kw:
                            if not (isinstance(kw[u], ast.Constant) and kw[u].value is None):
                                passing += 1
                                break
                        elif idx is not None and len(c.args) > idx:
                            passing += 1
                            break
                        elif any(kk.arg is None for kk in c.keywords):
                            passing += 1  # ** 전개는 알 수 없다 — 넘기는 것으로 본다(보수적)
                            break
                if passing == 0:
                    found[(tbl, col)] = {"table": tbl, "col": col, "attr": attr, "func": f"{rel.split('koipa/')[-1]}::{fn.name}",
                                         "params": used, "callers": len(cs),
                                         "verdict": ("호출자 없음" if not cs else "호출자가 안 넘김")
                                         + (f" · 동명 정의 {def_count[fn.name]}개(수동 확인)" if def_count[fn.name] > 1 else "")}
    return list(found.values())


# ── ③ 호출자 없는 정의 ──────────────────────────────────────────────────
def dead_defs(files: list[str]):
    src = [f for f in files if f.startswith("poc/src/koipa/") and f.endswith(".py")]
    scr = [f for f in files if (f.startswith("poc/scripts/") or f.startswith("scripts/")) and f.endswith(".py")]
    tst = [f for f in files if f.startswith("poc/tests/") and f.endswith(".py")]
    defs = {}
    refs = {k: collections.Counter() for k in ("src", "scr", "tst")}

    def collect(rel, tree, bucket, want_defs):
        class V(ast.NodeVisitor):
            def __init__(self):
                self.st = []

            def visit_ClassDef(self, n):
                self.st.append(n.name)
                self.generic_visit(n)
                self.st.pop()

            def visit_FunctionDef(self, n):
                if want_defs:
                    defs[(rel, ".".join(self.st + [n.name]))] = (n.name, n.lineno)
                self.st.append(n.name)
                self.generic_visit(n)
                self.st.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

        V().visit(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                refs[bucket][n.id] += 1
            elif isinstance(n, ast.Attribute):
                refs[bucket][n.attr] += 1
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 80 and n.value.isidentifier():
                refs[bucket][n.value] += 1

    for rel in src:
        t = _parse(rel)
        if t:
            collect(rel, t, "src", True)
    for rel in scr:
        t = _parse(rel)
        if t:
            collect(rel, t, "scr", False)
    for rel in tst:
        t = _parse(rel)
        if t:
            collect(rel, t, "tst", False)

    def framework_called(rel, lineno):
        """데코레이터(여러 줄에 걸친 @router.get(...) 포함)로 프레임워크가 이름으로 부르는 정의인가."""
        lines = (_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        i = lineno - 2
        while i >= 0 and (lines[i].strip() == "" or lines[i].lstrip().startswith(("@", ")", "\"", "'", "summary", "response_model",
                                                                                     "status_code", "dependencies", "tags", "description",
                                                                                     "responses", "operation_id", "name=", "include_in_schema"))
                          or lines[i].rstrip().endswith((",", "("))):
            s = lines[i].strip()
            if s.startswith("@") and any(k in s for k in ("router", "app.", "celery", "task", "validator", "listens_for", "fixture",
                                                          "property", "classmethod", "staticmethod", "abstractmethod",
                                                          "contextmanager", "lru_cache", "signal", "connect")):
                return True
            i -= 1
        return False

    out = []
    for (rel, q), (name, ln) in defs.items():
        if name.startswith("__") or name in ("main", "upgrade", "downgrade"):
            continue
        if refs["src"][name] == 0 and refs["scr"][name] == 0 and not framework_called(rel, ln):
            out.append({"file": rel.replace("poc/src/koipa/", ""), "name": q, "line": ln, "tests": refs["tst"][name]})
    return len(defs), sorted(out, key=lambda x: (x["tests"] == 0, x["file"], x["line"]))


# ── ④⑤ API ─────────────────────────────────────────────────────────────
def api_inventory():
    from koipa.api.app import app  # noqa: PLC0415

    spec = app.openapi()
    comps = spec.get("components", {}).get("schemas", {})

    def refs_in(s, acc):
        if isinstance(s, dict):
            if "$ref" in s:
                acc.add(s["$ref"].split("/")[-1])
            for v in s.values():
                refs_in(v, acc)
        elif isinstance(s, list):
            for v in s:
                refs_in(v, acc)

    def nested(name, seen=None):
        seen = seen if seen is not None else set()
        if name in seen or name not in comps:
            return seen
        seen.add(name)
        acc = set()
        refs_in(comps[name], acc)
        for a in acc:
            nested(a, seen)
        return seen

    ops = {"req": collections.defaultdict(set), "res": collections.defaultdict(set)}
    for path, item in spec["paths"].items():
        for m, op in item.items():
            if m not in ("get", "post", "put", "patch", "delete"):
                continue
            key = f"{m.upper()} {path}"
            if op.get("requestBody"):
                acc = set()
                refs_in(op["requestBody"], acc)
                for a in acc:
                    for n in nested(a):
                        ops["req"][n].add(key)
            for code, r in op.get("responses", {}).items():
                if str(code).startswith("2"):
                    acc = set()
                    refs_in(r, acc)
                    for a in acc:
                        for n in nested(a):
                            ops["res"][n].add(key)
    return comps, ops


KL_OPS = {"GET /api/v1/healthz", "POST /api/v1/classify/async", "GET /api/v1/classify/jobs/{job_id}",
          "GET /api/v1/classify/{doc_id}", "POST /api/v1/documents"}


def api_fields(prod_files: list[str], comps, ops):
    from pydantic import BaseModel  # noqa: PLC0415

    allc = {}

    def collect(c):
        for s in c.__subclasses__():
            allc.setdefault(s.__name__, s)
            collect(s)

    collect(BaseModel)

    def related(name):
        c = allc.get(name)
        if not c:
            return {name}
        out = {name}
        for b in c.__mro__:
            if issubclass(b, BaseModel) and b is not BaseModel:
                out.add(b.__name__)

        def subs(x):
            for s in x.__subclasses__():
                out.add(s.__name__)
                subs(s)

        subs(c)
        return out

    req_classes = sorted(n for n in ops["req"] if not n.startswith("Body_"))
    res_classes = sorted(ops["res"])
    trees = {rel: _parse(rel) for rel in prod_files}
    trees = {k: v for k, v in trees.items() if v is not None}

    # 요청 필드: 타입 주석으로 따라간 속성 읽기
    reads = collections.defaultdict(lambda: collections.defaultdict(set))
    for rel, tree in trees.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            tracked = {}
            for a in list(fn.args.args) + list(fn.args.kwonlyargs):
                t = ast.unparse(a.annotation) if a.annotation is not None else ""
                for cname in req_classes:
                    if re.search(rf"(?<![A-Za-z0-9_]){re.escape(cname)}(?![A-Za-z0-9_])", t):
                        tracked.setdefault(a.arg, set()).add(cname)
            if not tracked:
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in tracked and isinstance(n.ctx, ast.Load):
                    for cname in tracked[n.value.id]:
                        reads[cname][n.attr].add(rel)
    req_zero = []
    for name in req_classes:
        for f in (comps[name].get("properties") or {}):
            hit = set()
            for cc in related(name):
                hit |= reads.get(cc, {}).get(f, set())
            if not hit:
                req_zero.append({"schema": name, "field": f, "ops": sorted(ops["req"][name])[:3], "kl": bool(ops["req"][name] & KL_OPS)})

    # 응답 필드: 생성 지점(생성자 키워드)
    sites = collections.defaultdict(list)
    for rel, tree in trees.items():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                callee = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else "")
                if callee in res_classes:
                    sites[callee].append((rel.split("koipa/")[-1], n.lineno, sorted(k.arg for k in n.keywords if k.arg),
                                          any(k.arg is None for k in n.keywords)))
    per_site = []
    checked = unchecked = 0
    for name in res_classes:
        fields = list((comps[name].get("properties") or {}))
        ss = [s for cc in related(name) for s in sites.get(cc, [])]
        if not ss or any(s[3] for s in ss):
            unchecked += len(fields)
            continue
        checked += len(fields)
        if len(ss) >= 2:
            union = set().union(*[set(s[2]) for s in ss])
            if union != set.intersection(*[set(s[2]) for s in ss]):
                for s in ss:
                    miss = sorted(set(fields) - set(s[2]))
                    if miss and len(fields) >= 6:
                        per_site.append({"schema": name, "site": f"{s[0]}:{s[1]}", "sets": len(s[2]), "not_set": miss,
                                         "kl": bool(ops["res"][name] & KL_OPS)})
    return req_zero, per_site, checked, unchecked


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="후보 목록을 JSON 으로 저장")
    args = ap.parse_args(argv)

    files = _files()
    prod_files = [f for f in files if f.startswith("poc/src/koipa/") and f.endswith(".py") and f not in _DEFINITIONS]
    caller_files = prod_files + [f for f in files if (f.startswith("poc/scripts/") or f.startswith("scripts/")) and f.endswith(".py")]
    prod = Facts(prod_files)

    cols = orm_columns()
    sig = db_signals(cols, prod)
    flow = param_flow(cols, prod_files, caller_files)
    n_defs, dead = dead_defs(files)
    comps, ops = api_inventory()
    req_zero, per_site, res_checked, res_unchecked = api_fields(prod_files, comps, ops)

    print("=" * 78)
    print(" ① DB 컬럼 — 표 단위 읽기·쓰기 신호 (제품코드)")
    print("=" * 78)
    print(f"  표 {len({c['table'] for c in cols})}개 · 컬럼 {len(cols)}개 (ORM 기준 · 이름이 겹치는 속성 {sum(1 for c in sig if c['shared_name'])}개 컬럼 포함)")
    zero = [c for c in sig if c["read"] == 0 and c["write"] == 0 and not c["pk"]]
    print(f"  읽기·쓰기 신호 둘 다 0: {len(zero)}개")
    for c in zero:
        print(f"    {c['table']:<28}{c['col']:<24}{c['attr']}")
    no_read = [c for c in sig if c["read"] == 0 and c["write"] > 0 and not c["pk"]]
    print(f"  쓰기만 있고 읽기 0: {len(no_read)}개")
    for c in no_read:
        print(f"    {c['table']:<28}{c['col']:<24}{c['attr']}")

    print()
    print("=" * 78)
    print(" ② 값 흐름 — 컬럼을 채우는 값이 매개변수인데 호출자가 안 넘기거나 호출자가 없다")
    print("=" * 78)
    for r in sorted(flow, key=lambda x: (x["table"], x["col"])):
        print(f"    {r['table']:<28}{r['col']:<24}{r['verdict']} ({r['callers']}곳) {r['func']} 인자={r['params']}")
    print(f"  {len(flow)}개 컬럼")

    print()
    print("=" * 78)
    print(" ③ 호출자 없는 정의 — 제품코드·스크립트가 이름을 한 번도 안 부른다")
    print("=" * 78)
    only_tests = [d for d in dead if d["tests"] > 0]
    nobody = [d for d in dead if d["tests"] == 0]
    print(f"  {len(dead)}건 / 정의 {n_defs}개 (시험만 부름 {len(only_tests)} · 시험도 안 부름 {len(nobody)})")
    for d in dead:
        print(f"    {d['file']}:{d['line']}  {d['name']}  (시험 {d['tests']})")

    print()
    print("=" * 78)
    print(" ④ API 응답 항목 — 생성 지점마다 다르게 채우는 스키마(필드 6개 이상)")
    print("=" * 78)
    print(f"  판정한 응답 필드 {res_checked}개 · 판정 못 한 필드 {res_unchecked}개(딕셔너리·**전개·생성 지점 없음)")
    for r in per_site:
        print(f"    {'KL ' if r['kl'] else '   '}{r['schema']:<26}{r['site']:<40}채움 {r['sets']}개  못 채움 {len(r['not_set'])}: {r['not_set'][:10]}")

    print()
    print("=" * 78)
    print(" ⑤ API 요청 항목 — 타입 주석으로 따라간 읽기 0 (후보 · 연쇄 접근을 못 따라가 과하게 나온다)")
    print("=" * 78)
    for r in req_zero:
        print(f"    {'KL ' if r['kl'] else '   '}{r['schema']:<30}{r['field']:<24}{r['ops']}")
    print(f"  {len(req_zero)}개")

    print()
    print("  주의 — 위는 후보다. 지우기 전에 각 항목을 열어 보고(연쇄 접근·`git log -S<이름>` 의 남긴 이유),")
    print("  KL 이 받은 규격에 든 항목은 규격 변경으로 다뤄야 한다.")

    if args.json:
        Path(args.json).write_text(json.dumps({"db_zero": zero, "db_write_only": no_read, "param_flow": flow, "dead_defs": dead,
                                               "api_per_site": per_site, "api_req_zero": req_zero}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
