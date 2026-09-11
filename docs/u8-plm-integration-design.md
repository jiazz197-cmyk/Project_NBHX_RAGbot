# U8 / PLM 集成与数据依赖设计文档

> **文档目的**：配合大和衡器（上海）U8 与 PLM（PDM）系统升级，说明 Yamato AI 助手平台对 U8 / PDM 的数据依赖、访问方式与兼容性要求，以便升级方在改造库表结构时与开发方协同，保持系统可用性。
>
> **适用范围**：Yamato AI 助手平台后端（FastAPI，`project-yamato-shanghai`）
> **文档版本**：v1.0（2026-07）
> **更新日期**：2026-07-28

---

## 1. 概述

Yamato AI 助手平台的「报价生成」核心链路深度依赖大和既有的两套系统：

| 系统 | 角色 | 数据库 | 平台对它的操作 |
|------|------|--------|----------------|
| **U8（用友 ERP）** | BOM 展开、存货/库存/成本价查询、材料出库价格补充 | SQL Server 库 `UFDATA_888_2016` | **只读**（`SELECT`） |
| **PDM / PLM** | 部件主数据检索、按图纸关键词匹配 PARTID、BOM 层级反查 | SQL Server 库 `pdm78` | **只读**（`SELECT`） |

> ⚠️ **重要前提**：平台对 U8 / PDM **只做只读查询，不执行任何 `INSERT` / `UPDATE` / `DELETE` 写操作**，不修改、不新增 ERP / PLM 中的任何业务数据。升级方对库表结构做变更时，平台侧的风险集中在「读路径字段是否仍然存在 / 语义是否一致」，而非数据一致性。

---

## 2. 访问方式与技术架构

### 2.1 连接方式

- **驱动**：`pymssql`（Python 的 SQL Server 驱动）
- **协议**：TDS（Tabular Data Stream），直连 SQL Server 默认实例端口 `1433`
- **连接配置**（见 `.env.example`）：

  | 配置项 | U8 | PDM |
  |--------|----|-----|
  | 主机 | `U8_SQLSERVER_HOST` | `PDM_SQLSERVER_HOST` |
  | 端口 | `U8_SQLSERVER_PORT`（1433） | `PDM_SQLSERVER_PORT`（1433） |
  | 库名 | `UFDATA_888_2016` | `pdm78` |
  | 账号 | `U8_SQLSERVER_USER` | `PDM_SQLSERVER_USER` |
  | 加密 | `U8_SQLSERVER_ENCRYPT` | `PDM_SQLSERVER_ENCRYPT` |

- **账号权限需求**：仅需对上述库的 **只读（`SELECT`）** 权限，无需写权限、DDL 权限或跨库写入权限。
  > 升级后若更换数据库账号，请确保新账号对下列表/视图具备 `SELECT` 权限。

### 2.2 连接池与高可用机制

- **U8 共享连接池**（`app/adapters/sqlserver/u8_bom.py`）：跨任务复用，配额三层限流：
  - 跨任务并发上限 `U8_BOM_MAX_CONCURRENT_TASKS`（默认 30）
  - 单用户并发上限 `U8_BOM_MAX_CONCURRENT_TASKS_PER_USER`（默认 2）
  - 总连接上限 `U8_BOM_MAX_TOTAL_CONNECTIONS`（默认 64）
  - 连接 age 回收 `SQLSERVER_POOL_RECYCLE_SEC`（默认 1800s，治 idle 连接失效）
- **PDM 单连接 client**：PDM 查询不使用共享池，按需创建连接。
- **启动连通性检查**（`main.py` `_startup_check_sqlserver_connectivity`）：服务启动时对 U8 / PDM 各执行一次 `SELECT 1`，结果记录到 `app.state.sqlserver_connectivity`；**连通失败不阻塞服务启动**（降级运行，报价功能不可用但其余功能正常）。
- **查询超时与熔断**：
  - 查询超时 `SQLSERVER_QUERY_TIMEOUT_SEC`（默认 120s）
  - 登录超时 `SQLSERVER_LOGIN_TIMEOUT_SEC`（默认 30s）
  - 熔断阈值 `SQLSERVER_CB_FAIL_THRESHOLD`（默认 5 次连续失败触发熔断）

