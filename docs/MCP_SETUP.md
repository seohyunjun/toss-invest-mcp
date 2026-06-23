# 토스증권 MCP 서버 — 설정 가이드

`toss_invest.py`(API 클라이언트)를 `toss_mcp_server.py`(MCP 서버)로 감싸,
Claude Desktop·Claude Code·Cursor 등 MCP 클라이언트에서 토스증권 API를 도구로 사용합니다.

## 1. 설치

```bash
pip install -r requirements.txt        # Python 3.10 이상
```

`toss_invest.py`, `toss_mcp_server.py`, `.env` 가 같은 폴더에 있어야 합니다.

## 2. 자격증명(.env)

```
TOSS_CLIENT_ID=c_xxxxx
TOSS_CLIENT_SECRET=s_xxxxx
TOSS_ACCOUNT_SEQ=12345        # 선택. 계좌/주문 도구 기본 계좌
TOSS_ENABLE_TRADING=false     # true 로 바꿔야 주문/정정/취소 도구가 노출됨(실거래)
TOSS_LOG_LEVEL=INFO           # 선택. DEBUG/INFO/WARNING/ERROR
TOSS_LOG_FILE=                # 선택. 지정 시 해당 파일에도 로그 기록
```

## 3. 동작 확인

```bash
python toss_mcp_server.py          # stdio 로 대기하면 정상 (Ctrl+C 종료)
# 또는 MCP Inspector 로 도구 점검:
mcp dev toss_mcp_server.py
```

## 4. 등록 — Claude Desktop

설정 파일을 직접 편집합니다.
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "toss-invest": {
      "command": "python",
      "args": ["/절대경로/toss_mcp_server.py"],
      "env": {
        "TOSS_CLIENT_ID": "c_xxxxx",
        "TOSS_CLIENT_SECRET": "s_xxxxx",
        "TOSS_ACCOUNT_SEQ": "12345",
        "TOSS_ENABLE_TRADING": "false",
        "TOSS_LOG_LEVEL": "INFO",
        "TOSS_LOG_FILE": "/절대경로/toss-mcp.log"
      }
    }
  }
}
```

`args` 의 경로는 반드시 절대경로. venv를 쓴다면 `command` 를 그 venv의
파이썬(예: `/path/.venv/bin/python`)으로 지정하세요. 저장 후 Claude Desktop 재시작.

## 5. 등록 — Claude Code (CLI)

플래그(`--env`, `--scope`)는 서버 이름 앞, 실행 명령은 `--` 뒤에 둡니다.

```bash
claude mcp add toss-invest \
  --scope user \
  --env TOSS_CLIENT_ID=c_xxxxx \
  --env TOSS_CLIENT_SECRET=s_xxxxx \
  --env TOSS_ACCOUNT_SEQ=12345 \
  --env TOSS_ENABLE_TRADING=false \
  -- python /절대경로/toss_mcp_server.py

claude mcp list            # 등록/상태 확인
claude mcp remove toss-invest
```

## 6. 노출되는 도구

조회(항상): `get_prices`, `get_orderbook`, `get_trades`, `get_price_limits`,
`get_candles`, `get_daily_quote`, `get_stocks`, `get_warnings`, `get_exchange_rate`,
`get_market_calendar`, `get_accounts`, `get_holdings`, `get_buying_power`,
`get_sellable_quantity`, `get_commissions`, `get_orders`, `get_order` (17개)

> `get_daily_quote(symbol, date)` — 티커와 날짜('005930 2026-06-17')를 주면
> 그 날의 시/고/저/종가·거래량과 직전 거래일 대비 등락을 반환. 휴장일이면
> 직전 거래일로 폴백(`exactDate=false`, 실제 거래일은 `tradingDate`).

거래(TOSS_ENABLE_TRADING=true 일 때만): `place_order`, `modify_order`,
`cancel_order` (+3개)

## 7. 로그 확인

stdio 트랜스포트에서 **stdout 은 MCP 프로토콜(JSON-RPC) 전용**이라, 로그를 stdout 으로
보내면 통신이 깨집니다. 그래서 서버는 로그를 **stderr 와 (설정 시) 파일로만** 남깁니다.
모든 도구 호출/결과/에러와 서버 시작·종료가 기록됩니다.

- `TOSS_LOG_LEVEL`: `DEBUG`/`INFO`(기본)/`WARNING`/`ERROR`
- `TOSS_LOG_FILE`: 지정하면 해당 경로 파일에도 기록(미지정 시 stderr 만)

```bash
# 직접 실행하며 파일로 남겨 확인
TOSS_LOG_FILE=./toss-mcp.log TOSS_LOG_LEVEL=DEBUG python toss_mcp_server.py
tail -f ./toss-mcp.log
```

출력 예시:

```
2026-06-23 12:34:48 [INFO] toss_mcp: toss-invest MCP server starting (trading=off, log_level=DEBUG, log_file=./toss-mcp.log)
2026-06-23 12:34:49 [INFO] toss_mcp: tool call: get_prices('005930,AAPL')
2026-06-23 12:34:49 [INFO] toss_mcp: tool ok: get_prices -> {...}
2026-06-23 12:34:50 [WARNING] toss_mcp: tool api-error: get_holdings status=401 code=... requestId=...
```

MCP 클라이언트로 띄운 경우:
- **Claude Desktop**: 서버 stderr 가 클라이언트 로그로 캡처됩니다.
  - macOS: `~/Library/Logs/Claude/mcp-server-toss-invest.log`
  - Windows: `%APPDATA%\Claude\logs\mcp-server-toss-invest.log`
- **Claude Code**: `claude mcp list` 로 상태 확인. stderr 가 캡처되지 않는 환경이면
  위 설정처럼 `TOSS_LOG_FILE` 을 지정해 파일로 확인하는 것이 가장 확실합니다.

`toss_invest` 클라이언트(토큰 재발급/재시도 등)의 로그도 동일 핸들러로 함께 남습니다.

## 주의

- `TOSS_ENABLE_TRADING` 는 기본 false. 켜면 모델이 실제 주문을 낼 수 있습니다.
  켜더라도 클라이언트(Claude)가 도구 호출 시 사용자 승인을 요구하도록 두고,
  소액으로 먼저 검증하세요.
- Client Secret 은 외부에 노출되지 않게 관리하세요(설정 파일/환경변수 권한 확인).
- 엔드포인트/필드는 OpenAPI 스펙 v1.1.1 기준입니다. 변경 시 공식 스펙으로 대조하세요.
