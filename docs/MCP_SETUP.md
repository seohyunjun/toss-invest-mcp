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
        "TOSS_ENABLE_TRADING": "false"
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
`get_candles`, `get_stocks`, `get_warnings`, `get_exchange_rate`,
`get_market_calendar`, `get_accounts`, `get_holdings`, `get_buying_power`,
`get_sellable_quantity`, `get_commissions`, `get_orders`, `get_order` (16개)

거래(TOSS_ENABLE_TRADING=true 일 때만): `place_order`, `modify_order`,
`cancel_order` (+3개)

## 주의

- `TOSS_ENABLE_TRADING` 는 기본 false. 켜면 모델이 실제 주문을 낼 수 있습니다.
  켜더라도 클라이언트(Claude)가 도구 호출 시 사용자 승인을 요구하도록 두고,
  소액으로 먼저 검증하세요.
- Client Secret 은 외부에 노출되지 않게 관리하세요(설정 파일/환경변수 권한 확인).
- 엔드포인트/필드는 OpenAPI 스펙 v1.1.1 기준입니다. 변경 시 공식 스펙으로 대조하세요.
