"""
toss_mcp_server.py
==================
토스증권 Open API 를 MCP(Model Context Protocol) 서버로 노출합니다.
toss_invest.py 의 TossInvestClient 를 감싸 각 엔드포인트를 MCP tool 로 제공합니다.

실행
----
    pip install "mcp[cli]" requests python-dotenv      # Python 3.10+
    python toss_mcp_server.py                          # stdio 로 구동

자격증명은 .env(또는 호출 클라이언트가 주입하는 env)에서 로드합니다.
    TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_ACCOUNT_SEQ(선택)

⚠️ 안전 설계
-----------
- 조회(read-only) 도구는 항상 등록됩니다.
- 주문/정정/취소(실거래) 도구는 환경변수 TOSS_ENABLE_TRADING 가
  '1','true','yes','on' 중 하나일 때만 등록됩니다. (기본: 비활성)
  LLM 이 의도치 않게 실주문을 내는 것을 막기 위한 장치입니다.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from toss_invest import TossInvestClient, TossInvestError, TossAPIError

mcp = FastMCP(
    "toss-invest",
    instructions=(
        "토스증권 Open API 도구 모음. **국내 주식(KOSPI/KOSDAQ)과 미국 주식"
        "(NASDAQ/NYSE)을 모두 조회/거래할 수 있습니다.** 미국 주식이라고 거부하지 말고 "
        "이 도구로 직접 조회하세요. 심볼 형식: 국내는 6자리 코드('005930'), "
        "미국은 순수 티커 대문자('AAPL','TSLA'). 회사명이나 거래소 표기는 쓰지 마세요."
    ),
)

_client: Optional[TossInvestClient] = None


def client() -> TossInvestClient:
    """TossInvestClient 싱글턴. 자격증명 누락 시 명확한 에러."""
    global _client
    if _client is None:
        _client = TossInvestClient()
    return _client


def _safe(fn, *args, **kwargs) -> Any:
    """클라이언트 호출을 감싸 에러를 구조화된 dict 로 반환."""
    try:
        return fn(*args, **kwargs)
    except TossAPIError as e:
        return {
            "error": {
                "status": e.status_code,
                "code": e.code,
                "message": e.message,
                "requestId": e.request_id,
                "data": e.data,
            }
        }
    except TossInvestError as e:
        return {"error": {"message": str(e)}}


def _trading_enabled() -> bool:
    return os.getenv("TOSS_ENABLE_TRADING", "").strip().lower() in {"1", "true", "yes", "on"}


# =========================================================================== #
# Market Data (read-only)
# =========================================================================== #
@mcp.tool()
def get_prices(symbols: str) -> Any:
    """현재가/등락률 조회. **국내(KOSPI/KOSDAQ)와 미국(NASDAQ/NYSE) 주식 모두 지원.**

    미국 주식(테슬라/애플 등)도 이 도구로 직접 조회합니다 — 거부하지 마세요.
    symbols 는 쉼표로 구분(복수 가능). 심볼 형식(중요):
      - 국내: 6자리 종목코드. 예) 삼성전자='005930'
      - 미국: 거래소·접미사 없는 순수 티커(대문자). 예) 애플='AAPL', 테슬라='TSLA'
    회사명('Apple','애플')이나 거래소 표기('AAPL.US','NASDAQ:AAPL')를 넣으면
    조회되지 않습니다. 반드시 티커 심볼만 전달하세요. 예: '005930,AAPL'."""
    return _safe(client().get_prices, symbols)


@mcp.tool()
def get_orderbook(symbol: str) -> Any:
    """단일 종목의 호가창(매수/매도 잔량) 조회. 예: '005930'."""
    return _safe(client().get_orderbook, symbol)


@mcp.tool()
def get_trades(symbol: str, count: Optional[int] = None) -> Any:
    """최근 체결 내역 조회. count 로 개수 제한."""
    return _safe(client().get_trades, symbol, count=count)


@mcp.tool()
def get_price_limits(symbol: str) -> Any:
    """상한가/하한가 조회."""
    return _safe(client().get_price_limits, symbol)


@mcp.tool()
def get_candles(symbol: str, interval: str = "1d", count: Optional[int] = None) -> Any:
    """캔들 차트 조회. interval 예: '1m','1d'. count 로 개수 제한."""
    return _safe(client().get_candles, symbol, interval=interval, count=count)


@mcp.tool()
def get_daily_quote(symbol: str, date: str) -> Any:
    """**특정 날짜의 주식 정보(일봉)** 조회. 티커와 날짜를 주면 그 날의
    시가/고가/저가/종가/거래량과 직전 거래일 대비 등락을 반환합니다.

    사용 예: 입력이 '005930 2026-06-17' 이면 symbol='005930', date='2026-06-17'.
    심볼 형식: 국내는 6자리 코드('005930'), 미국은 순수 티커 대문자('AAPL','TSLA').
    회사명·거래소 표기('Apple','AAPL.US')는 쓰지 마세요. 미국 주식도 거부하지 말고
    이 도구로 직접 조회하세요. date 는 'YYYY-MM-DD'(또는 'YYYYMMDD').
    요청 날짜가 휴장일이면 직전 거래일 캔들을 돌려주며 응답의 exactDate=false 로
    표시됩니다(tradingDate 로 실제 거래일 확인)."""
    return _safe(client().get_daily_quote, symbol, date)


# =========================================================================== #
# Stock / Market Info (read-only)
# =========================================================================== #
@mcp.tool()
def get_stocks(symbols: str) -> Any:
    """종목 기본정보(이름/시장/섹터 등). symbols 는 쉼표 구분(복수 가능).

    심볼 형식: 국내는 6자리 코드('005930'), 미국은 순수 티커 대문자('AAPL').
    회사명·거래소 표기('Apple','AAPL.US','NASDAQ:AAPL')는 조회되지 않습니다."""
    return _safe(client().get_stocks, symbols)


@mcp.tool()
def get_warnings(symbol: str) -> Any:
    """종목의 매수 유의사항(투자유의/관리 등) 조회."""
    return _safe(client().get_warnings, symbol)


@mcp.tool()
def get_exchange_rate(base_currency: str = "USD", quote_currency: str = "KRW") -> Any:
    """환율 조회. 통화는 'KRW' 또는 'USD'."""
    return _safe(client().get_exchange_rate, base_currency, quote_currency)


@mcp.tool()
def get_market_calendar(market: str = "KR") -> Any:
    """장 운영 정보(휴장일/거래시간). market 은 'KR' 또는 'US'."""
    return _safe(client().get_market_calendar, market)


# =========================================================================== #
# Account / Asset (read-only, 계좌 헤더 필요)
# =========================================================================== #
@mcp.tool()
def get_accounts() -> Any:
    """내 계좌 목록 조회. (이후 도구의 account 인자로 쓸 식별자 확인용)"""
    return _safe(client().get_accounts)


@mcp.tool()
def get_holdings(account: Optional[int] = None, symbol: Optional[str] = None) -> Any:
    """보유 종목/평가금액 조회. account 미지정 시 .env 의 TOSS_ACCOUNT_SEQ 사용."""
    return _safe(client().get_holdings, account=account, symbol=symbol)


@mcp.tool()
def get_buying_power(currency: str = "KRW", account: Optional[int] = None) -> Any:
    """매수 가능 금액 조회. currency 는 'KRW' 또는 'USD'."""
    return _safe(client().get_buying_power, currency, account=account)


@mcp.tool()
def get_sellable_quantity(symbol: str, account: Optional[int] = None) -> Any:
    """특정 종목의 매도 가능 수량 조회."""
    return _safe(client().get_sellable_quantity, symbol, account=account)


@mcp.tool()
def get_commissions(account: Optional[int] = None) -> Any:
    """매매 수수료 정보 조회."""
    return _safe(client().get_commissions, account=account)


# =========================================================================== #
# Order history (read-only)
# =========================================================================== #
@mcp.tool()
def get_orders(
    status: str = "OPEN",
    account: Optional[int] = None,
    symbol: Optional[str] = None,
    limit: Optional[int] = None,
) -> Any:
    """주문 목록 조회. status 는 'OPEN'(미체결) 또는 'CLOSED'(완료)."""
    return _safe(client().get_orders, status=status, account=account, symbol=symbol, limit=limit)


@mcp.tool()
def get_order(order_id: str, account: Optional[int] = None) -> Any:
    """단일 주문 상세 조회."""
    return _safe(client().get_order, order_id, account=account)


# =========================================================================== #
# Trading (실거래) — TOSS_ENABLE_TRADING 가 켜진 경우에만 등록
# =========================================================================== #
def _register_trading_tools() -> None:
    @mcp.tool()
    def place_order(
        symbol: str,
        side: str,
        order_type: str = "LIMIT",
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        order_amount: Optional[float] = None,
        account: Optional[int] = None,
        confirm_high_value_order: bool = False,
    ) -> Any:
        """
        ⚠️ 실제 주문 접수. side='BUY'|'SELL', order_type='LIMIT'|'MARKET'.
        수량기반은 quantity(LIMIT 이면 price 필수), 금액기반(US MARKET)은 order_amount.
        실행 전 get_buying_power / get_sellable_quantity 로 반드시 검증하세요.
        """
        return _safe(
            client().place_order,
            symbol=symbol, side=side, order_type=order_type,
            quantity=quantity, price=price, order_amount=order_amount,
            confirm_high_value_order=confirm_high_value_order, account=account,
        )

    @mcp.tool()
    def modify_order(
        order_id: str,
        order_type: str = "LIMIT",
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        account: Optional[int] = None,
    ) -> Any:
        """⚠️ 주문 정정. KR 은 quantity 필수, LIMIT 은 price 필수."""
        return _safe(
            client().modify_order,
            order_id, order_type=order_type, quantity=quantity, price=price, account=account,
        )

    @mcp.tool()
    def cancel_order(order_id: str, account: Optional[int] = None) -> Any:
        """⚠️ 주문 취소."""
        return _safe(client().cancel_order, order_id, account=account)


if _trading_enabled():
    _register_trading_tools()


def main() -> None:
    """stdio 트랜스포트로 구동 (Claude Desktop / Claude Code 가 서브프로세스로 실행)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