### 2.3 SQL 安全说明

- 所有用户输入（零件编码、关键词、机型型号）均通过 **pymssql 参数化查询**（`%s` 占位符）传入，**不拼接进 SQL 文本**，防止 SQL 注入。
- `LIKE` 通配符（`%`、`_`）与字符类（`[a-zA-Z]`）拼在**参数值**内而非 SQL 文本中。
- IN 列表按 `_IN_CLAUSE_BATCH_SIZE = 1000` 分批，规避 SQL Server 单次 2100 参数硬上限（避免大 BOM 触发 8003 错误被静默吞掉）。

---

## 3. PDM（PLM）库 `pdm78` 数据依赖

PDM 库服务于「报价生成 Phase1」（图纸 → 关键词 → PARTID）与「PDM 部件匹配」接口。

### 3.1 使用的表

| 表名 | 用途 | 访问方式 |
|------|------|----------|
| `BOM_027` | 部件主数据（PARTID、中文名、型号、规格） | `SELECT` |
| `BOM_016` | BOM 层级关系（父件→子件 PARTID、装配层级） | `SELECT` |
| `BOM_054` | BOM 状态与名称（PARTID 的 BOMSTATE/BOMNAME） | `LEFT JOIN SELECT` |

### 3.2 BOM_027（部件主数据表）

平台最核心的 PDM 查询表。**升级时此表的字段变更影响最大。**

| 字段 | 类型/用途 | 平台如何使用 |
|------|-----------|--------------|
| `PARTID` | 部件内部 ID（主键） | 关键输出，传给 U8 做 BOM 展开；去重/排序依据 |
| `PARTVAR` | 版本号 | **取每个 PARTID 的最新版本**：`PARTVAR = (SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID)` |
| `CHINANAME` | 部件中文名称 | 按图纸 OCR 关键词做 `LIKE` 匹配；排除「停用/暂停使用/禁用/作废/废弃」 |
| `MODEL` | 机型型号（如 `ADW-A-0314S`） | 精确匹配 + 边界排除（`LIKE '%model%' AND NOT LIKE '%model[a-zA-Z]%'`，防止 `0314S` 误匹配 `0314SX`） |
| `SPEC` | 规格信息 | 打分/展示 |

**典型查询（取最新版本部件）：**

```sql
SELECT DISTINCT
    a.PARTID AS PARTID,
    a.CHINANAME AS CHINANAME
FROM BOM_027 a
WHERE a.PARTVAR = (
    SELECT MAX(b.PARTVAR) FROM BOM_027 b WHERE b.PARTID = a.PARTID
)
AND a.CHINANAME LIKE '%关键词%'
AND a.MODEL LIKE '%ADW-A-0314S%'
AND a.MODEL NOT LIKE '%ADW-A-0314S[a-zA-Z]%'
ORDER BY a.PARTID
```

> **MODEL 回退语义**：当传入的 MODEL 在 PDM 中命中 0 行时，平台会**静默回退到不带 MODEL 的查询**（业务侧故意行为，用作兜底候选）。升级后若 MODEL 字段格式或填充规则变化，需确认此回退仍可接受。

### 3.3 BOM_016（BOM 层级关系表）

用于「PDM 部件匹配」Channel 4（BOM 层级反查）与工号子件反查。

| 字段 | 用途 |
|------|------|
| `PARTID` | 子件 PARTID |
| `PARENTID` | 父件 PARTID |
| `ASSEMBLELEVEL` | 装配层级（平台筛选 `<= 2` 或 `<= 3`） |

**典型查询（Channel 4：由子件 PARTID 反查父件）：**

