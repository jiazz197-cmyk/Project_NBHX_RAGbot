import math

import pytest

from app.core.exceptions import APIException
from app.usecases.quotation.create_direct_u8_task import (
    CreateDirectU8TaskCommand,
    CreateDirectU8TaskUseCase,
)


class _FakeCmd:
    def __init__(self, partids, quantities=None, code_type=None):
        self.partids = partids
        self.quantities = quantities
        self.code_type = code_type


def _validate(partids, quantities=None):
    cmd = _FakeCmd(partids, quantities)
    return CreateDirectU8TaskUseCase._validate_and_dedupe(cmd)


# ── positive cases ────────────────────────────────────────────────────────

@pytest.mark.parametrize("qty", [1, 1.0, 1.5, 0.5, 0.25, 0.0001, 1.2345, 100, 3.0])
def test_valid_quantities_pass(qty):
    partids, qtys = _validate(["010101"], [qty])
    assert qtys == {"010101": float(qty)}


def test_omitted_quantities_default_to_one():
    partids, qtys = _validate(["010101", "020202"])
    assert qtys == {"010101": 1, "020202": 1}


def test_dedup_keeps_first_quantity():
    partids, qtys = _validate(["010101", "010101"], [1.5, 3.0])
    assert partids == ["010101"]
    assert qtys == {"010101": 1.5}


# ── rejection: non-positive ───────────────────────────────────────────────

@pytest.mark.parametrize("qty", [0, 0.0, -1, -0.5, -0.0001])
def test_non_positive_quantities_rejected(qty):
    with pytest.raises(APIException) as exc_info:
        _validate(["010101"], [qty])
    assert exc_info.value.status_code == 400
    assert "必须为正数" in exc_info.value.message


# ── rejection: inf / nan ──────────────────────────────────────────────────

@pytest.mark.parametrize("qty", [math.inf, -math.inf, math.nan])
def test_non_finite_quantities_rejected(qty):
    with pytest.raises(APIException) as exc_info:
        _validate(["010101"], [qty])
    assert exc_info.value.status_code == 400
    assert "必须为正数" in exc_info.value.message


@pytest.mark.parametrize("qty_str", ["Infinity", "-Infinity", "NaN"])
def test_string_non_finite_quantities_rejected(qty_str):
    """JSON-parsed string specials become float inf/nan via Pydantic coercion."""
    with pytest.raises(APIException) as exc_info:
        _validate(["010101"], [float(qty_str)])
    assert exc_info.value.status_code == 400


# ── rejection: too many decimals ──────────────────────────────────────────

@pytest.mark.parametrize("qty", [1.23456, 0.00001, 1.00001])
def test_more_than_four_decimals_rejected(qty):
    with pytest.raises(APIException) as exc_info:
        _validate(["010101"], [qty])
    assert exc_info.value.status_code == 400
    assert "最多四位小数" in exc_info.value.message


def test_error_message_uses_original_index():
    with pytest.raises(APIException) as exc_info:
        _validate(["010101", "020202"], [1, 1.23456])
    assert "quantities[1]" in exc_info.value.message


# ── length / structure ────────────────────────────────────────────────────

def test_quantities_length_mismatch_rejected():
    cmd = _FakeCmd(["010101", "020202"], [1])
    with pytest.raises(APIException) as exc_info:
        CreateDirectU8TaskUseCase._validate_and_dedupe(cmd)
    assert exc_info.value.error_code == "INVALID_QUANTITIES"


def test_empty_partids_rejected():
    with pytest.raises(APIException) as exc_info:
        _validate([])
    assert exc_info.value.error_code == "EMPTY_PARTIDS"
