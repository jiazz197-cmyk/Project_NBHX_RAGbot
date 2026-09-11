import logging


class DocumentProcessingError(Exception):
    """文档处理通用异常"""


class DocumentParseError(DocumentProcessingError):
    """文档解析失败"""


class TextSplitError(DocumentProcessingError):
    """文本分块失败"""


class EmbeddingError(DocumentProcessingError):
    """向量化失败"""


class VectorStoreError(DocumentProcessingError):
    """写入向量存储失败"""


logger = logging.getLogger(__name__)

