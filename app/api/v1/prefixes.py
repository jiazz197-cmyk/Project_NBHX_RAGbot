"""First URL segment for v1 (after settings.API_V1_STR). All start with /.

The chat/LangChain routes intentionally keep their historical external paths at
the API root (``/api/v1/chat-messages`` etc.), so their aggregate prefix here
is an empty string rather than a new path segment.
"""

AUTH = "/auth"
EXAMPLE = "/example"
FILES = "/files"
DOCUMENT_TASKS = "/document-tasks"
DOCS_DEPRECATED = "/docs"
OCR = "/ocr"
IMAGE2URL_DEPRECATED = "/image2url"
PDF2IMAGE_DEPRECATED = "/pdf2image"
RETRIEVER = "/retriever"
CHAT_SUMMARY = "/chat-summary"
KNOWLEDGE = "/knowledge"
CONTEXT_COMPRESSION = "/context-compression"

# Reserved LangChain chat routes preserve the external legacy paths:
# /chat-messages, /conversations, /messages, etc.  An empty prefix means
# registry mounts them directly under settings.API_V1_STR.
CHAT = ""
LANGCHAIN = ""
