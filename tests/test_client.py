"""TossInvestClient 단위 테스트 (네트워크 없이 requests.Session 을 모킹)."""

from __future__ import annotations

import json as jsonlib
from unittest.mock import MagicMock

import pytest

from toss_invest import (
    TossAPIError,
    TossAuthError,
    TossInvestClient,
    TossInvestError,
    _fmt_num,
    _norm_date,
)


def _resp(status: int, body=None, headers=None) -> MagicMock:
    """requests.Response 흉내."""
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.headers = headers or {}
    if body is None:
        r.content = b""
        r.json.side_effect = ValueError("no json")
        r.text = ""
    else:
        raw = jsonlib.dumps(body).encode()
        r.content = raw
        r.json.return_value = body
        r.text = raw.decode()
    return r


def _make_client(monkeypatch) -> TossInvestClient:
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_test")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_test")
    monkeypatch.delenv("TOSS_ACCOUNT_SEQ", raising=False)
    c = TossInvestClient(backoff_factor=0)  # 백오프 0 → 테스트 즉시 실행
    # 토큰은 항상 발급된 것으로 처리
    c._access_token = "tok"
    c._token_expiry = 1e18
    return c


# --------------------------------------------------------------------------- #
# _fmt_num
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,expected",
    [
        (70000, "70000"),
        (70000.0, "70000"),
        (0.5, "0.5"),
        ("1.50", "1.50"),
        (1e-5, "0.00001"),
        (10, "10"),
    ],
)
def test_fmt_num(value, expected):
    assert _fmt_num(value) == expected


def test_fmt_num_invalid():
    # 문자열은 그대로 통과(스펙이 decimal 문자열을 받음)
    assert _fmt_num("abc") == "abc"
    # 숫자로 변환 불가한 비문자열은 ValueError
    with pytest.raises(ValueError):
        _fmt_num(object())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 인증 / 자격증명
# --------------------------------------------------------------------------- #
def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_SECRET", raising=False)
    with pytest.raises(TossAuthError):
        TossInvestClient()


# --------------------------------------------------------------------------- #
# 응답 envelope / 에러 처리
# --------------------------------------------------------------------------- #
def test_result_unwrapped(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, {"result": {"price": 100}}))
    assert c.get_prices("005930") == {"price": 100}


def test_raw_returns_envelope(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, {"result": {"x": 1}}))
    assert c.get_prices("005930", raw=True) == {"result": {"x": 1}}


def test_api_error_raised(monkeypatch):
    c = _make_client(monkeypatch)
    body = {"error": {"code": "BAD", "message": "nope", "requestId": "rid"}}
    c._session.request = MagicMock(return_value=_resp(400, body))
    with pytest.raises(TossAPIError) as ei:
        c.get_prices("005930")
    assert ei.value.status_code == 400
    assert ei.value.code == "BAD"
    assert ei.value.request_id == "rid"


# --------------------------------------------------------------------------- #
# 재시도 로직
# --------------------------------------------------------------------------- #
def test_401_refreshes_token_once(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(
        side_effect=[_resp(401), _resp(200, {"result": "ok"})]
    )
    monkeypatch.setattr(c, "_fetch_token", lambda: setattr(c, "_access_token", "tok2"))
    assert c.get_accounts() == "ok"
    assert c._session.request.call_count == 2


def test_transient_retry_then_success(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(
        side_effect=[_resp(503), _resp(200, {"result": "ok"})]
    )
    assert c.get_accounts() == "ok"
    assert c._session.request.call_count == 2


def test_transient_exhausts_retries(monkeypatch):
    c = _make_client(monkeypatch)
    c.max_retries = 1
    c._session.request = MagicMock(return_value=_resp(503, {"error": {"code": "X"}}))
    with pytest.raises(TossAPIError) as ei:
        c.get_accounts()
    assert ei.value.status_code == 503
    assert c._session.request.call_count == 2  # 최초 1 + 재시도 1


# --------------------------------------------------------------------------- #
# 파라미터 / 계좌 헤더
# --------------------------------------------------------------------------- #
def test_none_params_dropped(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, {"result": []}))
    c.get_trades("005930", count=None)
    _, kwargs = c._session.request.call_args
    assert kwargs["params"] == {"symbol": "005930"}


def test_account_header_required(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, {"result": []}))
    with pytest.raises(TossInvestError):
        c.get_holdings()  # account 없음 → 에러