```sql
SELECT DISTINCT b27.PARTID, b27.PARTVAR, b27.CHINANAME, b27.MODEL, b27.SPEC,
       b54.BOMSTATE, b54.BOMNAME,
       MIN(b16.ASSEMBLELEVEL) AS min_asm_level
FROM BOM_016 b16
JOIN BOM_027 b27 ON b16.PARENTID = b27.PARTID
LEFT JOIN BOM_054 b54 ON b27.PARTID = b54.PARTID
WHERE b16.PARTID IN (...)
  AND b16.ASSEMBLELEVEL <= 2
GROUP BY b27.PARTID, b27.PARTVAR, b27.CHINANAME, b27.MODEL, b27.SPEC,
         b54.BOMSTATE, b54.BOMNAME
```

### 3.4 BOM_054（BOM 状态表）

| 字段 | 用途 |
|------|------|
| `PARTID` | 关联 BOM_027.PARTID |
| `BOMSTATE` | BOM 状态（打分参考） |
| `BOMNAME` | BOM 名称（展示/打分参考） |

> 仅 `LEFT JOIN`，缺失不影响主流程（打分降级）。

---

## 4. U8 库 `UFDATA_888_2016` 数据依赖

U8 库服务于「报价生成 Phase2」（PARTID → BOM 展开 → 库存/成本）与「直接 U8 查询」。

### 4.1 使用的表/视图

| 表/视图名 | 类型 | 用途 |
|-----------|------|------|
| `v_bas_part` | 视图 | 物料主数据（PartId ↔ InvCode 映射） |
| `bom_parent` | 表 | BOM 父件关系（ParentId → BomId） |
| `bom_opcomponent` | 表 | BOM 子件明细（ComponentId、用量、损耗） |
| `bom_bom` | 表 | BOM 主表（状态/版本筛选） |
| `Inventory` | 表 | 存货档案（名称、价格、规格、仓库、供应类型） |
| `recordoutlist` | 视图 | 材料出库单（成本价缺失时补充单价） |

### 4.2 v_bas_part（物料主数据视图）

PDM 的 `PARTID` 与 U8 的 `InvCode`（存货编码）之间的**桥梁**。

| 字段 | 用途 |
|------|------|
| `PartId` | 物料内部 ID（与 PDM PARTID 对应） |
| `InvCode` | 物料编码（优先使用） |
| `cInvCode` | 物料编码（备用字段，InvCode 为空时回退） |

**映射逻辑**（平台固定行为）：
```sql
COALESCE(
    NULLIF(LTRIM(RTRIM(vp.InvCode)), ''),
    NULLIF(LTRIM(RTRIM(vp.cInvCode)), '')
) AS PartInvCode
```
> ⚠️ 升级时若此视图字段重命名（如 `InvCode` 改名），平台需同步调整。

### 4.3 bom_bom（BOM 主表）

| 字段 | 用途 |
|------|------|
| `BomId` | BOM 主键 |
| `Status` | BOM 状态（**平台固定筛选 `Status = 3`，即只展开生效 BOM**） |
| `ModifyDate` | 修改日期（多版本时取最新） |
| `ModifyTime` | 修改时间（多版本时取最新） |

### 4.4 bom_parent（BOM 父件关系表）

| 字段 | 用途 |
|------|------|
| `BomId` | 关联 bom_bom.BomId |
| `ParentId` | 父件 PartId（关联 v_bas_part.PartId） |

### 4.5 bom_opcomponent（BOM 子件明细表）

| 字段 | 用途 |
|------|------|
| `BomId` | 关联 bom_bom.BomId |
| `ComponentId` | 子件 PartId（关联 v_bas_part.PartId） |
| `SortSeq` | 排序号 |
| `BaseQtyN` | 基本用量分子 |
| `BaseQtyD` | 基本用量分母 |
| `CompScrap` | 损耗率 |

> 单件用量 = `BaseQtyN / BaseQtyD`（分母为 0 时置 NULL）。

