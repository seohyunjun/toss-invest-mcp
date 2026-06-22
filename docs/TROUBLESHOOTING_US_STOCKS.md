# 트러블슈팅: 미국 주식 조회 실패 (llama.cpp + MCP 브리지 스택)

llama.cpp 로컬 LLM에서 **국내 주식은 조회되는데 미국 주식(테슬라/애플 등)이 조회되지 않던** 문제의 원인과 해결 기록.

## 증상

- 국내 종목(예: 삼성전자) 시세는 정상 조회.
- 미국 종목을 물으면 모델이 도구를 호출하지 않고 거부:
  > "현재 제공된 도구들은 한국 주식(KRX) 시장 데이터에 특화되어 있어, 미국 주식인 테슬라(TSLA)의 시세는 조회할 수 없습니다. … 네이버/야후 파이낸스를 권장드립니다."
- toss-invest 도구를 연결한 뒤에는 조회는 되지만, Slack 전송 단계에서 실패:
  > `invalid_blocks … must be more than 0 characters [json-pointer:/blocks/0/text/text]`

## 스택 구조

```
Slack봇 (Docker)
   │  POST /v1/chat/completions
   ▼
mcp-bridge  (:9001, /home/roots/workspace/mcp-bridge/index.js)
   │  ├─ MCP 도구를 stdio로 연결 (MCP_COMMANDS / MCP_ARGS_LIST)
   │  └─ 모든 요청 system 메시지에 규칙 주입 (injectCurrentDate)
   ▼
llama-server  (:11111, Qwen3.6-35B-A3B GGUF)
```

- 스택 실행: `/home/roots/.openclaw/workspace/mcp_list/llama-mcp-stack.sh`
- 브리지에 **연결된 MCP 서버만** 모델에게 도구로 노출된다. (`MCP_ARGS_LIST` 배열에 없으면 노출 안 됨)
- `llama-server` 빌드에 `--system-prompt` 옵션 없음 → 시스템 프롬프트는 브리지에서 주입.
- 노출 도구 확인: `curl -s http://127.0.0.1:9001/health`

## 근본 원인

### 1. toss-invest MCP가 브리지에 연결되지 않은 고아 프로세스 (핵심)

`llama-mcp-stack.sh`가 toss 서버를 백그라운드로 띄우기만 하고, 브리지의 `MCP_COMMANDS`/`MCP_ARGS_LIST`에는 넣지 않았다. 그 결과 모델에 노출된 주식 도구는 **pykrx(KRX 전용)뿐**이었고, 미국 주식을 조회할 `get_prices`(toss)는 도구 목록에 아예 없었다.

→ "국내 됨 / 미국 안 됨"의 정체: 미국 지원 도구 자체가 노출되지 않음. 모델 거부는 정직한 반응이었고, 심볼 형식·시스템 프롬프트 문제가 아니었다.

### 2. Slack `invalid_blocks` = 브리지가 빈 content를 반환

브리지가 툴 호출을 **1라운드만** 처리했다. 모델이 도구 결과를 받고 또 도구를 부르려 하거나 빈 응답을 내면, `tool_calls만 있고 content는 빈` 메시지를 그대로 반환 → Slack봇이 빈 텍스트 블록을 만들어 거부당함.

## 해결

### A. toss-invest를 브리지에 연결 (`llama-mcp-stack.sh`)

`MCP_COMMANDS`에 venv python을, `MCP_ARGS_LIST`에 `toss_mcp_server.py`를 4번째 항목으로 추가. 자격증명은 `set -a; source .env; set +a`로 export 해야 브리지 자식 프로세스까지 전달된다(`.env`가 `export` 없는 평문이라 그냥 `source`하면 자식에 안 넘어감).

```bash
# 자격증명을 export 해서 브리지 자식(toss MCP)에 전달
set -a
source /home/roots/workspace/toss-invest-mcp/.env 2>/dev/null || true
set +a

MCP_COMMANDS='["node","/home/roots/.local/bin/uv","node","/home/roots/workspace/toss-invest-mcp/.venv/bin/python"]' \
MCP_ARGS_LIST='[[...insure-detect...],[...pykrx...],[...rtms...],["/home/roots/workspace/toss-invest-mcp/toss_mcp_server.py"]]' \
node /home/roots/workspace/mcp-bridge/index.js &
```

### B. 브리지 빈 응답 방지 (`mcp-bridge/index.js`)

1. 모델이 텍스트 답변을 낼 때까지 **툴 호출을 반복**(최대 5라운드).
2. 그래도 최종 `content`가 비면 `reasoning_content` 또는 안내 문구로 **폴백**.

### C. (보조) toss MCP 측 강화

- `toss_invest.py`: 심볼 정규화(`_norm_symbol`/`_norm_symbols`) — 공백 제거 + 대문자화로 `' aapl '` 같은 입력 흡수.
- `toss_mcp_server.py`: 도구 설명과 서버 `instructions`에 "국내+미국 모두 지원, 미국은 순수 티커 대문자" 명시.
- 브리지 `injectCurrentDate`: 시스템 규칙에 "미국 주식 거부 금지, get_prices 호출" 추가.

> 도구가 노출되지 않으면 프롬프트를 아무리 고쳐도 소용없다. C는 도구가 노출된 상태에서 모델이 올바른 티커를 넘기도록 돕는 보조 안전망이며, **핵심은 A(도구 연결)** 다.

## 적용 / 검증

```bash
# 스택 재시작
bash /home/roots/.openclaw/workspace/mcp_list/llama-mcp-stack.sh

# 1) toss 도구 노출 확인 (get_prices / get_stocks 등이 보여야 함)
curl -s http://127.0.0.1:9001/health

# 2) 미국 주식 조회 확인 → 모델이 get_prices("TSLA") 호출, USD 가격 응답
#    Slack 채널에서 "AMD 최근 주가 분석" 등으로 테스트
```

## 심볼 형식 참고

| 시장 | 형식 | 예 |
|---|---|---|
| 국내 | 6자리 종목코드 | 삼성전자 `005930` |
| 미국 | 순수 티커(대문자), 거래소·접미사 없음 | 애플 `AAPL`, 테슬라 `TSLA` |

`Apple`/`애플`(회사명), `AAPL.US`/`NASDAQ:AAPL`(거래소 표기)는 빈 결과 또는 400으로 실패한다.
