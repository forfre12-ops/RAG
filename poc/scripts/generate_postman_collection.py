"""P1-E1: OpenAPI yaml → Postman Collection v2.1 자동 변환.

KL 측 통합 시간 단축 목적. CI에서 main push 시 산출하여 artifact 게시.

사용:
  python scripts/generate_postman_collection.py \
      --spec doc/03_openapi_lloydk_kl.yaml \
      --output dist/lloydk_kl_postman.json \
      --base-url https://api.lloydk.example.com
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    print("PyYAML 필요: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def _example_body(schema: dict, components: dict) -> dict:
    """OpenAPI schema에서 example 또는 sample 본문 생성."""
    if not isinstance(schema, dict):
        return {}
    if "example" in schema:
        return schema["example"]
    if "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        target = components.get("schemas", {}).get(ref, {})
        return _example_body(target, components)
    if schema.get("type") == "object" or "properties" in schema:
        out = {}
        for k, v in (schema.get("properties") or {}).items():
            out[k] = _example_field(v, components)
        return out
    return {}


def _example_field(schema: dict, components: dict):
    if "$ref" in schema:
        return _example_body(schema, components)
    t = schema.get("type")
    if t == "string":
        if schema.get("format") == "date-time":
            return "2026-05-29T12:00:00Z"
        return schema.get("example") or schema.get("enum", ["string"])[0]
    if t == "integer" or t == "number":
        return 0
    if t == "boolean":
        return True
    if t == "array":
        return [_example_field(schema.get("items", {}), components)]
    if t == "object" or "properties" in schema:
        return _example_body(schema, components)
    return None


def convert(spec: dict, base_url: str) -> dict:
    info = spec.get("info", {})
    components = spec.get("components", {})
    paths = spec.get("paths", {})

    items = []
    for path, methods in paths.items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                continue
            name = op.get("summary") or f"{method.upper()} {path}"
            body = None
            request_body = op.get("requestBody", {})
            if request_body:
                content = request_body.get("content", {})
                for mime, mc in content.items():
                    if mime == "application/json":
                        body = _example_body(mc.get("schema", {}), components)
                        break
            full_url = base_url.rstrip("/") + path
            item = {
                "name": name,
                "request": {
                    "method": method.upper(),
                    "header": [
                        {"key": "X-API-Key", "value": "{{api_key}}", "type": "text"},
                        {"key": "Content-Type", "value": "application/json", "type": "text"},
                    ],
                    "url": {
                        "raw": full_url,
                        "host": [base_url.split("//", 1)[-1].split("/", 1)[0]],
                        "path": [p for p in path.split("/") if p],
                    },
                    "description": op.get("description", ""),
                },
            }
            if body:
                item["request"]["body"] = {
                    "mode": "raw",
                    "raw": json.dumps(body, ensure_ascii=False, indent=2),
                    "options": {"raw": {"language": "json"}},
                }
            items.append(item)

    return {
        "info": {
            "name": info.get("title", "Lloydk KL Integration"),
            "description": info.get("description", ""),
            "version": info.get("version", "0.1.0"),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
        "variable": [
            {"key": "api_key", "value": "REPLACE_WITH_KEY"},
            {"key": "base_url", "value": base_url},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="E1: OpenAPI → Postman")
    parser.add_argument("--spec", type=Path, default=Path("doc/03_openapi_lloydk_kl.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="https://api.lloydk.example.com")
    args = parser.parse_args()

    spec = yaml.safe_load(args.spec.read_text(encoding="utf-8"))
    collection = convert(spec, args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Postman collection written: {args.output} ({len(collection['item'])} requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