### 4.6 Inventory（存货档案表）

| 字段 | 用途 |
|------|------|
| `cInvCode` | 存货编码（关联 v_bas_part 的 PartInvCode） |
| `cInvName` | 存货名称 |
| `iInvSprice` | 标准价 |
| `iInvNcost` | 成本价（**报价核心价格字段**；缺失时从 recordoutlist 补充） |
| `cInvStd` | 规格型号 |
| `cInvDepCode` | 部门编码 |
| `cDefWareHouse` | 默认仓库 |
| `bForeExpland` | 预展开件标识：`1`=虚拟件（无实际库存），`0`=实际物料（领料），`NULL`=无 Inventory 匹配 |
| `iSupplyType` | 供应类型 |

### 4.7 recordoutlist（材料出库单视图）

当 `Inventory.iInvNcost` 为 NULL/空/0 时，平台自动从 `recordoutlist` 取**最近一次出库单价**补充。

| 字段 | 用途 |
|------|------|
| `cinvcode` | 材料编码（对应 BOM 子件 ChildInvCode） |
| `iunitcost` | 单价（用于补充缺失成本价） |
| `ddate` | 单据日期（取最新记录排序） |
| `autoid` | 自增 ID（排序兜底） |

> 平台使用**跨库全限定名** `UFDATA_888_2016.dbo.recordoutlist` 访问此视图。若升级后库名变更，需同步修改配置或授予跨库访问。

**价格补充查询（取每个编码最新出库单价）：**
```sql
SELECT cinvcode, iunitcost
FROM (
    SELECT cinvcode, iunitcost,
        ROW_NUMBER() OVER (PARTITION BY cinvcode ORDER BY ddate DESC, autoid DESC) AS rn
    FROM UFDATA_888_2016.dbo.recordoutlist
    WHERE cinvcode IN (...)
      AND iunitcost IS NOT NULL AND iunitcost <> 0
) t
WHERE rn = 1
```

### 4.8 U8 BOM 递归展开核心查询

平台在 U8 中递归展开 BOM 子树并关联 Inventory，核心 SQL（`app/adapters/sqlserver/u8_bom.py`）：

```sql
;WITH PartMap AS (
    SELECT vp.PartId,
        COALESCE(NULLIF(LTRIM(RTRIM(vp.InvCode)), ''),
                 NULLIF(LTRIM(RTRIM(vp.cInvCode)), '')) AS PartInvCode,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(NULLIF(LTRIM(RTRIM(vp.InvCode)), ''),
                                  NULLIF(LTRIM(RTRIM(vp.cInvCode)), ''))
            ORDER BY vp.PartId
        ) AS part_rn
    FROM v_bas_part vp
),
LatestBom AS (
    SELECT bp.ParentId, bp.BomId, b.ModifyDate, b.ModifyTime,
        ROW_NUMBER() OVER (
            PARTITION BY bp.ParentId
            ORDER BY b.ModifyDate DESC, b.ModifyTime DESC, b.BomId DESC
        ) AS bom_rn
    FROM bom_parent bp
    JOIN bom_bom b ON b.BomId = bp.BomId AND b.Status = 3
)
SELECT
    parent.PartInvCode AS ParentInvCode,
    child.PartInvCode AS ChildInvCode,
    lb.BomId, lb.ModifyDate, lb.ModifyTime,
    oc.SortSeq, oc.BaseQtyN, oc.BaseQtyD, oc.CompScrap,
    CAST(1.0 * oc.BaseQtyN / NULLIF(oc.BaseQtyD, 0) AS DECIMAL(38,12)) AS QtyPer,
    ic.cInvName, ic.iInvSprice, ic.iInvNcost, ic.cInvStd,
    ic.cInvDepCode, ic.cDefWareHouse, ic.bForeExpland, ic.iSupplyType,
    child.PartId AS ChildPartId
FROM PartMap parent
JOIN LatestBom lb ON lb.ParentId = parent.PartId AND lb.bom_rn = 1
JOIN bom_opcomponent oc ON oc.BomId = lb.BomId
JOIN PartMap child ON child.PartId = oc.ComponentId AND child.part_rn = 1
LEFT JOIN Inventory ic ON ic.cInvCode = child.PartInvCode
WHERE parent.PartInvCode = %s AND parent.part_rn = 1
ORDER BY oc.SortSeq, child.PartInvCode
```

