"""Polars/Pandas + Plotly: clean, analyze, visualize; DataAnalysisService entry."""

import ast
import asyncio
import json
import os
from enum import Enum
from typing import List, Dict, Any, Union, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import polars as pl
from polars import Utf8
from polars.selectors import numeric
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive

logger = get_logger("ragsystem.data_analyze")


class AnalysisTask(Enum):
    CLEAN = "clean"
    ANALYZE = "analyze"
    VISUALIZE = "visualize"


class DataContext(BaseModel):
    data_source: Union[str, Dict[str, Any]]
    requirements: Dict[str, Any]
    history: List[Dict[str, Any]] = []


class DataCleaner:
    async def clean_data(self, data: pl.DataFrame) -> Dict[str, Any]:
        profile = await asyncio.to_thread(self._profile_data, data)
        issues = await asyncio.to_thread(self._identify_issues, data, profile)
        cleaned_data = await asyncio.to_thread(self._perform_cleaning, data, issues)
        return {
            "cleaned_data": {"header": cleaned_data.columns, "rows": cleaned_data.to_dicts()},
            "profile": profile,
            "issues": issues
        }

    def _profile_data(self, data: pl.DataFrame) -> Dict[str, Any]:
        return {"columns": data.columns, "rows": data.height}

    def _check_missing_values(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        issues, null_counts = [], data.null_count()
        for col in data.columns:
            count = null_counts.select(col).item()
            if count > 0:
                issues.append({"column": col, "type": "missing", "count": count})
        return issues

    def _detect_outliers(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        return []

    def _check_data_types(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        return []

    def _identify_issues(self, data: pl.DataFrame, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        return self._check_missing_values(data) + self._detect_outliers(data) + self._check_data_types(data)

    def _handle_missing(self, data: pl.DataFrame, issue: Dict[str, Any]) -> pl.DataFrame:
        col = issue["column"]
        if data[col].dtype in (pl.Float64, pl.Int64):
            return data.with_columns(pl.col(col).fill_null(data[col].mean()))
        return data.with_columns(pl.col(col).fill_null("missing"))

    def _handle_outlier(self, data: pl.DataFrame, issue: Dict[str, Any]) -> pl.DataFrame:
        return data

    def _handle_type(self, data: pl.DataFrame, issue: Dict[str, Any]) -> pl.DataFrame:
        return data

    def _perform_cleaning(self, data: pl.DataFrame, issues: List[Dict[str, Any]]) -> pl.DataFrame:
        cleaned = data.clone()
        for issue in issues:
            if issue["type"] == "missing": cleaned = self._handle_missing(cleaned, issue)
            elif issue["type"] == "outlier": cleaned = self._handle_outlier(cleaned, issue)
            elif issue["type"] == "type": cleaned = self._handle_type(cleaned, issue)
        return cleaned


class DataAnalyzer:
    async def analyze_features(self, data: pl.DataFrame, requirements: Dict[str, Any]) -> Dict[str, Any]:
        stats = await asyncio.to_thread(lambda: data.describe().to_dicts())
        corrs = await asyncio.to_thread(self._correlations, data)
        return {"statistics": stats, "correlations": corrs, "trends": {}}

    def _correlations(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        try:
            return data.select(numeric()).corr().to_dicts()
        except Exception as e:
            logger.debug("计算相关性失败，返回空列表: %s", e)
            return []


class DataVisualizer:
    def __init__(self, viz_config: Dict[str, Any] = None):
        self.viz_config = viz_config or {}
        self.y_pattern = self.viz_config.get("y_pattern", None)
        self.output_dir = self.viz_config.get("output_dir", "charts")
        self.save_png = self.viz_config.get("save_png", False)
        os.makedirs(self.output_dir, exist_ok=True)

    def _apply_format(self, fig: go.Figure, chart_type: str) -> go.Figure:
        fmt = self.viz_config.get(chart_type, {})
        if fmt:
            fig.update_layout(**{k: v for k, v in fmt.items()
                                  if k in ("template", "width", "height", "title_font")})
            axis = self.viz_config.get("axis", {})
            if axis:
                fig.update_xaxes(**axis)
                fig.update_yaxes(**axis)
        return fig

    def _ts(self) -> str:
        return utcnow_naive().strftime("%Y%m%d%H%M%S")

    def _save_html_and_png(self, fig: go.Figure, prefix: str) -> Tuple[str, Union[str, None]]:
        ts = self._ts()
        html_name = f"{prefix}_{ts}.html"
        html_path = os.path.join(self.output_dir, html_name)
        pio.write_html(fig, file=html_path, auto_open=False, full_html=True)
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()
        html = html.replace(
            '<body>',
            '<body style="text-align:center; margin:0;">'
        )
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        png_name = None
        png_path = None
        if self.save_png:
            png_name = f"{prefix}_{ts}.png"
            png_path = os.path.join(self.output_dir, png_name)
            fig.write_image(png_path, scale=3)

        html_name = os.path.abspath(html_path)
        png_name = os.path.abspath(png_path) if png_path else None
        return html_name, png_name

    def _select_x(self, df: pl.DataFrame) -> Union[str, None]:
        str_cols = [c for c, dt in df.schema.items() if dt == Utf8]
        variable = [
            c for c in str_cols
            if df.select(pl.col(c).n_unique()).item() > 1
        ]
        return variable[0] if variable else None

    def _select_y_cols(self, df: pl.DataFrame) -> List[str]:
        return df.select(pl.col(pl.Float64, pl.Int64)).columns

    def _plot_combined_line_charts(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        x_col = self._select_x(data)
        y_cols = self._select_y_cols(data)

        if self.y_pattern:
            y_cols = [c for c in y_cols if self.y_pattern in c]

        results: List[Dict[str, Any]] = []
        if not x_col or len(y_cols) < 2:
            return results

        pdf = data.to_pandas()
        fig = go.Figure()

        for y in y_cols:
            fig.add_trace(go.Scatter(x=pdf[x_col], y=pdf[y], mode='lines', name=y))

        fig.update_layout(
                          xaxis_title=x_col,
                          yaxis_title="Values",
                          font=dict(family="Microsoft YaHei", size=14)
                          )
        name_html, name_png = self._save_html_and_png(fig, "combined_line")
        results.append({
            "html_path": name_html,
            "pdf_word_path": name_png
        })
        return results

    def _plot_combined_bar_charts(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        x_col = self._select_x(data)
        y_cols = self._select_y_cols(data)

        if self.y_pattern:
            y_cols = [c for c in y_cols if self.y_pattern in c]

        results: List[Dict[str, Any]] = []
        if not x_col or len(y_cols) < 2:
            return results

        pdf = data.to_pandas()
        fig = go.Figure()

        for y in y_cols:
            fig.add_trace(go.Bar(x=pdf[x_col], y=pdf[y], name=y))

        fig.update_layout(
                          xaxis_title=x_col,
                          yaxis_title="Values",
                          font=dict(family="Microsoft YaHei", size=14)
                          )
        name_html, name_png = self._save_html_and_png(fig, "combined_bar")
        results.append({
            "html_path": name_html,
            "pdf_word_path": name_png
        })
        return results

    def _plot_line_charts(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        x_col = self._select_x(data)
        y_cols = self._select_y_cols(data)

        if self.y_pattern:
            y_cols = [c for c in y_cols if self.y_pattern in c]

        results: List[Dict[str, Any]] = []
        if not x_col or len(y_cols) < 1:
            return results

        pdf = data.to_pandas()
        fig = go.Figure()

        for y in y_cols:
            fig.add_trace(go.Scatter(x=pdf[x_col], y=pdf[y], mode='lines', name=y))

        fig.update_layout(
                          xaxis_title=x_col,
                          yaxis_title="Values",
                          font=dict(family="Microsoft YaHei", size=14)
                          )
        name_html, name_png = self._save_html_and_png(fig, "line_chart")
        results.append({
            "html_path": name_html,
            "pdf_word_path": name_png
        })
        return results

    def _plot_bar_charts(self, data: pl.DataFrame) -> List[Dict[str, Any]]:
        x_col = self._select_x(data)
        y_cols = self._select_y_cols(data)

        if self.y_pattern:
            y_cols = [c for c in y_cols if self.y_pattern in c]

        results: List[Dict[str, Any]] = []
        if not x_col or len(y_cols) < 1:
            return results

        pdf = data.to_pandas()
        fig = go.Figure()

        for y in y_cols:
            fig.add_trace(go.Bar(x=pdf[x_col], y=pdf[y], name=y))

        fig.update_layout(
                          xaxis_title=x_col,
                          yaxis_title="Values",
                          font=dict(family="Microsoft YaHei", size=14)
                          )
        name_html, name_png = self._save_html_and_png(fig, "bar_chart")
        results.append({
            "html_path": name_html,
            "pdf_word_path": name_png
        })
        return results

    def _plot_stats_table(self, stats: List[Dict[str, Any]]) -> Tuple[Union[str, None], Union[str, None]]:
        if not stats:
            return None, None
        df = pd.DataFrame(stats)

        col_widths = [120] * len(df.columns)

        formats = []
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                formats.append(".2f")
            else:
                formats.append("")

        fig = go.Figure(data=[go.Table(
            columnwidth=col_widths,
            header=dict(
                values=list(df.columns),
                font=dict(family="Microsoft YaHei", size=14),
                align="center"
            ),
            cells=dict(
                values=[df[col].tolist() for col in df.columns],
                font=dict(family="Microsoft YaHei", size=12),
                align="center",
                format=formats
            )
        )])

        total_width = sum(col_widths) + 50
        fig.update_layout(

            width=total_width,
            height=400
        )

        fig = self._apply_format(fig, 'statistics')

        return self._save_html_and_png(fig, 'statistics')

    def _plot_corr_heatmap(self, corr_data: List[List[float]]) -> Tuple[str, Union[str, None]]:
        if not corr_data or not corr_data[0]:
            return None, None

        corr_df = pd.DataFrame(corr_data)
        cols = corr_df.columns.astype(str).tolist()

        fig = go.Figure(data=go.Heatmap(
            z=corr_df.values,
            x=cols,
            y=cols,
            colorscale='Blues',
            colorbar=dict(title="Correlation"),
            text=np.round(corr_df.values, 2),
            texttemplate="%{text}",
            hoverinfo="z"
        ))

        fig.update_layout(title="相关性热力图")

        name_html, name_png = self._save_html_and_png(fig, "corr_heatmap")
        return name_html, name_png

    def _plot_trend(self, trend_key: str, trend_data: List[Dict[str, Any]]) -> Tuple[str, Union[str, None]]:
        if not trend_data:
            return None, None

        trend_df = pd.DataFrame(trend_data)
        fig = go.Figure(data=[go.Scatter(x=trend_df["date"], y=trend_df["value"], mode='lines', name=trend_key)])

        fig.update_layout(
                          xaxis_title="Date",
                          yaxis_title="Value",
                          font=dict(family="Microsoft YaHei", size=14)
                          )
        name_html, name_png = self._save_html_and_png(fig, f"trend_{trend_key}")
        return name_html, name_png

    async def create_visualizations(
        self,
        data: pl.DataFrame,
        analysis: Dict[str, Any],
        chart_type: str = None
    ) -> Dict[str, Any]:
        charts: List[Dict[str, Any]] = []

        if chart_type == "line":
            charts.extend(await asyncio.to_thread(self._plot_line_charts, data))
            return {"charts": charts, "layout": "filtered"}

        if chart_type == "bar":
            charts.extend(await asyncio.to_thread(self._plot_bar_charts, data))
            return {"charts": charts, "layout": "filtered"}

        if chart_type == "combined_line":
            charts.extend(await asyncio.to_thread(self._plot_combined_line_charts, data))
            return {"charts": charts, "layout": "filtered"}

        if chart_type == "combined_bar":
            charts.extend(await asyncio.to_thread(self._plot_combined_bar_charts, data))
            return {"charts": charts, "layout": "filtered"}

        if chart_type == "statistics":
            name, png = await asyncio.to_thread(self._plot_stats_table, analysis.get("statistics", []))
            if name:
                charts.append({"html_path": name, "pdf_word_path": png})
            return {"charts": charts, "layout": "filtered"}

        if chart_type == "correlation":
            name, png = await asyncio.to_thread(self._plot_corr_heatmap, analysis.get("correlations", []))
            if name:
                charts.append({"html_path": name, "pdf_word_path": png})
            return {"charts": charts, "layout": "filtered"}

        charts.extend(await asyncio.to_thread(self._plot_line_charts, data))
        charts.extend(await asyncio.to_thread(self._plot_bar_charts, data))

        stats_name, stats_png = await asyncio.to_thread(
            self._plot_stats_table, analysis.get("statistics", [])
        )
        if stats_name:
            charts.append({"html_path": stats_name, "pdf_word_path": stats_png})
        corr_name, corr_png = await asyncio.to_thread(
            self._plot_corr_heatmap, analysis.get("correlations", [])
        )
        if corr_name:
            charts.append({"html_path": corr_name, "pdf_word_path": corr_png})
        for key, trend_data in analysis.get("trends", {}).items():
            trend_name, trend_png = await asyncio.to_thread(self._plot_trend, key, trend_data)
            if trend_name:
                charts.append({
                    "name": trend_name,
                    "png": trend_png,
                    "type": f"trend_{key}",
                    "title": f"trend_{key}"
                })

        return {"charts": charts, "layout": "default"}


class DataAnalyst:
    def __init__(self, config: Dict[str, Any], visualizer_config: Dict[str, Any] = None):
        self.tools = {
            "cleaner": DataCleaner(),
            "analyzer": DataAnalyzer(),
            "visualizer": DataVisualizer(viz_config=visualizer_config)
        }

    async def process_all_tasks(self, context: DataContext) -> Dict[str, Any]:
        data = await self._load_data(context.data_source)
        y_pat = context.requirements.get("y_pattern")
        if y_pat is not None:
            self.tools["visualizer"].y_pattern = y_pat

        cleaning = await self.tools["cleaner"].clean_data(data)
        cleaned_df = pl.DataFrame(cleaning["cleaned_data"]["rows"])
        analysis = await self.tools["analyzer"].analyze_features(cleaned_df, context.requirements)
        chart_type = context.requirements.get("chart_type")
        viz = await self.tools["visualizer"].create_visualizations(cleaned_df, analysis, chart_type)
        result = {"cleaning": cleaning, "analysis": analysis, "visualization": viz}
        context.history.append(result)
        return result

    async def _load_data(self, data_source: Union[str, Dict[str, Any]]) -> pl.DataFrame:
        if isinstance(data_source, dict) and ("data" in data_source or "rows" in data_source):
            headers = data_source.get("headers", [])
            raw_rows = data_source.get("data", data_source.get("rows", []))
            cleaned_rows = []
            for row in raw_rows:
                cleaned = {}
                for k, v in row.items():
                    if v in ("", "null", None):
                        cleaned[k] = None
                    else:
                        try: cleaned[k] = float(v)
                        except (ValueError, TypeError): cleaned[k] = v
                cleaned_rows.append(cleaned)
            df = pl.DataFrame(cleaned_rows)
            if headers:
                df = df.select(headers)
            return df
        try:
            return pl.read_csv(data_source)
        except Exception as e:
            raise ValueError(f"无法加载数据源：{e}")


class DataAnalysisService:
    def __init__(self, viz_config: Dict[str, Any] = None):
        self.analyst = DataAnalyst(config={}, visualizer_config=viz_config)

    async def run_analysis(self, data_source: Union[str, Dict[str, Any]], requirements: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(data_source, dict) and 'chart_type' in data_source:
            requirements['chart_type'] = data_source.pop('chart_type')
            if 'recommended_chart_type' in data_source:
                requirements['recommended_chart_type'] = data_source.pop('recommended_chart_type')
            if isinstance(data_source, dict) and "y_pattern" in data_source:
                requirements["y_pattern"] = data_source.pop("y_pattern")
        context = DataContext(data_source=data_source, requirements=requirements)
        return await self.analyst.process_all_tasks(context)

    async def run_analysis_json(self, data_source: Union[str, Dict[str, Any]], requirements: Dict[str, Any]) -> str:
        result = await self.run_analysis(data_source, requirements)
        return json.dumps(result, indent=4, ensure_ascii=False)

def _open_excel_file(path: str) -> pd.ExcelFile:
    """打开 Excel：calamine 引擎优先、openpyxl 兜底（issue15 同源修复）。

    腾讯文档 / WPS 导出的 xlsx 会在 styles.xml 里写自闭合空 ``<fill/>``，
    openpyxl 3.1.5 解析 stylesheet 时抛
    ``TypeError: expected <class 'openpyxl.styles.fills.Fill'>`` —— 与
    ``app.adapters.doc_processing.doc_reader._read_excel_frames`` 的根因相同。
    写入端已改用 calamine（只读数据不读样式，天然免疫），但检索端
    ``excel_to_json`` 仍写死 openpyxl，导致 /excel 命中正确文件后整表解析失败、
    只返回错误串（2026-09-18 实测：8 个文件 3 个中招，正是含项目/负责人数据的
    NICE BG_Project list财务指标.xlsx 等）。这里对齐写入端的引擎策略。
    """
    try:
        return pd.ExcelFile(path, engine="calamine")
    except ImportError:
        logger.warning(
            "python-calamine 未安装，Excel 解析回退 openpyxl；"
            "腾讯文档/WPS 等工具导出的文件可能解析失败，请重建开发镜像"
        )
    except Exception as exc:  # noqa: BLE001 - 引擎不支持/文件异常都回退重试
        logger.warning("calamine 引擎打开 Excel 失败（%s），回退 openpyxl 重试", exc)
    return pd.ExcelFile(path, engine="openpyxl")


def excel_to_json(
    path: str,
    sheet_idx: int = 0,
    skiprows: list = None,
    header_rows: int = 2
) -> str:
    """Excel -> JSON string with sheet_name, headers, rows (multi-level header flatten)."""
    skip = skiprows if skiprows is not None else [0]

    xls = _open_excel_file(path)
    sheet_name_actual = xls.sheet_names[sheet_idx]

    title_df = pd.read_excel(
        xls,
        sheet_name=sheet_name_actual,
        header=None,
        nrows=1
    )
    raw_title = None
    for v in title_df.iloc[0].tolist():
        if pd.notna(v) and str(v).strip():
            raw_title = str(v).strip()
            break
    sheet_title = raw_title if raw_title else sheet_name_actual

    df = pd.read_excel(
        xls,
        sheet_name=sheet_name_actual,
        skiprows=skip,
        header=list(range(header_rows)),
        # 不写死 engine：沿用 ExcelFile 已选定的引擎（calamine 优先），
        # 否则会退回 openpyxl 并在坏样式文件上失败。
    )

    if isinstance(df.columns, pd.MultiIndex):
        cols = []
        last_top = None
        for tops in df.columns:
            parts = [str(p).strip() for p in tops]
            if parts[0] and not parts[0].startswith('Unnamed'):
                last_top = parts[0]
            else:
                parts[0] = last_top or parts[0]
            sub = parts[1] if len(parts) > 1 else ''
            cols.append(f"{parts[0]}_{sub}" if sub and not sub.startswith('Unnamed') else parts[0])
        df.columns = cols
    else:
        df.columns = [str(c).strip() for c in df.columns]

    df = df.fillna('').astype(str)

    result = {
        'sheet_name': sheet_title,
        'headers': df.columns.tolist(),
        'rows': df.to_dict(orient='records')
    }
    return json.dumps(result, ensure_ascii=False)


async def main():
    inp = input("请输入Excel文件路径、CSV文件路径或JSON数据：").strip()

    try:
        data_source = json.loads(inp)
    except json.JSONDecodeError:
        try:
            data_source = ast.literal_eval(inp)
        except (ValueError, SyntaxError):
            file_path = inp
            if file_path.endswith(".csv"):
                data_source = file_path
            elif file_path.endswith(".xlsx"):
                data_source = excel_to_json(file_path)
                data_source = json.loads(data_source)
            else:
                raise ValueError("无法识别的文件类型或无效的 JSON 数据")
    print(f"解析后的数据源：{data_source}")
    requirements_input = input("请输入requirements配置（JSON格式）：").strip()

    try:
        requirements = json.loads(requirements_input)
    except json.JSONDecodeError:
        raise ValueError("无法解析的 JSON 格式，请检查输入的格式")

    viz_cfg = {
        "output_dir": "charts1",
        "save_png": True,
        "y_pattern": "同比增减",
        "line": {"template": "plotly_dark", "width": 800, "height": 400},
        "bar": {"template": "ggplot2", "width": 600, "height": 350},
        "correlation": {"width": 700, "height": 700},
        "trend": {"template": "seaborn", "width": 900, "height": 450},
        "axis": {"tickfont": {"size": 12}, "titlefont": {"size": 14}}
    }
    service = DataAnalysisService(viz_config=viz_cfg)
    result = await service.run_analysis(data_source, requirements)
    print(json.dumps(result, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
