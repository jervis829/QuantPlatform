"""Deterministic OpenAPI 3.1 JSON Schema -> TypeScript interface generator (P11)."""

from __future__ import annotations

from typing import Any


def _schema_to_ts_type(prop: dict[str, Any]) -> str:
    if "$ref" in prop:
        return prop["$ref"].split("/")[-1]
    if "anyOf" in prop:
        parts = [_schema_to_ts_type(sub) for sub in prop["anyOf"]]
        dedup = list(dict.fromkeys(parts))
        return " | ".join(dedup)
    ptype = prop.get("type")
    if ptype == "string":
        if "enum" in prop:
            return " | ".join(f'"{v}"' for v in prop["enum"])
        return "string"
    if ptype in {"integer", "number"}:
        return "number"
    if ptype == "boolean":
        return "boolean"
    if ptype == "null":
        return "null"
    if ptype == "array":
        item_ts = _schema_to_ts_type(prop.get("items", {}))
        return f"Array<{item_ts}>"
    if ptype == "object":
        add_props = prop.get("additionalProperties")
        if isinstance(add_props, dict):
            return f"Record<string, {_schema_to_ts_type(add_props)}>"
        return "Record<string, unknown>"
    return "unknown"


def generate_typescript_types_from_openapi(openapi_doc: dict[str, Any]) -> str:
    """Render all OpenAPI `components.schemas` + endpoint route map as strict TypeScript types."""
    lines: list[str] = [
        "/* AUTO-GENERATED FROM FastAPI OpenAPI Schema — DO NOT EDIT MANUALLY */",
        "",
    ]
    schemas: dict[str, Any] = openapi_doc.get("components", {}).get("schemas", {})
    for name in sorted(schemas.keys()):
        schema = schemas[name]
        required = set(schema.get("required", []))
        props: dict[str, Any] = schema.get("properties", {})
        lines.append(f"export interface {name} {{")
        for pname, pdef in props.items():
            opt = "" if pname in required else "?"
            ts_t = _schema_to_ts_type(pdef)
            lines.append(f"  {pname}{opt}: {ts_t};")
        lines.append("}")
        lines.append("")

    paths = sorted(openapi_doc.get("paths", {}).keys())
    lines.append("export type ApiEndpointPath =")
    for idx, p in enumerate(paths):
        suffix = ";" if idx == len(paths) - 1 else ""
        lines.append(f'  | "{p}"{suffix}')
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    from pathlib import Path
    import sys

    from quant_platform.api.app import create_app

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("frontend/src/types/openapi.generated.ts")
    app_inst = create_app(db_url="sqlite:///:memory:")
    target.write_text(generate_typescript_types_from_openapi(app_inst.openapi()), encoding="utf-8")