**递归展开规则（平台固定业务逻辑）：**
1. 从根节点（父编码）开始，逐层查询子件 BOM；
2. `visited_part_ids` 防止循环引用；
3. 支持 `max_depth` 限制递归深度；
4. **以 `4` 或 `7` 开头的子件编码保留该行但不再向下展开**（外购/标准件叶子）；
5. 多版本 BOM 取 `Status=3` 且 `ModifyDate/Time` 最新者；
6. 无 BOM 子件的编码（采购件/叶子件）直接查 `Inventory` 回填单行。

---

## 5. 业务场景与数据流

### 5.1 报价生成两阶段流水线

```mermaid
flowchart LR
    PDF[PDF 图纸上传] --> OCR[首页栅格化 + OCR]
    OCR --> KW[关键词/机型提取]
    KW --> PDM1["PDM BOM_027 查询<br/>(关键词+MODEL匹配)"]
    PDM1 --> REVIEW[等待人工审核<br/>勾选保留 PARTID]
    REVIEW --> U8MAP[PDM PARTID →<br/>U8 父编码映射<br/>(v_bas_part)]
    U8MAP --> U8EXP["U8 BOM 递归展开<br/>(bom_parent/bom_opcomponent<br/>/bom_bom/v_bas_part)"]
    U8EXP --> INV[Inventory 库存/成本关联]
    INV --> PRICE["recordoutlist<br/>价格补充(缺失时)"]
    PRICE --> XLSX[按类型多 Sheet Excel]
```

**阶段定义：**

| 阶段 | 状态 | 数据来源 | 产出 |
|------|------|----------|------|
| Phase1 | `queued` → `running` → `awaiting_approval` | PDM 库 `BOM_027` | PARTID 候选列表（供用户勾选） |
| Phase2 | `running` → `completed` | U8 库（BOM 展开 + Inventory + recordoutlist） | 按类型分组的报价 Excel |

### 5.2 直接 U8 查询

跳过 Phase1，用户直接提供 PARTID 列表（上限 1500 个根件），直接进入 U8 BOM 展开流程。适用于已确知零件号的场景。

### 5.3 PDM 部件匹配接口

四路召回 + 多维打分 + 分层输出：

| 通道 | 数据源 | 匹配策略 |
|------|--------|----------|
| Channel 1 | `BOM_027` + `BOM_054` | CHINANAME 关键词 must 匹配 |
| Channel 2 | `BOM_027` + `BOM_054` | MODEL 精确/模糊 + CHINANAME 过滤 |
| Channel 3 | `BOM_027` + `BOM_054` | CHINANAME 型号模糊（边界匹配） |
| Channel 4 | `BOM_016` + `BOM_027` + `BOM_054` | BOM 层级反查（子件→父件） |

---

## 6. 升级兼容性要求（关键）

为确保 U8 / PLM 升级后平台仍可用，请升级方遵循以下要求：

### 6.1 必须保持的字段（破坏即导致平台故障）

#### PDM 库 `pdm78`

| 表 | 字段 | 要求 |
|----|------|------|
| `BOM_027` | `PARTID`, `PARTVAR`, `CHINANAME`, `MODEL`, `SPEC` | **必须保留**，类型与语义不变 |
| `BOM_016` | `PARTID`, `PARENTID`, `ASSEMBLELEVEL` | **必须保留** |
| `BOM_054` | `PARTID`, `BOMSTATE`, `BOMNAME` | 建议保留（缺失则打分降级，不致命） |

