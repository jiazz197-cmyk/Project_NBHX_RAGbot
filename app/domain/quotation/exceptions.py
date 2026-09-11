"""Quotation execution errors shared by use cases, workers, and OCR integration."""


class QuotationPipelineCancelledError(RuntimeError):
    """Raised when a quotation task is cancelled during cooperative checks."""


class QuotationPipelineError(RuntimeError):
    """Raised for unrecoverable quotation pipeline errors."""
