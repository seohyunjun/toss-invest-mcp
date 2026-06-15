"""
toss_invest.py
==============
토스증권 Open API Python 클라이언트 (스펙 v1.1.1 기준).

인증: OAuth 2.0 Client Credentials Grant
    1) POST /oauth2/token (application/x-www-form-urlencoded,
       body: grant_type=client_credentials, client_id, client_secret)
       -> { access_token, token_type=Bearer, expires_in }
    2) 이후 모든 호출에 Authorization: Bearer {access_token}
    3) 계좌/자산/주문 엔드포인트는 X-Tossinvest-Account 헤더(정수) 필요

응답 규약
    성공: { "result": <payload> }      (200)
    실패: { "error": { requestId, code, message, data } }  (4xx/5xx)
    이 클라이언트는 기본적으로 result 를 벗겨서 반환합니다. (raw=True 로 원본 반환)

자격증명은 .env 에서 python-dotenv 로 로드:
    TOSS_CLIENT_ID=...
    TOSS_CLIENT_SECRET=...
    TOSS_ACCOUNT_SEQ=...        # (선택) 기본 계좌 (정수)

주의
    - Client Secret 은 서버 환경변수로만 보관. 클라이언트 노출 금지.
    - 별도 sandbox 없음. 주문 계열은 실제 자산을 움직입니다.
    - 주문 수량/가격은 스펙상 문자열(decimal). 본 클라이언트가 자동 문자열화합니다.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("toss_invest")

__all__ = [
    "TossInvestClient",
    "TossInvestError",
    "TossAuthError",
    "TossAPIError",
]

Number = Union[int, float, str]


def _fmt_num(value: Number) -> str:
    """수량/가격을 스펙용 decimal 문자열로 정규화.

    float 의 ``str()`` 은 지수표기('1e-05')나 부동소수 오차를 만들 수 있어
    Decimal 로 변환 후 지수표기 없이, 불필요한 0 을 제거해 직렬화한다.
    """
    if isinstance(value, str):
        return value.strip()
    try:
        dec = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"숫자로 변환할 수 없습니다: {value!r}") from exc
    # normalize() 가 정수를 '1E+1' 처럼 만들 수 있어 지수표기를 풀어준다.
    return format(dec, "f")


# --------------------------------------------------------------------------- #
# 예외
# --------------------------------------------------------------------------- #
class TossInvestError(Exception):
    """공통 예외."""


class TossAuthError(TossInvestError):
    """인증/토큰 발급 실패."""


class TossAPIError(TossInvestError):
    """API 호출 실패. 토스 ApiError(code/message/requestId/data) 를 담습니다."""

    def __init__(
        self,
        status_code: int,
        code: str = "",
        message: str = "",
        request_id: str = "",
        data: Any = None,
    ):
        super().__init__(f"[{status_code}] {code}: {message}".rstrip(": "))
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.data = data


# --------------------------------------------------------------------------- #
# 클라이언트
# --------------------------------------------------------------------------- #
class TossInvestClient:
    """토스증권 Open API 클라이언트."""

    BASE_URL = "https://openapi.tossinvest.com"
    TOKEN_PATH = "/oauth2/token"
    API = "/api/v1"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        account_seq: Optional[Union[int, str]] = None,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
        token_refresh_buffer: int = 300,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
    ):
        self.client_id = client_id or os.getenv("TOSS_CLIENT_ID") or os.getenv("API_KEY")
        self.client_secret = (
            client_secret or os.getenv("TOSS_CLIENT_SECRET") or os.getenv("API_SECRET")
        )
        self.account_seq = account_seq or os.getenv("TOSS_ACCOUNT_SEQ")

        if not self.client_id or not self.client_secret:
            raise TossAuthError(
                "CLIENT_ID/CLIENT_SECRET 가 없습니다. .env 의 "
                "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 를 설정하거나 인자로 전달하세요."
            )

        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.timeout = timeout
        self.token_refresh_buffer = token_refresh_buffer
        self.max_retries = max(0, max_retries)
        self.backoff_factor = max(0.0, backoff_factor)

        self._session = requests.Session()
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 인증
    # ------------------------------------------------------------------ #
    def _fetch_token(self) -> None:
        url = f"{self.base_url}{self.TOKEN_PATH}"
        try:
            resp = self._session.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TossAuthError(f"토큰 요청 실패: {exc}") from exc

        if resp.status_code != 200:
            raise TossAuthError(f"토큰 발급 실패 [{resp.status_code}]: {resp.text[:300]}")

        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise TossAuthError(f"응답에 access_token 이 없습니다: {data}")

        self._access_token = token
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))

    def _get_token(self) -> str:
        with self._lock:
            if (
                self._access_token is None
                or time.time() >= self._token_expiry - self.token_refresh_buffer
            ):
                self._fetch_token()
            return self._access_token  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # 공통 요청
    # ------------------------------------------------------------------ #
    def _account_header(self, account: Optional[Union[int, str]]) -> str:
        acc = account if account is not None else self.account_seq
        if acc is None:
            raise TossInvestError(
                "이 엔드포인트에는 account_seq(정수)가 필요합니다. "
                "TOSS_ACCOUNT_SEQ 설정 또는 account 인자를 전달하세요."
            )
        return str(acc)

    # 일시적(재시도 가능) HTTP 상태 코드
    _RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

    def _sleep_backoff(self, attempt: int, retry_after: Optional[str]) -> None:
        """지수 백오프(+지터) 또는 Retry-After 헤더만큼 대기."""
        delay = self.backoff_factor * (2 ** attempt)
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                pass
        delay += random.uniform(0, self.backoff_factor)  # 동시요청 충돌 완화
        if delay > 0:
            time.sleep(delay)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        account: Optional[Union[int, str]] = None,
        use_account: bool = False,
        raw: bool = False,
    ) -> Any:
        method = method.upper()
        url = f"{self.base_url}{path}"
        account_header = self._account_header(account) if use_account else None

        # None 값 파라미터 제거 (한 번만)
        if params:
            params = {k: v for k, v in params.items() if v is not None}

        retried_401 = False
        attempt = 0
        while True:
            headers = {
                "Authorization": f"Bearer {self._get_token()}",
                "Accept": "application/json",
            }
            if account_header is not None:
                headers["X-Tossinvest-Account"] = account_header

            try:
                resp = self._session.request(
                    method, url, params=params, json=json,
                    headers=headers, timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    logger.warning("network error (attempt %d): %s", attempt + 1, exc)
                    self._sleep_backoff(attempt, None)
                    attempt += 1
                    continue
                raise TossAPIError(0, "network-error", str(exc)) from exc

            # 토큰 만료 시 1회 갱신 후 재시도 (재시도 횟수에 포함하지 않음)
            if resp.status_code == 401 and not retried_401:
                retried_401 = True
                with self._lock:
                    self._access_token = None
                continue

            # 일시적 오류는 백오프 후 재시도
            if resp.status_code in self._RETRY_STATUS and attempt < self.max_retries:
                logger.warning(
                    "transient %d on %s (attempt %d)", resp.status_code, path, attempt + 1
                )
                self._sleep_backoff(attempt, resp.headers.get("Retry-After"))
                attempt += 1
                continue

            break

        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = None

        if not resp.ok:
            err = (body or {}).get("error", {}) if isinstance(body, dict) else {}
            raise TossAPIError(
                resp.status_code,
                err.get("code", ""),
                err.get("message", "") or (resp.text[:300] if not body else ""),
                err.get("requestId", resp.headers.get("X-Request-Id", "")),
                err.get("data"),
            )

        if raw or not isinstance(body, dict) or "result" not in body:
            return body
        return body["result"]

    # ================================================================== #
    # Market Data
    # ================================================================== #
    def get_prices(self, symbols: Union[str, list[str]], **kw: Any) -> Any:
        """현재가 조회. symbols: 'AAPL' 또는 ['005930','AAPL'] (복수 가능)."""
        s = ",".join(symbols) if isinstance(symbols, (list, tuple)) else symbols
        return self._request("GET", f"{self.API}/prices", params={"symbols": s}, **kw)

    def get_orderbook(self, symbol: str, **kw: Any) -> Any:
        """호가 조회."""
        return self._request("GET", f"{self.API}/orderbook", params={"symbol": symbol}, **kw)

    def get_trades(self, symbol: str, count: Optional[int] = None, **kw: Any) -> Any:
        """최근 체결 내역."""
        return self._request(
            "GET", f"{self.API}/trades", params={"symbol": symbol, "count": count}, **kw
        )

    def get_price_limits(self, symbol: str, **kw: Any) -> Any:
        """상/하한가 조회."""
        return self._request("GET", f"{self.API}/price-limits", params={"symbol": symbol}, **kw)

    def get_candles(
        self,
        symbol: str,
        interval: str = "1d",
        count: Optional[int] = None,
        before: Optional[str] = None,
        adjusted: Optional[bool] = None,
        **kw: Any,
    ) -> Any:
        """캔들 차트. interval: '1m' | '1d' 등 (스펙 enum 확인)."""
        return self._request(
            "GET",
            f"{self.API}/candles",
            params={
                "symbol": symbol, "interval": interval,
                "count": count, "before": before, "adjusted": adjusted,
            },
            **kw,
        )

    # ================================================================== #
    # Stock Info
    # ================================================================== #
    def get_stocks(self, symbols: Union[str, list[str]], **kw: Any) -> Any:
        """종목 기본 정보 (복수 가능)."""
        s = ",".join(symbols) if isinstance(symbols, (list, tuple)) else symbols
        return self._request("GET", f"{self.API}/stocks", params={"symbols": s}, **kw)

    def get_warnings(self, symbol: str, **kw: Any) -> Any:
        """매수 유의사항 조회."""
        return self._request("GET", f"{self.API}/stocks/{symbol}/warnings", **kw)

    # ================================================================== #
    # Market Info
    # ================================================================== #
    def get_exchange_rate(
        self, base_currency: str, quote_currency: str,
        date_time: Optional[str] = None, **kw: Any,
    ) -> Any:
        """환율 조회. currency: 'KRW' | 'USD'."""
        return self._request(
            "GET", f"{self.API}/exchange-rate",
            params={
                "baseCurrency": base_currency,
                "quoteCurrency": quote_currency,
                "dateTime": date_time,
            },
            **kw,
        )

    def get_market_calendar(self, market: str = "KR", date: Optional[str] = None, **kw: Any) -> Any:
        """장 운영 정보. market: 'KR' | 'US'."""
        m = market.upper()
        if m not in ("KR", "US"):
            raise ValueError("market 은 'KR' 또는 'US' 여야 합니다.")
        return self._request("GET", f"{self.API}/market-calendar/{m}", params={"date": date}, **kw)

    # ================================================================== #
    # Account & Asset  (X-Tossinvest-Account 필요)
    # ================================================================== #
    def get_accounts(self, **kw: Any) -> Any:
        """계좌 목록 조회 (계좌 헤더 불필요)."""
        return self._request("GET", f"{self.API}/accounts", **kw)

    def get_holdings(
        self, account: Optional[Union[int, str]] = None,
        symbol: Optional[str] = None, **kw: Any,
    ) -> Any:
        """보유 주식 조회."""
        return self._request(
            "GET", f"{self.API}/holdings", params={"symbol": symbol},
            account=account, use_account=True, **kw,
        )

    # ================================================================== #
    # Order Info  (X-Tossinvest-Account 필요)
    # ================================================================== #
    def get_buying_power(
        self, currency: str, account: Optional[Union[int, str]] = None, **kw: Any
    ) -> Any:
        """매수 가능 금액. currency: 'KRW' | 'USD' (필수)."""
        return self._request(
            "GET", f"{self.API}/buying-power", params={"currency": currency},
            account=account, use_account=True, **kw,
        )

    def get_sellable_quantity(
        self, symbol: str, account: Optional[Union[int, str]] = None, **kw: Any
    ) -> Any:
        """판매 가능 수량."""
        return self._request(
            "GET", f"{self.API}/sellable-quantity", params={"symbol": symbol},
            account=account, use_account=True, **kw,
        )

    def get_commissions(self, account: Optional[Union[int, str]] = None, **kw: Any) -> Any:
        """매매 수수료 조회."""
        return self._request(
            "GET", f"{self.API}/commissions", account=account, use_account=True, **kw
        )

    # ================================================================== #
    # Order History  (X-Tossinvest-Account 필요)
    # ================================================================== #
    def get_orders(
        self,
        status: str = "OPEN",
        account: Optional[Union[int, str]] = None,
        symbol: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kw: Any,
    ) -> Any:
        """주문 목록 조회. status: 'OPEN' | 'CLOSED' (필수)."""
        return self._request(
            "GET", f"{self.API}/orders",
            params={
                "status": status, "symbol": symbol, "from": from_,
                "to": to, "cursor": cursor, "limit": limit,
            },
            account=account, use_account=True, **kw,
        )

    def get_order(
        self, order_id: str, account: Optional[Union[int, str]] = None, **kw: Any
    ) -> Any:
        """주문 상세 조회."""
        return self._request(
            "GET", f"{self.API}/orders/{order_id}",
            account=account, use_account=True, **kw,
        )

    # ================================================================== #
    # Order  (X-Tossinvest-Account 필요) — ⚠️ 실제 자산 이동
    # ================================================================== #
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "LIMIT",
        quantity: Optional[Number] = None,
        price: Optional[Number] = None,
        order_amount: Optional[Number] = None,
        time_in_force: Optional[str] = None,
        client_order_id: Optional[str] = None,
        confirm_high_value_order: bool = False,
        account: Optional[Union[int, str]] = None,
        **kw: Any,
    ) -> Any:
        """
        주문 생성. ⚠️ 실제 주문이 접수됩니다.

        수량 기반: quantity 지정 (KR/US 공통). LIMIT 이면 price 필수.
        금액 기반: order_amount 지정 (US + MARKET 전용, 정규장만).
        side: 'BUY' | 'SELL', order_type: 'LIMIT' | 'MARKET'
        time_in_force: 'DAY'(기본) | 'CLS'(US LIMIT 장마감)

        수량/가격/금액은 스펙상 문자열(decimal)이라 자동 변환합니다.
        """
        if quantity is None and order_amount is None:
            raise ValueError("quantity 또는 order_amount 중 하나는 필요합니다.")

        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "orderType": order_type.upper(),
        }
        if quantity is not None:
            body["quantity"] = _fmt_num(quantity)
        if order_amount is not None:
            body["orderAmount"] = _fmt_num(order_amount)
        if price is not None:
            body["price"] = _fmt_num(price)
        if time_in_force is not None:
            body["timeInForce"] = time_in_force.upper()
        if client_order_id is not None:
            body["clientOrderId"] = client_order_id
        if confirm_high_value_order:
            body["confirmHighValueOrder"] = True

        return self._request(
            "POST", f"{self.API}/orders", json=body,
            account=account, use_account=True, **kw,
        )

    def modify_order(
        self,
        order_id: str,
        order_type: str = "LIMIT",
        quantity: Optional[Number] = None,
        price: Optional[Number] = None,
        confirm_high_value_order: bool = False,
        account: Optional[Union[int, str]] = None,
        **kw: Any,
    ) -> Any:
        """주문 정정. KR 은 quantity 필수, US 는 quantity 전달 불가. LIMIT 이면 price 필수."""
        body: dict[str, Any] = {"orderType": order_type.upper()}
        if quantity is not None:
            body["quantity"] = _fmt_num(quantity)
        if price is not None:
            body["price"] = _fmt_num(price)
        if confirm_high_value_order:
            body["confirmHighValueOrder"] = True
        return self._request(
            "POST", f"{self.API}/orders/{order_id}/modify", json=body,
            account=account, use_account=True, **kw,
        )

    def cancel_order(
        self, order_id: str, account: Optional[Union[int, str]] = None, **kw: Any
    ) -> Any:
        """주문 취소."""
        return self._request(
            "POST", f"{self.API}/orders/{order_id}/cancel", json={},
            account=account, use_account=True, **kw,
        )

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "TossInvestClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


if __name__ == "__main__":
    with TossInvestClient() as toss:
        print("계좌 목록:", toss.get_accounts())
        print("현재가:", toss.get_prices("005930"))