#### U8 库 `UFDATA_888_2016`

| 表/视图 | 字段 | 要求 |
|---------|------|------|
| `v_bas_part` | `PartId`, `InvCode`, `cInvCode` | **必须保留**（PARTID↔InvCode 映射是桥梁） |
| `bom_bom` | `BomId`, `Status`, `ModifyDate`, `ModifyTime` | **必须保留**；`Status=3` 语义不变 |
| `bom_parent` | `BomId`, `ParentId` | **必须保留** |
| `bom_opcomponent` | `BomId`, `ComponentId`, `SortSeq`, `BaseQtyN`, `BaseQtyD`, `CompScrap` | **必须保留** |
| `Inventory` | `cInvCode`, `cInvName`, `iInvSprice`, `iInvNcost`, `cInvStd`, `cInvDepCode`, `cDefWareHouse`, `bForeExpland`, `iSupplyType` | **必须保留**（`iInvNcost` 是报价核心价格） |
| `recordoutlist` | `cinvcode`, `iunitcost`, `ddate`, `autoid` | 建议保留（缺失则部分材料无价格补充，不致命） |

### 6.2 库名与跨库引用

- 平台对 `recordoutlist` 使用**全限定名** `UFDATA_888_2016.dbo.recordoutlist`。**若 U8 升级后库名变更**（如年度账套从 `UFDATA_888_2016` 变为新年度），必须：
  - 同步更新平台配置 `U8_SQLSERVER_DATABASE`；**且**
  - 确认 `recordoutlist` 在新库中可跨库访问，或调整平台代码中的全限定名。
- PDM 库名 `pdm78` 通过配置 `PDM_SQLSERVER_DATABASE` 读取，**升级后若改名仅需更新配置**，无需改代码。

### 6.3 数据语义约束

| 约束 | 说明 |
|------|------|
| `BOM_027.PARTVAR` 版本语义 | 平台依赖 `MAX(PARTVAR)` 取最新版本。若升级后版本机制改变（如改用时间戳字段），需同步告知平台方调整。 |
| `bom_bom.Status = 3` 生效语义 | 平台固定只展开 `Status=3` 的 BOM。若生效状态码变更，必须告知。 |
| `Inventory.bForeExpland` 供应类型语义 | `1`=虚拟件，`0`=实际物料。若语义变更需告知。 |
| 子件编码 `4`/`7` 开头停止展开 | 平台固定业务规则：以 `4` 或 `7` 开头的子件不再向下展开。若编码规则变更需重新评估。 |
| PDM `PARTID` ↔ U8 `PartId` 对应关系 | PDM 的 `PARTID` 经 `v_bas_part.PartId` 映射到 U8 存货编码。**两套系统的 ID 必须保持可对应**，否则 Phase1→Phase2 链路断裂。 |

### 6.4 连接与账号

- 升级后请提供新的只读账号，确保对上述表/视图的 `SELECT` 权限；
- 确认 SQL Server 实例地址、端口（默认 1433）、加密设置（`encrypt`）是否变更；
- 平台支持 `pymssql` 连接，需确认升级后 SQL Server 仍开放 TDS 协议访问（非仅 HTTP API）。

---

## 7. 风险与保障措施

### 7.1 平台侧已有的容错机制

| 机制 | 说明 | 升级期间作用 |
|------|------|--------------|
| 启动连通性检查 | 启动时 `SELECT 1` 探活，失败不阻塞启动 | 升级停机期间服务仍可启动，报价功能降级 |
| 故障隔离 + 熔断 | 单根 BOM 失败仅跳过，连续 5 根失败才中止任务 | 避免单点故障拖垮整单 |
| 死锁重试 | `DEADLOCK_RETRY_DELAYS_SEC = (0.3, 0.8, 1.5)` | 升级后若锁竞争加剧，自动重试 |
| 价格补充降级 | `iInvNcost` 缺失时从 `recordoutlist` 补充；补充失败保留空值 | 价格字段部分缺失不影响整体流程 |
| 连接池回收 | `SQLSERVER_POOL_RECYCLE_SEC` 防 idle 连接失效 | 升级重启后自动重建连接 |

