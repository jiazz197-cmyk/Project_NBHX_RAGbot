"""
LangChain 兼容性补丁
修复 paddlex 与新版 LangChain 的兼容性问题
"""
import sys

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
        except ImportError:
            pass  # 如果导入失败，让原始错误显示出来

# 自动应用补丁
apply_langchain_compat()

