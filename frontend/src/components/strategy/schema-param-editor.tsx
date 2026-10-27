"use client";

import React, { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, GitCompare, RotateCcw, Save } from "lucide-react";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

interface JsonSchemaProperty {
  title?: string;
  description?: string;
  type?: string;
  anyOf?: Array<{ type?: string }>;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
}

type FieldGroupKey = "signal" | "risk_sizing" | "general";

interface ParsedSchemaField {
  key: string;
  title: string;
  description: string;
  fieldType: "integer" | "number" | "boolean" | "string";
  required: boolean;
  defaultValue: unknown;
  min?: number;
  max?: number;
  exclusiveMin?: number;
  exclusiveMax?: number;
  unitHint: string;
  group: FieldGroupKey;
  isDangerParam: boolean;
}

function classifyFieldGroup(key: string): {
  group: FieldGroupKey;
  isDangerParam: boolean;
  unitHint: string;
} {
  const lower = key.toLowerCase();
  if (
    /(qty|quantity|size|leverage|notional|stop|drawdown|max_|limit|cap|margin|weight)/.test(
      lower
    )
  ) {
    let unitHint = "";
    if (lower.includes("leverage")) unitHint = "x (倍)";
    else if (lower.includes("qty") || lower.includes("quantity") || lower.includes("size"))
      unitHint = "基础资产数量";
    else if (lower.includes("notional") || lower.includes("usdt") || lower.includes("loss"))
      unitHint = "USDT";
    else if (lower.includes("pct") || lower.includes("drawdown") || lower.includes("stop"))
      unitHint = "% / 比例";
    return { group: "risk_sizing", isDangerParam: true, unitHint };
  }

  if (/(window|period|lookback|fast|slow|ema|sma|rsi|zscore|threshold|bar)/.test(lower)) {
    const unitHint = /(window|period|lookback|fast|slow|bar)/.test(lower) ? "Bars (K线根数)" : "";
    return { group: "signal", isDangerParam: false, unitHint };
  }

  return { group: "general", isDangerParam: false, unitHint: "" };
}

function parsePydanticJsonSchema(schema: Record<string, unknown>): ParsedSchemaField[] {
  const props = (schema?.properties ?? {}) as Record<string, JsonSchemaProperty>;
  const reqSet = new Set<string>(
    Array.isArray(schema?.required) ? (schema.required as string[]) : []
  );

  return Object.entries(props).map(([key, prop]) => {
    const anyOfTypes = (prop.anyOf ?? []).map((x) => x.type).filter(Boolean);
    const rawType = prop.type ?? anyOfTypes[0] ?? "string";
    let fieldType: ParsedSchemaField["fieldType"] = "string";
    if (rawType === "integer") fieldType = "integer";
    else if (rawType === "number") fieldType = "number";
    else if (rawType === "boolean") fieldType = "boolean";

    const { group, isDangerParam, unitHint } = classifyFieldGroup(key);
    return {
      key,
      title: prop.title || key,
      description: prop.description || "",
      fieldType,
      required: reqSet.has(key),
      defaultValue: prop.default,
      min: prop.minimum,
      max: prop.maximum,
      exclusiveMin: prop.exclusiveMinimum,
      exclusiveMax: prop.exclusiveMaximum,
      unitHint,
      group,
      isDangerParam,
    };
  });
}

function stringifyVal(val: unknown): string {
  if (val === undefined || val === null) return "";
  return String(val);
}

export interface SchemaParamEditorProps {
  strategyId: string;
  versionNumber: number;
  schema: Record<string, unknown>;
  savedParams: Record<string, unknown>;
  isSaving?: boolean;
  canEdit?: boolean;
  onSaveNewVersion: (nextParams: Record<string, unknown>) => Promise<void> | void;
}

