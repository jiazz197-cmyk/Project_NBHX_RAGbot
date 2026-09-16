import logging
import os
from typing import List, Optional, Dict, Any

import pandas as pd
from transformers import AutoTokenizer
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sklearn.feature_extraction.text import CountVectorizer

from .exceptions import TextSplitError
from .tagger_client import HttpTagGeneratorClient

from app.ports.outbound.tag_generator import TagGeneratorPort

logger = logging.getLogger(__name__)


def _simple_tags(text: str, num_tags: int) -> List[str]:
    """CPU 兜底标签提取（CountVectorizer，无模型依赖）。

    tagger 服务不可用 / 熔断冷却期内使用，保证文档流程不阻塞。
    与 tagger 容器内的同名实现保持一致的算法与参数。
    """
    try:
        vectorizer = CountVectorizer(max_features=num_tags * 2, stop_words=None, ngram_range=(1, 2))
        X = vectorizer.fit_transform([text])
        feature_names = vectorizer.get_feature_names_out()
        word_counts = X.toarray()[0]
        word_freq = dict(zip(feature_names, word_counts))
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in sorted_words[:num_tags]]
    except Exception:
        logger.exception("简单标签提取失败")
        return []


class TagGenerator:
    """标签生成器代理（薄壳）：委托独立 tagger 服务（``TagGeneratorPort``）。

    模型（SentenceTransformer + KeyBERT + keyphrase pipeline）与 GPU 都在 tagger
    容器里，主应用进程不再 import torch、不再持有模型池——``model_name`` /
    ``device`` 仅保留为向后兼容 ``pipeline.py`` 构造调用的占位参数，真正的模型与
    设备由容器侧 ``TAGGER_MODEL_NAME`` / ``TAGGER_DEVICE`` 决定。

    降级策略（与拆分前一致）：服务不可达 / 返回异常 / 熔断冷却期内，退化为本地
    CPU ``_simple_tags``，文档仍可处理；失败日志每个实例只告警一次，避免逐
    chunk 刷屏。
    """

    def __init__(
        self,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
        device: str = "auto",
        client: Optional[TagGeneratorPort] = None,
    ):
        self._model_name = model_name
        self._device = device
        self._client = client
        self._degraded_logged = False

    @property
    def client(self) -> TagGeneratorPort:
        """惰性构造默认 HTTP 客户端；显式注入时以注入的 client 为准（便于测试）。"""
        if self._client is None:
            self._client = HttpTagGeneratorClient()
        return self._client

    def extract_tags(self, text: str, num_tags: int = 5, diversity: float = 0.5) -> List[str]:
        if not text.strip():
            return []
        try:
            tags = self.client.extract_tags(text, num_tags, diversity)
        except Exception as exc:
            self._log_degraded(exc)
            return _simple_tags(text, num_tags)
        # 上一次降级过、这次成功 → 允许下次失败时重新告警
        self._degraded_logged = False
        return tags

    def _log_degraded(self, exc: Exception) -> None:
        if self._degraded_logged:
            logger.debug("TagGenerator 仍不可用，继续使用本地兜底标签: %s", exc)
            return
        self._degraded_logged = True
        logger.warning("TagGenerator 服务不可用，降级为本地简单标签: %s", exc)


class TokenAwareTextSplitter:
    """基于 token 计数的文本切分器"""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50, model_name: Optional[str] = None):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._init_tokenizer(model_name)

    def _init_tokenizer(self, model_name: Optional[str]):
        try:
            # 不再依赖本地 ai_models 目录，统一通过模型名加载 tokenizer
            if model_name is None:
                # 可通过环境变量覆盖默认模型名
                model_name = os.environ.get("BGE_M3_TOKENIZER_NAME", "BAAI/bge-m3")

            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        except Exception as exc:
            raise TextSplitError(f"初始化 tokenizer 失败: {exc}") from exc

    def count_tokens(self, text: str) -> int:
        try:
            return len(self.tokenizer.encode(text, add_special_tokens=False))
        except Exception as exc:
            raise TextSplitError(f"统计 token 失败: {exc}") from exc

    def split_text(self, text: str) -> List[str]:
        if not text.strip():
            return [""]

        try:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                length_function=self.count_tokens,
                separators=["\n\n", "\n", "。", "．", "。 ", "\\.\\s+", "！", "？", "；", "…", " ", ""],
            )
            return splitter.split_text(text)
        except Exception as exc:
            raise TextSplitError(f"文本切分失败: {exc}") from exc


