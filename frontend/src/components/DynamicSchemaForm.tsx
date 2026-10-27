"use client";

import React, { useEffect, useState } from "react";

interface SchemaProperty {
  type?: string;
  format?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  title?: string;
}

interface DynamicSchemaFormProps {
  schema: Record<string, unknown>;
  initialValues: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => Promise<void>;
}

export function DynamicSchemaForm({
  schema,
  initialValues,
  onSubmit,
}: DynamicSchemaFormProps) {
  const properties = (schema?.properties || {}) as Record<string, SchemaProperty>;
  const [values, setValues] = useState<Record<string, unknown>>(initialValues);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValues(initialValues);
  }, [initialValues]);

  const keys = Object.keys(properties);
  if (keys.length === 0) {
    return <p style={{ color: "#94a3b8" }}>当前策略无自定义 Pydantic 参数（ParamModel 为空）。</p>;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      await onSubmit(values);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ display: "grid", gap: "10px" }}>
      {keys.map((key) => {
        const prop = properties[key];
        const val = values[key] ?? prop.default ?? "";
        const isBool = prop.type === "boolean";
        const isInt = prop.type === "integer";
        return (
          <label
            key={key}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              background: "#0f172a",
              padding: "8px 12px",
              borderRadius: "6px",
              border: "1px solid #1e293b",
            }}
          >
            <span>
              <strong>{key}</strong>{" "}
              <small style={{ color: "#64748b" }}>
                ({prop.format || prop.type || "string"})
              </small>
            </span>
            {isBool ? (
              <input
                type="checkbox"
                checked={Boolean(val)}
                onChange={(ev) => setValues({ ...values, [key]: ev.target.checked })}
              />
            ) : (
              <input
                type={isInt ? "number" : "text"}
                value={String(val)}
                min={prop.minimum}
                max={prop.maximum}
                onChange={(ev) =>
                  setValues({
                    ...values,
                    [key]: isInt ? Number(ev.target.value) : ev.target.value,
                  })
                }
                style={{
                  background: "#1e293b",
                  color: "#f8fafc",
                  border: "1px solid #334155",
                  borderRadius: "4px",
                  padding: "4px 8px",
                  width: "180px",
                }}
              />
            )}
          </label>
        );
      })}
      <button
        type="submit"
        disabled={saving}
        style={{
          background: "#2563eb",
          color: "#fff",
          border: "none",
          borderRadius: "6px",
          padding: "8px 14px",
          cursor: "pointer",
          fontWeight: 600,
        }}
      >
        {saving ? "正在生成新参数版本..." : "保存参数（自动生成新版本 + Diff + 审计记录）"}
      </button>
    </form>
  );
}
