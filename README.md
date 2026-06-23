# toss-invest-mcp

토스증권 Open API용 Python 클라이언트와 이를 감싼 **MCP(Model Context Protocol) 서버**.
Claude Desktop·Claude Code·Cursor 등 MCP 클라이언트에서 시세 조회부터 (선택적으로) 주문까지 도구로 사용할 수 있습니다.

> ⚠️ **면책 / 위험 고지**
> 본 프로젝트는 토스증권이 만든 공식 SDK가 아닌 **비공식** 래퍼입니다.
> 별도 sandbox가 없어 주문 계열 기능은 **실제 자산을 움직입니다.** 소프트웨어는
> "있는 그대로(AS IS)" 제공되며 어떤 보증도 없습니다. 투자 자문이 아니며, 사용에 따른
> 모든 책임은 사용자에게 있습니다. API 명세는 OpenAPI v1.1.1 기준이며 변경될 수 있습니다.

## 기능

- **API 클라이언트** (`toss_invest.py`): OAuth2 토큰 자동 발급·캐싱·갱신, 401 토큰 재발급,
  일시 오류(429/5xx/네트워크) 지수 백오프 재시도(`Retry-After` 존중),
  응답 envelope(`result`) 자동 처리, 구조화된 에러(`TossAPIError`),
  주문 수량/가격 decimal 정규화.
- **MCP 서버** (`toss_mcp_server.py`): 17개 조회 도구 + (옵션) 3개 거래 도구.
  거래 도구는 `TOSS_ENABLE_TRADING=true`일 때만 노출 — 의도치 않은 실주문 방지.
  모든 도구 호출/결과/에러를 stderr·파일로 로깅(`TOSS_LOG_LEVEL`/`TOSS_LOG_FILE`).
- **테스트 노트북** (`notebooks/test_toss_invest.ipynb`): 카테고리별 점검, 주문 셀은 안전 플래그로 잠금.

## 구조

```
toss-invest-mcp/
├── toss_invest.py              # 토스증권 Open API 클라이언트
├── toss_mcp_server.py          # FastMCP 기반 MCP 서버
├── pyproject.toml              # 패키징/도구 설정
├── requirements.txt
├── .env.example                # 자격증명 템플릿 (.env로 복사해 사용)
├── docs/
│   └── MCP_SETUP.md            # Claude Desktop/Code 등록 가이드
├── tests/
│   └── test_client.py          # 단위 테스트 (requests 모킹)
└── notebooks/
    └── test_toss_invest.ipynb  # 동작 테스트(수동)
```

## 설치

```bash
git clone https://github.com/<you>/toss-invest-mcp.git
cd toss-invest-mcp
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt                     # Python 3.10+
cp .env.example .env                                # 발급받은 키 입력
```

토스증권 앱 → 더보기 → Open API 에서 신청 후 `CLIENT_ID`/`CLIENT_SECRET`을 발급받습니다.

## 빠른 시작 (클라이언트)

```python
from toss_invest import TossInvestClient

with TossInvestClient() as toss:
    print(toss.get_prices(["005930", "AAPL"]))      # 현재가(복수)
    print(toss.get_daily_quote("005930", "2026-06-17"))  # 특정 날짜 일봉(OHLCV+등락)
    print(toss.get_accounts())                       # 계좌 목록
    print(toss.get_buying_power("KRW", account=12345))
    # 주문은 실거래 — 사전 검증 후 소액으로
    # toss.place_order("005930", "BUY", "LIMIT", quantity=1, price=70000, account=12345)
```

## MCP 서버

```bash
python toss_mcp_server.py        # stdio로 구동
mcp dev toss_mcp_server.py       # MCP Inspector로 도구 점검
```

Claude Code 등록 예:

```bash
claude mcp add toss-invest --scope user \
  --env TOSS_CLIENT_ID=... --env TOSS_CLIENT_SECRET=... \
  --env TOSS_ACCOUNT_SEQ=12345 --env TOSS_ENABLE_TRADING=false \
  -- python /절대경로/toss_mcp_server.py
```

Claude Desktop 설정 등 자세한 내용은 [`docs/MCP_SETUP.md`](docs/MCP_SETUP.md) 참고.

## 환경변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `TOSS_CLIENT_ID` | ✅ | Open API Client ID |
| `TOSS_CLIENT_SECRET` | ✅ | Open API Client Secret |
| `TOSS_ACCOUNT_SEQ` | — | 계좌/주문 도구 기본 계좌(정수) |
| `TOSS_ENABLE_TRADING` | — | `true`면 주문/정정/취소 도구 노출 (기본 false) |
| `TOSS_LOG_LEVEL` | — | 로그 레벨 `DEBUG`/`INFO`(기본)/`WARNING`/`ERROR` |
| `TOSS_LOG_FILE` | — | 지정 시 해당 파일에도 로그 기록 (미지정 시 stderr만) |

### 로그 확인

stdio 트랜스포트에서 **stdout은 MCP 프로토콜 전용**이라 로그는 stderr와 (설정 시)
파일로만 남깁니다. 모든 도구 호출·결과·에러와 서버 시작/종료가 기록됩니다.

```bash
# 파일로 남겨서 확인
TOSS_LOG_FILE=./toss-mcp.log TOSS_LOG_LEVEL=DEBUG python toss_mcp_server.py
tail -f ./toss-mcp.log
```

```
2026-06-23 12:34:48 [INFO] toss_mcp: tool call: get_prices('005930,AAPL')
2026-06-23 12:34:48 [INFO] toss_mcp: tool ok: get_prices -> {...}
2026-06-23 12:34:48 [WARNING] toss_mcp: tool api-error: get_holdings status=401 code=... requestId=...
```

MCP 클라이언트(Claude Desktop/Code)로 띄운 경우 클라이언트가 캡처하는 서버 stderr
로그에서 확인하거나, `TOSS_LOG_FILE`을 지정해 파일로 확인하세요.

## 테스트

네트워크 없이 `requests` 세션을 모킹한 단위 테스트(재시도·에러처리·주문 본문 등):

```bash
pip install pytest      # 또는: pip install -e ".[dev]"
pytest
```

## 라이선스

MIT — [`LICENSE`](LICENSE) 참고.