### 7.2 升级期间建议

1. **升级前**：提供升级时间窗口、库表结构变更清单（字段增删改、库名变更、状态码变更）给平台方评估；
2. **升级中（停机）**：平台报价功能将不可用（PDM/U8 查询失败），但 AI 对话、知识库、营业订单等不依赖 U8/PDM 的功能正常；
3. **升级后**：平台方需验证：
   - 连通性（账号、库名、端口）；
   - 字段存在性（按第 6.1 节清单核对）；
   - 关键查询语义（`Status=3`、`MAX(PARTVAR)`、`bForeExpland`、`4/7` 开头停止展开）；
   - 跑一次完整报价流水线做端到端验证。

### 7.3 不可降级的关键路径

以下环节若 U8/PDM 数据不可用，**报价生成功能完全不可用**（其余功能不受影响）：

- Phase1：PDM `BOM_027` 查询（PARTID 候选）
- Phase2：U8 `v_bas_part` 映射 + `bom_*` BOM 展开 + `Inventory` 价格关联

---

## 8. 数据使用清单汇总

### 8.1 PDM 库 `pdm78`（3 张表，只读）

| 表 | 读取字段 | 操作 |
|----|----------|------|
| `BOM_027` | `PARTID`, `PARTVAR`, `CHINANAME`, `MODEL`, `SPEC` | SELECT（关键词/型号匹配，取最新版本） |
| `BOM_016` | `PARTID`, `PARENTID`, `ASSEMBLELEVEL` | SELECT（BOM 层级反查） |
| `BOM_054` | `PARTID`, `BOMSTATE`, `BOMNAME` | LEFT JOIN（状态/名称打分） |

### 8.2 U8 库 `UFDATA_888_2016`（5 表 + 1 视图，只读）

| 表/视图 | 读取字段 | 操作 |
|---------|----------|------|
| `v_bas_part` | `PartId`, `InvCode`, `cInvCode` | SELECT（PARTID↔存货编码映射） |
| `bom_bom` | `BomId`, `Status`, `ModifyDate`, `ModifyTime` | SELECT（筛选生效 BOM、取最新版本） |
| `bom_parent` | `BomId`, `ParentId` | JOIN（父件关系） |
| `bom_opcomponent` | `BomId`, `ComponentId`, `SortSeq`, `BaseQtyN`, `BaseQtyD`, `CompScrap` | JOIN（子件明细、用量） |
| `Inventory` | `cInvCode`, `cInvName`, `iInvSprice`, `iInvNcost`, `cInvStd`, `cInvDepCode`, `cDefWareHouse`, `bForeExpland`, `iSupplyType` | LEFT JOIN（存货档案、价格、供应类型） |
| `recordoutlist` | `cinvcode`, `iunitcost`, `ddate`, `autoid` | SELECT（成本价缺失时补充出库单价） |

> **写操作**：平台对 U8 / PDM **无任何写操作**，仅消费方只读查询。


---

## 9. 升级协同流程建议

1. **升级方**提供：升级时间窗口、库表结构变更清单、新账号与连接信息、状态码/版本机制变更说明；
2. **平台方**评估：按本文档第 6 节逐项核对兼容性，标注需代码调整的项；
3. **联合验证**：升级后在测试环境跑完整报价流水线（PDF → PDM → U8 → Excel），核对价格与 BOM 展开结果与升级前一致；
4. **上线**：验证通过后更新 `.env` 配置（账号/库名/端口），重启服务，确认连通性检查通过。

---

*本文档基于代码库 `project-yamato-shanghai` 当前实现梳理。若 U8/PDM 升级涉及库表结构变更，请双方以此文档为基线协同确认。*
