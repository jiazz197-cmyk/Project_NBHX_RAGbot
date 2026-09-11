# Code Review: pdm_bom.py MODEL 查询重构

**审查日期**：2026-05-31
**审查文件**：`app/integrations/sqlserver/pdm_bom.py`
**审查范围**：`build_model_filter_clauses` / `query_pdm_bom_merged` 的未提交改动

---

## 改动概述

本次改动重构了 `query_pdm_bom_merged` 的 MODEL 查询策略：

1. 删除了 `build_model_filter_clauses` 中的 `NOT LIKE '%{model}[a-zA-Z]%'` 排除条款
2. 将单次 SQL 查询改为 CTE + UNION ALL 实现"MODEL 优先匹配，无匹配时回退到不限 MODEL 的结果"
3. `query_pdm_bom_merged` 绕过了 `build_pdm_and_where_clause` 的 model 参数，改为内联拼 model 条件

---

## CRITICAL 1：删除 `NOT LIKE` 导致 MODEL 匹配精度退化

**状态**：✅ 已修复

**位置**：`build_model_filter_clauses` L46（旧代码 L47 被删除）

### 旧代码

```python
clauses.append(f"a.MODEL LIKE '%{safe_model}%'")
clauses.append(f"a.MODEL NOT LIKE '%{safe_model}[a-zA-Z]%'")  # ← 被删了
```

### 问题

SQL Server LIKE 的 `[a-zA-Z]` 是字符类语法。旧代码用 `NOT LIKE '%ADW-A-0314S[a-zA-Z]%'` 排除 MODEL 字段中 model 值后面紧跟字母的情况。

删除后，`model="ADW-A-0314S"` 会匹配到 `ADW-A-0314SX`、`ADW-A-0314SA` 等错误型号的零件，直接影响报价准确性。

### 修复

已在 `build_model_filter_clauses` 中恢复 `NOT LIKE` 子句，并将 `query_pdm_bom_merged` 改回通过 `build_pdm_and_where_clause(alts, model=model)` 拼条件（不再绕过该 helper 自己内联），自动同步生效。

---

## ~~CRITICAL 2：model 不命中时静默回退~~

**状态**：⛔ 业务侧故意行为，**不修改**

`UNION ALL ... WHERE NOT EXISTS` 的"静默回退到无 MODEL 结果"是上层 quotation pipeline 期望的行为：当用户传入的 model 在 PDM 库里查不到任何对应零件时，回退到不限型号的 keyword 命中，用作兜底候选。新实现保留该语义（见下文 WARNING 修复方案）。

---

## WARNING：CTE + UNION ALL 强制双次扫描 BOM_027

**状态**：✅ 已修复

**位置**：`query_pdm_bom_merged` L207-230

### 问题

SQL Server 不保证对 sibling CTE 短路求值。即使 `model_matched` 有结果，`no_model` CTE 的扫描（含 `SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID` 相关子查询）也会执行。**常见路径（model 命中）的查询成本从 1 次扫描变成 2 次扫描**。

### 修复

改成 Python 两步查询：

```python
def _run(where_clause: str, tag: str) -> List[Dict[str, Any]]:
    query_sql = f"""
        SELECT DISTINCT a.PARTID, a.CHINANAME
        FROM BOM_027 a
        WHERE a.PARTVAR = (SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID)
          AND {where_clause}
        ORDER BY a.PARTID
    """
    return client.query(query_sql)

# 先尝试带 MODEL
where_with_model = build_pdm_and_where_clause(alternatives_per_keyword, model=model)
rows = _run(where_with_model, "带 MODEL" if model else "无 MODEL")

# 命中 0 行且存在 MODEL 时，静默回退到无 MODEL（业务侧故意行为）
if not rows and model:
    where_no_model = build_pdm_and_where_clause(alternatives_per_keyword, model=None)
    rows = _run(where_no_model, "MODEL 回退")
```

- **常见路径（model 命中）**：1 次 SQL，1 次 BOM_027 扫描
- **罕见路径（model 不命中触发回退）**：2 次 SQL（旧 CTE 方案也是 2 次扫描，成本相同）
- 保留 CRITICAL 2 中确认的静默回退语义

---

## ~~SUGGESTION：`build_pdm_and_where_clause` 的 `model` 参数变成死代码~~

**状态**：✅ 顺带修复

WARNING 修复中恢复了 `build_pdm_and_where_clause(alts, model=model)` 的使用，参数自动复活，L131-133 内部分支不再死代码。

---

## ~~SUGGESTION：两个 CTE 内的 base SELECT 块逐字重复~~

**状态**：✅ 顺带修复

WARNING 修复中三处 SQL 模板合并为 `_run(where_clause, tag)` 一处，模板重复消除。

---

## 优先级总结

| 优先级 | 编号 | 问题 | 状态 |
|--------|------|------|------|
| 🔴 CRITICAL | 1 | NOT LIKE 被删 → `ADW-A-0314S` 误匹配 `ADW-A-0314SX` | ✅ 已修复 |
| 🔴 CRITICAL | 2 | model 静默回退 | ⛔ 业务故意，保留 |
| 🟡 WARNING | — | CTE 双扫描 → 常见路径 BOM_027 扫描翻倍 | ✅ 已修复（Python 两步） |
| 💡 SUGGESTION | — | `build_pdm_and_where_clause` 的 `model` 参数死代码 | ✅ 顺带修复 |
| 💡 SUGGESTION | — | 三处 base SELECT 模板重复 | ✅ 顺带修复 |