export function SchemaParamEditor({
  versionNumber,
  schema,
  savedParams,
  isSaving = false,
  canEdit = true,
  onSaveNewVersion,
}: SchemaParamEditorProps) {
  const fields = useMemo(() => parsePydanticJsonSchema(schema), [schema]);
  const [draftValues, setDraftValues] = useState<Record<string, string>>({});

  useEffect(() => {
    const init: Record<string, string> = {};
    fields.forEach((f) => {
      const saved = savedParams[f.key] ?? f.defaultValue ?? "";
      init[f.key] = stringifyVal(saved);
    });
    // Also preserve any extra keys present in savedParams
    Object.entries(savedParams).forEach(([k, v]) => {
      if (!(k in init)) init[k] = stringifyVal(v);
    });
    setDraftValues(init);
  }, [fields, savedParams]);

  // Field-level validation
  const validationErrors = useMemo(() => {
    const errs: Record<string, string> = {};
    fields.forEach((f) => {
      const raw = (draftValues[f.key] ?? "").trim();
      if (raw === "") {
        if (f.required || f.defaultValue === undefined) {
          errs[f.key] = "必填参数，不能为空";
        }
        return;
      }
      if (f.fieldType === "integer" || f.fieldType === "number") {
        const num = Number(raw);
        if (!Number.isFinite(num)) {
          errs[f.key] = "请输入有效数值";
          return;
        }
        if (f.fieldType === "integer" && !Number.isInteger(num)) {
          errs[f.key] = "必须为整数";
          return;
        }
        if (f.min !== undefined && num < f.min) {
          errs[f.key] = `不得小于最小值 ${f.min}`;
          return;
        }
        if (f.max !== undefined && num > f.max) {
          errs[f.key] = `不得大于最大值 ${f.max}`;
          return;
        }
        if (f.exclusiveMin !== undefined && num <= f.exclusiveMin) {
          errs[f.key] = `必须严格大于 ${f.exclusiveMin}`;
          return;
        }
        if (f.exclusiveMax !== undefined && num >= f.exclusiveMax) {
          errs[f.key] = `必须严格小于 ${f.exclusiveMax}`;
          return;
        }
      }
    });
    return errs;
  }, [draftValues, fields]);

  // Compute live diff against savedParams
  const diffEntries = useMemo(() => {
    return fields
      .map((f) => {
        const oldStr = stringifyVal(savedParams[f.key] ?? f.defaultValue);
        const newStr = (draftValues[f.key] ?? "").trim();
        if (oldStr === newStr) return null;

        let pctDelta: number | null = null;
        const oldNum = Number(oldStr);
        const newNum = Number(newStr);
        if (Number.isFinite(oldNum) && Number.isFinite(newNum) && oldNum !== 0) {
          pctDelta = ((newNum - oldNum) / Math.abs(oldNum)) * 100;
        }
        return {
          field: f,
          oldStr,
          newStr,
          pctDelta,
          amplifiesRisk:
            f.isDangerParam &&
            Number.isFinite(oldNum) &&
            Number.isFinite(newNum) &&
            newNum > oldNum,
        };
      })
      .filter((x): x is NonNullable<typeof x> => x !== null);
  }, [draftValues, fields, savedParams]);

  const dangerDiffs = diffEntries.filter((d) => d.field.isDangerParam);
  const hasErrors = Object.keys(validationErrors).length > 0;

  const handleReset = () => {
    const resetMap: Record<string, string> = {};
    fields.forEach((f) => {
      resetMap[f.key] = stringifyVal(savedParams[f.key] ?? f.defaultValue);
    });
    setDraftValues(resetMap);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (hasErrors || diffEntries.length === 0 || !canEdit) return;
    const payload: Record<string, unknown> = {};
    fields.forEach((f) => {
      const raw = (draftValues[f.key] ?? "").trim();
      if (f.fieldType === "integer") {
        payload[f.key] = parseInt(raw, 10);
      } else if (f.fieldType === "number") {
        payload[f.key] = Number(raw);
      } else if (f.fieldType === "boolean") {
        payload[f.key] = raw === "true";
      } else {
        // Keep Decimal-compatible string representation
        payload[f.key] = raw;
      }
    });
    await onSaveNewVersion(payload);
  };

  const groups: Array<{ id: FieldGroupKey; title: string; subtitle: string }> = [
    {
      id: "signal",
      title: "信号与周期参数",
      subtitle: "决定均线窗口、回看周期与开平仓触发阈值",
    },
    {
      id: "risk_sizing",
      title: "仓位与风控敏感参数",
      subtitle: "直接影响单笔委托名义额、保证金占用与强平距离（修改后高亮提示）",
    },
    {
      id: "general",
      title: "通用策略参数",
      subtitle: "其余 Pydantic Schema 定义项（含 Decimal 严格精度字段）",
    },
  ];

  return (
    <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      {/* Left 7 Cols: Schema-Driven Grouped Form */}
      <div className="space-y-4 lg:col-span-7">
        {fields.length === 0 ? (
          <div className="rounded-lg border border-border bg-card p-6 text-xs text-muted-foreground">
            该策略版本未声明可配置 Pydantic `ParamModel` 参数。
          </div>
        ) : (
          groups.map((grp) => {
            const grpFields = fields.filter((f) => f.group === grp.id);
            if (grpFields.length === 0) return null;
            return (
              <div
                key={grp.id}
                className={cn(
                  "rounded-lg border p-4 space-y-3",
                  grp.id === "risk_sizing"
                    ? "border-amber-500/40 bg-amber-950/10"
                    : "border-border bg-card"
                )}
              >
                <div className="border-b border-border pb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="text-xs font-semibold text-white">{grp.title}</h3>
                    {grp.id === "risk_sizing" && (
                      <span className="rounded bg-amber-500/20 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-amber-300">
                        资金敞口敏感
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground">{grp.subtitle}</p>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {grpFields.map((f) => {
                    const currentVal = draftValues[f.key] ?? "";
                    const savedVal = stringifyVal(savedParams[f.key] ?? f.defaultValue);
                    const isDirty = currentVal.trim() !== savedVal;
                    const errMsg = validationErrors[f.key];

                    const rangeParts: string[] = [];
                    if (f.min !== undefined) rangeParts.push(`min: ${f.min}`);
                    if (f.exclusiveMin !== undefined) rangeParts.push(`> ${f.exclusiveMin}`);
                    if (f.max !== undefined) rangeParts.push(`max: ${f.max}`);
                    if (f.exclusiveMax !== undefined) rangeParts.push(`< ${f.exclusiveMax}`);

                    return (
                      <div key={f.key} className="space-y-1">
                        <div className="flex items-center justify-between gap-1 text-xs">
                          <label className="font-medium text-slate-200">
                            {f.key}
                            {(f.required || f.defaultValue === undefined) && (
                              <span className="ml-0.5 text-rose-400">*</span>
                            )}
                            {isDirty && (
                              <span className="ml-1.5 rounded bg-blue-500/20 px-1 py-0.2 font-mono text-[10px] text-blue-300">
                                已修改
                              </span>
                            )}
                          </label>
                          {f.unitHint && (
                            <span className="font-mono text-[10px] text-slate-400">
                              {f.unitHint}
                            </span>
                          )}
                        </div>

                        {f.fieldType === "boolean" ? (
                          <select
                            disabled={!canEdit}
                            value={currentVal}
                            onChange={(e) =>
                              setDraftValues((prev) => ({ ...prev, [f.key]: e.target.value }))
                            }
                            className="h-8 w-full rounded border border-input bg-slate-950 px-2.5 font-mono text-xs text-white focus:border-blue-500 focus:outline-none"
                          >
                            <option value="true">true</option>
                            <option value="false">false</option>
                          </select>
                        ) : (
                          <input
                            type="text"
                            disabled={!canEdit}
                            value={currentVal}
                            onChange={(e) =>
                              setDraftValues((prev) => ({ ...prev, [f.key]: e.target.value }))
                            }
                            className={cn(
                              "h-8 w-full rounded border bg-slate-950 px-2.5 font-mono text-xs text-white focus:outline-none",
                              errMsg
                                ? "border-rose-500 focus:border-rose-400"
                                : isDirty
                                  ? "border-blue-500/80 focus:border-blue-400"
                                  : "border-input focus:border-blue-500"
                            )}
                          />
                        )}

                        <div className="flex flex-wrap items-center justify-between gap-1 font-mono text-[10px] text-slate-400">
                          <span>
                            默认: {f.defaultValue !== undefined ? String(f.defaultValue) : "必填"}
                          </span>
                          {rangeParts.length > 0 && <span>范围 [{rangeParts.join(", ")}]</span>}
                        </div>

                        {f.description && (
                          <p className="text-[11px] text-slate-400">{f.description}</p>
                        )}
                        {errMsg && (
                          <p className="text-[11px] font-medium text-rose-400">{errMsg}</p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Right 5 Cols: Real-time Before/After Diff against Saved Version + Warnings */}
      <div className="space-y-3 lg:col-span-5">
        <div className="sticky top-16 rounded-lg border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-border pb-2.5">
            <div className="flex items-center gap-2">
              <GitCompare className="h-4 w-4 text-sky-400" />
              <h3 className="text-xs font-semibold text-white">
                与已保存版本 (v{versionNumber}) 实时参数对比
              </h3>
            </div>
            <span
              className={cn(
                "rounded px-2 py-0.5 font-mono text-[10px] font-semibold",
                diffEntries.length > 0
                  ? "bg-amber-500/20 text-amber-300"
                  : "bg-slate-800 text-slate-400"
              )}
            >
              {diffEntries.length > 0 ? `${diffEntries.length} 项未保存变更` : "与已保存版本一致"}
            </span>
          </div>

          {/* Explicit Danger Parameter Warning */}
          {dangerDiffs.length > 0 && (
            <div className="rounded-md border border-amber-500/50 bg-amber-950/35 p-3 text-xs text-amber-200 space-y-1">
              <div className="flex items-center gap-1.5 font-semibold text-amber-300">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <span>检测到仓位 / 风控敏感参数变更</span>
              </div>
              <p className="text-[11px] leading-relaxed text-amber-100/90">
                您修改了{" "}
                <code className="font-mono font-bold">
                  {dangerDiffs.map((d) => d.field.key).join(", ")}
                </code>
                {dangerDiffs.some((d) => d.amplifiesRisk)
                  ? "（且数值被放大，将直接增加单笔名义敞口与保证金占用）"
                  : ""}
                。保存后系统将自动生成新版本{" "}
                <strong className="font-mono">v{versionNumber + 1}</strong>{" "}
                并置为 <strong className="font-mono">PENDING</strong>，需人工重新审批后方可进入影子或实盘（红线 8）。
              </p>
            </div>
          )}

          {diffEntries.length === 0 ? (
            <div className="flex items-center gap-2 rounded-md border border-dashed border-slate-800 bg-slate-950/60 p-4 text-xs text-slate-400">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              <span>当前表单参数与已保存版本 v{versionNumber} 完全一致。修改左侧任意字段即可实时预览前后对比。</span>
            </div>
          ) : (
            <div className="space-y-2.5">
              {/* Structured Field Comparison Table */}
              <div className="overflow-hidden rounded border border-slate-800">
                <table className="w-full text-left font-mono text-[11px]">
                  <thead className="bg-slate-900 text-slate-400">
                    <tr>
                      <th className="px-2.5 py-1.5">参数</th>
                      <th className="px-2.5 py-1.5 text-right">已保存 (v{versionNumber})</th>
                      <th className="px-2.5 py-1.5 text-right">新草稿 (v{versionNumber + 1})</th>
                      <th className="px-2.5 py-1.5 text-right">变动</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diffEntries.map((d) => (
                      <tr key={d.field.key} className="border-t border-slate-800/80">
                        <td className="px-2.5 py-1.5 font-semibold text-slate-200">
                          {d.field.key}
                        </td>
                        <td className="px-2.5 py-1.5 text-right text-rose-300 line-through">
                          {d.oldStr}
                        </td>
                        <td className="px-2.5 py-1.5 text-right font-bold text-emerald-300">
                          {d.newStr}
                        </td>
                        <td className="px-2.5 py-1.5 text-right text-slate-300">
                          {d.pctDelta !== null
                            ? `${d.pctDelta > 0 ? "+" : ""}${d.pctDelta.toFixed(1)}%`
                            : "变更"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Unified Patch Preview */}
              <pre className="rounded border border-slate-800 bg-slate-950 p-2.5 font-mono text-[11px] overflow-x-auto leading-relaxed">
                <div className="text-slate-500">
                  --- params@v{versionNumber} (saved)
                </div>
                <div className="text-slate-500">
                  +++ params@v{versionNumber + 1} (unsaved_draft)
                </div>
                {diffEntries.map((d) => (
                  <React.Fragment key={`${d.field.key}_patch`}>
                    <div className="bg-rose-950/40 text-rose-300">
                      - &quot;{d.field.key}&quot;: {d.oldStr}
                    </div>
                    <div className="bg-emerald-950/40 text-emerald-300">
                      + &quot;{d.field.key}&quot;: {d.newStr}
                    </div>
                  </React.Fragment>
                ))}
              </pre>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={diffEntries.length === 0 || isSaving}
              onClick={handleReset}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              <span>放弃修改</span>
            </Button>
            <Button
              type="submit"
              variant="default"
              size="sm"
              disabled={!canEdit || hasErrors || diffEntries.length === 0 || isSaving}
            >
              <Save className="h-3.5 w-3.5" />
              <span>
                {isSaving
                  ? "保存中..."
                  : `保存为新版本 v${versionNumber + 1}`}
              </span>
            </Button>
          </div>
          {!canEdit && (
            <p className="text-[11px] text-amber-300">
              当前角色为只读 VIEWER，切换至 RESEARCHER / OPERATOR / ADMIN 可修改保存参数版本。
            </p>
          )}
        </div>
      </div>
    </form>
  );
}
