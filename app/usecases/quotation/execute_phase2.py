"""Usecase: execute quotation Phase2 (PARTIDs → U8 BOM inventory)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from app.core.logging import get_logger
from app.domain.quotation.exceptions import (
    QuotationPipelineCancelledError,
    QuotationPipelineError,
)
from app.domain.quotation.partid_mapping import convert_partids_to_u8_codes
from app.domain.quotation.pdm_result import summarize_partid_list
from app.domain.quotation.results import Phase2Result
from app.ports.outbound.quotation import CancelChecker, ProgressCallback
from app.domain.exceptions import QueryCancelledError
from app.ports.outbound.sqlserver_queries import U8BomInventoryQueryPort
from app.ports.dto.sqlserver_queries import U8BomInventoryCommand
from app.usecases.quotation._utils import response_to_dict

logger = get_logger("quotation.execute_phase2")

_U8_MAX_DEPTH = 20


@dataclass
class ExecuteQuotationPhase2Command:
    pdm_partids: List[str]
    keywords_payload: Optional[Dict[str, Any]] = None
    pdm_result: Optional[Dict[str, Any]] = None
    approved_partids: Optional[List[str]] = None
    manual_partid_types: Optional[Dict[str, str]] = None
    code_type: Optional[str] = None
    progress_callback: ProgressCallback = None
    cancel_checker: CancelChecker = None
    # 任务归属用户 id，用作 U8 BOM 每用户并发限流的 key（单人最多 N 个并发 BOM 查询）。
    owner_id: Optional[str] = None


class ExecuteQuotationPhase2UseCase:
    def __init__(self, u8_query: U8BomInventoryQueryPort):
        self._u8_query = u8_query

    def _emit_progress(self, cb: ProgressCallback, progress: int, message: str) -> None:
        if cb:
            cb(progress, message)

    def _check_cancel(self, cancel_checker: CancelChecker) -> None:
        if cancel_checker and cancel_checker():
            raise QuotationPipelineCancelledError("任务已取消")

    def execute(self, cmd: ExecuteQuotationPhase2Command) -> Phase2Result:
        cb = cmd.progress_callback
        cancel = cmd.cancel_checker

        self._check_cancel(cancel)

        if not cmd.pdm_partids:
            raise QuotationPipelineError("PDM 结果未包含任何 PARTID，无法继续 U8 查询")

        selected_partids = [str(partid).strip() for partid in cmd.pdm_partids if str(partid).strip()]
        if not selected_partids:
            raise QuotationPipelineError("PDM 结果中的 PARTID 为空，无法继续 U8 查询")

        logger.info(
            "Phase2 输入 PARTID: raw_count=%s, normalized_count=%s, sample=%s",
            len(cmd.pdm_partids),
            len(selected_partids),
            selected_partids[:8],
        )

        converted_u8_codes, pdm_to_u8_mappings = convert_partids_to_u8_codes(selected_partids)
        if not converted_u8_codes:
            raise QuotationPipelineError("PDM PARTID 转换 U8 编码后为空，无法继续 U8 查询")

        logger.info(
            "Phase2 PARTID->U8 编码转换: input_count=%s, output_count=%s, sample=%s",
            len(selected_partids),
            len(converted_u8_codes),
            pdm_to_u8_mappings[:8],
        )

        # 项目编码模式：多次查询
        if cmd.code_type == "project":
            return self._execute_project_mode(
                cmd, selected_partids, converted_u8_codes, pdm_to_u8_mappings, cancel, cb
            )

        # 普通模式：保持原有逻辑
        return self._execute_normal_mode(
            cmd, selected_partids, converted_u8_codes, pdm_to_u8_mappings, cancel, cb
        )

    def _execute_normal_mode(
        self,
        cmd: ExecuteQuotationPhase2Command,
        selected_partids: List[str],
        converted_u8_codes: List[str],
        pdm_to_u8_mappings: List[Dict[str, str]],
        cancel: CancelChecker,
        cb: ProgressCallback,
    ) -> Phase2Result:
        """普通模式：一次查询后分组"""
        from app.domain.quotation.u8_grouping import group_u8_result_by_type

        parent_inv_codes = ",".join(converted_u8_codes)
        u8_summary = summarize_partid_list(converted_u8_codes)
        self._emit_progress(
            cb,
            70,
            f"正在执行 U8 BOM Inventory 查询 | 参数(U8编码): {u8_summary}",
        )
        self._check_cancel(cancel)

        parent_codes = [code.strip() for code in parent_inv_codes.split(",") if code.strip()]
        logger.info(
            "Phase2 U8 查询开始: parent_codes=%s, max_depth=%s, sample=%s",
            len(parent_codes),
            _U8_MAX_DEPTH,
            parent_codes[:5],
        )

        try:
            response = self._u8_query.run(
                U8BomInventoryCommand(parent_inv_codes=parent_inv_codes, max_depth=_U8_MAX_DEPTH),
                cancel_checker=cancel,
                user_key=cmd.owner_id,
            )
        except QueryCancelledError as exc:
            raise QuotationPipelineCancelledError("U8 查询已取消") from exc

        self._check_cancel(cancel)

        u8_result = response_to_dict(response)
        total = u8_result.get("total") if isinstance(u8_result, dict) else None
        items = u8_result.get("items") if isinstance(u8_result, dict) else None
        failed_root_codes: List[str] = (
            u8_result.get("failed_root_codes") if isinstance(u8_result, dict) else None
        ) or []
        if failed_root_codes:
            logger.warning(
                "Phase2 U8 查询部分根节点失败已跳过: failed=%s, sample=%s",
                len(failed_root_codes), failed_root_codes[:10],
            )
        logger.info(
            "Phase2 U8 查询完成: total=%s, items_len=%s",
            total,
            len(items) if isinstance(items, list) else None,
        )

        if total == 0:
            logger.warning(
                "Phase2 U8 查询返回空结果: pdm_partids=%s, u8_parent_codes=%s",
                selected_partids,
                converted_u8_codes,
            )

        self._emit_progress(cb, 95, "U8 查询完成，正在收尾")

        u8_result_by_type: Dict[str, Any] = {"total": 0, "items": []}
        kw = cmd.keywords_payload
        has_manual = isinstance(cmd.manual_partid_types, dict) and bool(cmd.manual_partid_types)
        if (isinstance(kw, dict) and kw) or has_manual:
            u8_result_by_type, _ = group_u8_result_by_type(
                keywords_payload=kw if isinstance(kw, dict) else {},
                pdm_result=cmd.pdm_result,
                approved_partids=cmd.approved_partids or selected_partids,
                u8_result=u8_result,
                pdm_to_u8_mappings=pdm_to_u8_mappings,
                manual_partid_types=cmd.manual_partid_types,
                code_type=None,  # 普通模式
            )
            logger.info(
                "Phase2 U8 按 type 分组完成: total_types=%s",
                u8_result_by_type.get("total"),
            )

        return Phase2Result(
            u8_result=u8_result,
            u8_result_by_type=u8_result_by_type,
            failed_root_codes=failed_root_codes,
        )

    def _execute_project_mode(
        self,
        cmd: ExecuteQuotationPhase2Command,
        selected_partids: List[str],
        converted_u8_codes: List[str],
        pdm_to_u8_mappings: List[Dict[str, str]],
        cancel: CancelChecker,
        cb: ProgressCallback,
    ) -> Phase2Result:
        """项目编码模式：多次查询

        1. 浅层查询 (max_depth=1) 获取一级子零件列表
        2. 虚拟件：单独查询 (max_depth=3)，每个对应一个明细页
        3. 外购件：不单独查询，只在汇总页显示一行
        """
        project_codes_set = set(converted_u8_codes)

        # Step 1: 浅层查询获取一级子零件
        self._emit_progress(cb, 70, "正在执行项目编码浅层查询...")
        self._check_cancel(cancel)

        shallow_response = self._u8_query.run(
            U8BomInventoryCommand(parent_inv_codes=",".join(converted_u8_codes), max_depth=1),
            cancel_checker=cancel,
            user_key=cmd.owner_id,
        )
        shallow_result = response_to_dict(shallow_response)
        project_failed_root_codes: List[str] = list(
            shallow_result.get("failed_root_codes") or []
        )

        logger.info(
            "Phase2 项目编码浅层查询完成: total=%s",
            shallow_result.get("total"),
        )

        # Step 2: 提取一级子零件编码（区分虚拟件和外购件）
        virtual_children, purchased_items = self._extract_first_level_children(
            shallow_result, project_codes_set
        )

        logger.info(
            "Phase2 项目编码一级子零件: virtual=%s, purchased=%s",
            [c["code"] for c in virtual_children],
            len(purchased_items),
        )

        # Step 3: 对每个虚拟件一级子零件单独查询
        all_query_results: List[Dict[str, Any]] = []

        total_children = len(virtual_children)
        for idx, child in enumerate(virtual_children):
            child_code = child["code"]
            child_qty = child["qty"]
            child_name = child["name"]
            self._check_cancel(cancel)
            progress = 70 + int((idx / total_children) * 20) if total_children > 0 else 80
            self._emit_progress(cb, progress, f"正在查询一级子零件 {idx + 1}/{total_children}: {child_code}")

            try:
                child_response = self._u8_query.run(
                    U8BomInventoryCommand(parent_inv_codes=child_code, max_depth=_U8_MAX_DEPTH),
                    cancel_checker=cancel,
                    user_key=cmd.owner_id,
                )
            except QueryCancelledError as exc:
                raise QuotationPipelineCancelledError("U8 查询已取消") from exc

            child_result = response_to_dict(child_response)
            project_failed_root_codes.extend(child_result.get("failed_root_codes") or [])

            all_query_results.append({
                "code": child_code,
                "name": child_name,  # 使用浅层查询中的一级子零件名称
                "items": child_result.get("items", []),
                "qty": child_qty,  # 一级累计用量
            })

            logger.info(
                "Phase2 一级子零件查询完成: code=%s, name=%s, qty=%s, items=%s",
                child_code,
                child_name,
                child_qty,
                len(child_result.get("items", [])),
            )

        # Step 4: 外购件只在汇总页显示，不生成明细页
        for item in purchased_items:
            child_code = str(item.get("材料编码（物料编码）") or "").strip()
            child_name = str(item.get("子件名称") or child_code).strip()
            unit_price = item.get("单价")
            total_price = item.get("总价")

            # 读取一级累计用量
            cum_qty = item.get("累计用量")
            try:
                qty = float(cum_qty) if cum_qty is not None else 1.0
            except (TypeError, ValueError):
                qty = 1.0
            if qty < 1:
                qty = 1.0

            all_query_results.append({
                "code": child_code,
                "name": child_name,
                "items": [],  # 空明细页
                "qty": qty,
                "unit_price": unit_price,
                "total_price": total_price,
            })

            logger.info(
                "Phase2 外购件汇总行: code=%s, name=%s, price=%s",
                child_code,
                child_name,
                total_price,
            )

        self._emit_progress(cb, 95, "U8 查询完成，正在收尾")

        # Step 5: 构建分组结果
        u8_result_by_type = self._build_project_mode_groups(all_query_results)

        # 构建完整的 u8_result（合并所有查询结果，排除外购件汇总行）
        all_items = []
        for qr in all_query_results:
            all_items.extend(qr.get("items", []))

        u8_result = {
            "total": len(all_items),
            "items": all_items,
        }

        if project_failed_root_codes:
            logger.warning(
                "Phase2 项目编码 U8 查询部分根节点失败已跳过: failed=%s, sample=%s",
                len(project_failed_root_codes), project_failed_root_codes[:10],
            )

        return Phase2Result(
            u8_result=u8_result,
            u8_result_by_type=u8_result_by_type,
            failed_root_codes=project_failed_root_codes,
        )

    def _extract_first_level_children(
        self,
        shallow_result: Dict[str, Any],
        project_codes_set: Set[str],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """从浅层查询结果中提取一级子零件编码

        判断虚拟件的标准：供应类型 == "虚拟件"（对应 iSupplyType=3 或 bForeExpland=1），
        不使用编码前缀判断。有 BOM 可展开的才是虚拟件，外购件/末级零件只在汇总页显示。

        Returns:
            (virtual_children, purchased_items)
            - virtual_children: 虚拟件列表，每项含 code、qty（一级累计用量）
            - purchased_items: 外购件/末级行（浅层查询的完整行数据）
        """
        items = shallow_result.get("items", [])
        children: List[Dict[str, Any]] = []  # [{code, qty, name}]
        purchased_items: List[Dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            child_code = str(item.get("材料编码（物料编码）") or "").strip()
            parent_code = str(item.get("__parent_inv_code") or "").strip()
            supply_type = str(item.get("供应类型") or "").strip()
            level = item.get("子件层级")

            # 只处理一级子零件
            if level != 1 or parent_code not in project_codes_set:
                continue

            if not child_code:
                continue

            # 排除项目编码自身行（项目编码不应出现在子零件列表中）
            if child_code in project_codes_set:
                continue

            # 读取一级子零件名称
            child_name = str(item.get("子件名称") or child_code).strip()

            # 读取一级累计用量
            cum_qty = item.get("累计用量")
            try:
                qty = float(cum_qty) if cum_qty is not None else 1.0
            except (TypeError, ValueError):
                qty = 1.0
            if qty < 1:
                qty = 1.0

            # 判断是否为虚拟件：只用供应类型，不用编码前缀
            is_virtual = supply_type == "虚拟件"

            if is_virtual:
                children.append({"code": child_code, "qty": qty, "name": child_name})
            else:
                purchased_items.append(item)

        return children, purchased_items

    def _build_project_mode_groups(
        self,
        query_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """为项目编码模式构建分组结果

        虚拟件：有 items → 生成明细页 + 汇总行
        外购件：items 为空 → 只生成汇总行，不生成明细页
        """
        grouped_items: List[Dict[str, Any]] = []

        for qr in query_results:
            child_code = qr.get("code", "")
            child_name = qr.get("name", child_code)
            items = qr.get("items", [])
            qty = qr.get("qty", 1)  # 一级累计用量，默认 1

            # 外购件汇总行：直接使用浅层查询中的价格
            if not items:
                grouped_items.append({
                    "type": child_name,
                    "u8_parent_inv_codes": [child_code],
                    "root_inv_code": child_code,
                    "quantity": qty,
                    "total": 0,
                    "items": [],
                    "unit_price": qr.get("unit_price"),
                    "total_price": qr.get("total_price"),
                    "summary_only": True,  # 标记：只在汇总页显示
                })
                continue

            # 虚拟件：正常生成明细页
            grouped_items.append({
                "type": child_name,
                "u8_parent_inv_codes": [child_code],
                "root_inv_code": child_code,
                "quantity": qty,
                "total": len(items),
                "items": items,
                "summary_only": False,
            })

        return {
            "total": len(grouped_items),
            "items": grouped_items,
        }