def test_account_header_sent(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, {"result": []}))
    c.get_holdings(account=12345)
    _, kwargs = c._session.request.call_args
    assert kwargs["headers"]["X-Tossinvest-Account"] == "12345"


def test_symbols_list_joined(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, {"result": []}))
    c.get_prices(["005930", "AAPL"])
    _, kwargs = c._session.request.call_args
    assert kwargs["params"]["symbols"] == "005930,AAPL"


# --------------------------------------------------------------------------- #
# 주문 본문 구성
# --------------------------------------------------------------------------- #
def test_place_order_body(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, {"result": {"orderId": "1"}}))
    c.place_order("005930", "buy", "limit", quantity=1, price=70000.0, account=1)
    _, kwargs = c._session.request.call_args
    body = kwargs["json"]
    assert body == {
        "symbol": "005930",
        "side": "BUY",
        "orderType": "LIMIT",
        "quantity": "1",
        "price": "70000",
    }


def test_place_order_requires_qty_or_amount(monkeypatch):
    c = _make_client(monkeypatch)
    with pytest.raises(ValueError):
        c.place_order("005930", "BUY", account=1)


# --------------------------------------------------------------------------- #
# 날짜 정규화 / get_daily_quote
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-06-17", "2026-06-17"),
        ("20260617", "2026-06-17"),
        ("2026/06/17", "2026-06-17"),
        ("2026.06.17", "2026-06-17"),
        ("2026-06-17T13:00:00.000+09:00", "2026-06-17"),
        (" 2026-06-17 ", "2026-06-17"),
    ],
)
def test_norm_date(value, expected):
    assert _norm_date(value) == expected


def test_norm_date_invalid():
    with pytest.raises(ValueError):
        _norm_date("2026-13-99")
    with pytest.raises(ValueError):
        _norm_date("nope")


def _candles_body(candles):
    return {"result": {"candles": candles, "nextBefore": None}}


def test_daily_quote_exact_with_change(monkeypatch):
    c = _make_client(monkeypatch)
    body = _candles_body([
        {"timestamp": "2026-06-17T00:00:00.000+09:00", "openPrice": "339500",
         "highPrice": "348000", "lowPrice": "331000", "closePrice": "343000",
         "volume": "35704177", "currency": "KRW"},
        {"timestamp": "2026-06-16T00:00:00.000+09:00", "openPrice": "330000",
         "highPrice": "335000", "lowPrice": "320000", "closePrice": "330000",
         "volume": "1000", "currency": "KRW"},
    ])
    c._session.request = MagicMock(return_value=_resp(200, body))
    out = c.get_daily_quote("005930", "20260617")
    # before 커서가 요청일 23:59:59(KST) 로 구성되는지 확인
    _, kwargs = c._session.request.call_args
    assert kwargs["params"]["before"] == "2026-06-17T23:59:59.999+09:00"
    assert kwargs["params"]["interval"] == "1d"
    assert out["exactDate"] is True
    assert out["tradingDate"] == "2026-06-17"
    assert out["close"] == "343000"
    assert out["previousClose"] == "330000"
    assert out["change"] == "13000"
    assert out["changeRate"] == "3.94"


def test_daily_quote_holiday_fallback(monkeypatch):
    c = _make_client(monkeypatch)
    body = _candles_body([
        {"timestamp": "2026-06-19T00:00:00.000+09:00", "openPrice": "380000",
         "highPrice": "380000", "lowPrice": "346000", "closePrice": "350500",
         "volume": "77053712", "currency": "KRW"},
        {"timestamp": "2026-06-18T00:00:00.000+09:00", "openPrice": "346000",
         "highPrice": "364000", "lowPrice": "341500", "closePrice": "363500",
         "volume": "59080574", "currency": "KRW"},
    ])
    c._session.request = MagicMock(return_value=_resp(200, body))
    out = c.get_daily_quote("005930", "2026-06-21")  # 일요일
    assert out["exactDate"] is False
    assert out["requestedDate"] == "2026-06-21"
    assert out["tradingDate"] == "2026-06-19"


def test_daily_quote_no_candles(monkeypatch):
    c = _make_client(monkeypatch)
    c._session.request = MagicMock(return_value=_resp(200, _candles_body([])))
    out = c.get_daily_quote("AAPL", "2026-06-17")
    assert out["found"] is False
    assert out["exactDate"] is False
