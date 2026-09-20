"""
LangChain 兼容性补丁
修复 paddlex 与新版 LangChain 的兼容性问题
"""
import logging
import sys

logger = logging.getLogger("app.langchain_compat")


def apply_langchain_compat():
    """应用 LangChain 兼容性补丁"""
    if 'langchain.docstore' not in sys.modules:
        try:
            from langchain_core.documents import Document as LangChainDocument
            from langchain_text_splitters import RecursiveCharacterTextSplitter as LangChainTextSplitter
            
            # 创建兼容性命名空间
            class LangChainDocstore:
                document = type('module', (), {'Document': LangChainDocument})()
            
            class LangChainTextSplitterModule:
                RecursiveCharacterTextSplitter = LangChainTextSplitter
            
            # 注入到 sys.modules
            sys.modules['langchain.docstore.document'] = LangChainDocstore.document
            sys.modules['langchain.text_splitter'] = LangChainTextSplitterModule
            sys.modules['langchain.docstore'] = LangChainDocstore
        except ImportError as exc:
            # 兼容层失效不能静默吞：paddlex 的历史 import 会在更靠后的位置
            # 报更难定位的 ImportError（补丁文件本身不删，等 #9 OCR 服务化后再评估）。
            logger.warning(
                "LangChain 兼容层注入失败（paddlex 历史 import 可能受影响）: %s", exc
            )

# 自动应用补丁
apply_langchain_compat()