class ExcelHeaderPreservingSplitter:
    """
    Excel专用的保留表头分割器
    
    特点：
    1. 保留纵向表头：每个chunk中的内容都以"表头：值"的格式呈现
    2. 保持行完整性：每一横列不会被分割到不同的chunk中
    3. 基于token计数：chunk大小由token数量决定
    
    适用于：.xls、.xlsx文件
    """
    
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50, model_name: Optional[str] = None):
        """
        初始化Excel表头保留分割器
        
        Args:
            chunk_size: 每个chunk的最大token数
            chunk_overlap: chunk之间的重叠token数（用于保持上下文连贯性）
            model_name: tokenizer模型名称
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._init_tokenizer(model_name)
    
    def _init_tokenizer(self, model_name: Optional[str]):
        """初始化tokenizer"""
        try:
            if model_name is None:
                model_name = os.environ.get("BGE_M3_TOKENIZER_NAME", "BAAI/bge-m3")
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        except Exception as exc:
            raise TextSplitError(f"初始化 tokenizer 失败: {exc}") from exc
    
    def count_tokens(self, text: str) -> int:
        """统计文本的token数量"""
        try:
            return len(self.tokenizer.encode(text, add_special_tokens=False))
        except Exception as exc:
            raise TextSplitError(f"统计 token 失败: {exc}") from exc
    
    def split_excel_data(self, excel_data: Dict[str, Any]) -> List[str]:
        """
        分割Excel数据，保留表头
        
        Args:
            excel_data: Excel数据字典，格式为 {"headers": [...], "rows": [[...], ...]}
        
        Returns:
            分割后的文本块列表，每个块都包含完整的行和表头信息
        """
        if not excel_data or "headers" not in excel_data or "rows" not in excel_data:
            raise TextSplitError("Excel数据格式错误，需要包含 'headers' 和 'rows' 字段")
        
        headers = excel_data["headers"]
        rows = excel_data["rows"]
        
        if not headers or not rows:
            return [""]
        
        try:
            chunks = []
            current_chunk_rows = []
            current_token_count = 0
            
            # 遍历每一行
            for row in rows:
                # 构建当前行的文本（带表头）
                row_text = self._format_row_with_headers(headers, row)
                row_tokens = self.count_tokens(row_text)
                
                # 如果单行就超过chunk_size，单独作为一个chunk
                if row_tokens > self.chunk_size:
                    # 先保存之前累积的行
                    if current_chunk_rows:
                        chunk_text = "\n".join(current_chunk_rows)
                        chunks.append(chunk_text)
                        current_chunk_rows = []
                        current_token_count = 0
                    
                    # 单独保存这个大行
                    chunks.append(row_text)
                    logger.warning(f"单行token数({row_tokens})超过chunk_size({self.chunk_size})，单独作为一个chunk")
                    continue
                
                # 检查加入当前行后是否超过chunk_size
                if current_token_count + row_tokens > self.chunk_size:
                    # 保存当前chunk
                    if current_chunk_rows:
                        chunk_text = "\n".join(current_chunk_rows)
                        chunks.append(chunk_text)
                    
                    # 处理overlap：保留最后几行作为下一个chunk的开始
                    if self.chunk_overlap > 0 and current_chunk_rows:
                        overlap_rows = self._get_overlap_rows(current_chunk_rows, self.chunk_overlap)
                        current_chunk_rows = overlap_rows
                        current_token_count = self.count_tokens("\n".join(overlap_rows))
                    else:
                        current_chunk_rows = []
                        current_token_count = 0
                
                # 添加当前行
                current_chunk_rows.append(row_text)
                current_token_count += row_tokens
            
            # 保存最后一个chunk
            if current_chunk_rows:
                chunk_text = "\n".join(current_chunk_rows)
                chunks.append(chunk_text)
            
            return chunks if chunks else [""]
        
        except Exception as exc:
            raise TextSplitError(f"Excel数据切分失败: {exc}") from exc
    
    def _format_row_with_headers(self, headers: List[str], row: List[Any]) -> str:
        """
        将一行数据格式化为带表头的文本
        
        Args:
            headers: 表头列表
            row: 数据行
        
        Returns:
            格式化后的文本，如："价格：100, 数量：50, 总计：5000"
        """
        formatted_parts = []
        for i, header in enumerate(headers):
            if i < len(row):
                value = str(row[i]).strip() if row[i] is not None else ""
                if value:  # 只添加非空值
                    formatted_parts.append(f"{header}：{value}")
        
        return ", ".join(formatted_parts) if formatted_parts else ""
    
    def _get_overlap_rows(self, rows: List[str], overlap_tokens: int) -> List[str]:
        """
        从行列表末尾获取指定token数的行作为overlap
        
        Args:
            rows: 行文本列表
            overlap_tokens: 需要的overlap token数
        
        Returns:
            overlap行列表
        """
        overlap_rows = []
        token_count = 0
        
        # 从后往前遍历
        for row in reversed(rows):
            row_tokens = self.count_tokens(row)
            if token_count + row_tokens > overlap_tokens:
                break
            overlap_rows.insert(0, row)
            token_count += row_tokens
        
        return overlap_rows
    
    def split_from_dataframe(self, df: pd.DataFrame) -> List[str]:
        """
        直接从pandas DataFrame分割数据
        
        Args:
            df: pandas DataFrame对象
        
        Returns:
            分割后的文本块列表
        """
        if df.empty:
            return [""]
        
        # 将DataFrame转换为标准格式
        headers = df.columns.tolist()
        rows = df.values.tolist()
        
        excel_data = {
            "headers": headers,
            "rows": rows
        }
        
        return self.split_excel_data(excel_data)

