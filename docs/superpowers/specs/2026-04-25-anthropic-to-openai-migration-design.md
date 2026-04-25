# Anthropic API → OpenAI API 마이그레이션 설계

- **작성일:** 2026-04-25
- **브랜치:** `claude/dreamy-hugle-924519`
- **대상 프로젝트:** `news_mcp_naver` (네이버 뉴스 검색 MCP 서버)

## 1. 목적

현재 `_summarize_with_claude` 메서드에서 Anthropic Claude API(`claude-haiku-4-5-20251001`)로 뉴스 요약을 생성하고 있다. 이를 OpenAI Responses API(`gpt-5.4-mini`) + Structured Outputs로 전환한다.

이 변경은 단일 LLM 호출 경로(`search_and_summarize_news` 도구)에 국한되며, 네이버 뉴스 검색 API와 다른 4개 도구(`search_news`, `get_category_news`, `list_categories`, `generate_news_report`)는 영향받지 않는다.

## 2. 핵심 결정

| 항목 | 결정 | 비고 |
|---|---|---|
| 모델 | `gpt-5.4-mini` | 2026-03 출시, GPT-5.4-class 성능을 저비용으로 제공 |
| API | Responses API (`client.responses.parse`) | OpenAI 신규 권장 인터페이스 |
| 출력 강제 | Structured Outputs (Pydantic) | JSON 파싱·스키마 위반 가능성 제거 |
| 비동기 처리 | `AsyncOpenAI` 네이티브 | 기존 `run_in_executor` 래핑 제거 |
| 라벨 표기 | `Claude` → `OpenAI` | 출력/에러 메시지 3곳 |
| 의존성 정리 | `anthropic` 제거, `openai`+`pydantic` 추가 | clean swap, fallback 없음 |

## 3. 변경 파일 (5개)

### 3.1 `server.py`

**Import 변경:**
```python
# Before
import anthropic
from credential import anthropic_api_key, naver_client_id, naver_client_secret

# After
from openai import AsyncOpenAI
from pydantic import BaseModel
from typing import Literal
from credential import openai_api_key, naver_client_id, naver_client_secret
```

**Pydantic 스키마 추가 (모듈 상단, 클래스 외부):**
```python
class ArticleSummary(BaseModel):
    id: str
    news_type: Literal["Factual", "Analytical", "Predictive", "Hybrid"]
    summary: list[str]

class KeywordSource(BaseModel):
    item: str
    link: str

class KeywordCuration(BaseModel):
    keyword: str
    top_sources: list[KeywordSource]

class NewsSummaryReport(BaseModel):
    articles: list[ArticleSummary]
    keywords: list[KeywordCuration]
```

**클라이언트 초기화 (`__init__`):**
```python
# Before
self.claude_api_key = anthropic_api_key
self.claude_client: Optional[anthropic.Anthropic] = None
if not self.claude_api_key:
    logger.warning("Claude API key not found in environment variables")
else:
    self.claude_client = anthropic.Anthropic(api_key=self.claude_api_key)
    logger.info("Claude API client initialized successfully")

# After
self.openai_api_key = openai_api_key
self.openai_client: Optional[AsyncOpenAI] = None
if not self.openai_api_key:
    logger.warning("OpenAI API key not found in environment variables")
else:
    self.openai_client = AsyncOpenAI(api_key=self.openai_api_key)
    logger.info("OpenAI API client initialized successfully")
```

**`_summarize_with_claude` → `_summarize_with_openai` 메서드 교체:**

호출부(`_search_and_summarize_news`)도 메서드명·체크 변수명 동기화:
- `if not self.claude_client:` → `if not self.openai_client:`
- 에러 메시지 `"Claude API 키"` → `"OpenAI API 키"`, `"ANTHROPIC_API_KEY"` → `"OPENAI_API_KEY"`
- `self._summarize_with_claude(...)` → `self._summarize_with_openai(...)`

새 메서드 본문:
```python
async def _summarize_with_openai(
    self,
    news_data: List[Dict[str, Any]],
    keywords_for_curation: Optional[List[str]] = None,
) -> str:
    news_list = [
        {"id": item["id"], "제목": item["제목"], "본문": item["본문"]}
        for item in news_data
    ]

    user_message_parts = [
        "다음 뉴스 기사들을 분석하고 요약해주세요.\n",
        f"news_list: {json.dumps(news_list, ensure_ascii=False)}\n",
    ]
    if keywords_for_curation:
        user_message_parts.append(
            f"\nkeywords: {json.dumps(keywords_for_curation, ensure_ascii=False)}"
        )
    user_message = "\n".join(user_message_parts)

    response = await self.openai_client.responses.parse(
        model="gpt-5.4-mini",
        instructions=self.summary_prompt,
        input=user_message,
        text_format=NewsSummaryReport,
        max_output_tokens=20000,
    )

    parsed: NewsSummaryReport = response.output_parsed
    result_text = json.dumps(
        parsed.model_dump(), ensure_ascii=False, indent=2
    )

    return self._format_summary_result(result_text, news_data)
```

**`_format_summary_result` 라벨 변경 (2곳):**
- `## 📋 Claude AI 분석 결과` → `## 📋 OpenAI 분석 결과`
- `*본 요약은 Claude AI를 활용하여 자동 생성되었습니다.*` → `*본 요약은 OpenAI를 활용하여 자동 생성되었습니다.*`

### 3.2 `credential.py`

```python
# Before
anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')

# After
openai_api_key = os.environ.get('OPENAI_API_KEY')
```

### 3.3 `pyproject.toml`

```toml
dependencies = [
    "aiohttp>=3.11.18",
    "openai>=2.0.0",
    "pydantic>=2.0.0",
    "mcp>=1.9.1",
    "requests>=2.32.3",
]
```

### 3.4 `requirements.txt`

`pyproject.toml`과 동기화:
```
aiohttp>=3.11.18
openai>=2.0.0
pydantic>=2.0.0
mcp>=1.9.1
requests>=2.32.3
```

### 3.5 `config.json`

```json
"env": {
  "NAVER_CLIENT_ID": "",
  "NAVER_CLIENT_SECRET": "",
  "OPENAI_API_KEY": ""
}
```

## 4. 출력 동일성

기존 Claude 응답은 자유 형식 JSON 텍스트였고, `_format_summary_result`가 이를 마크다운 보고서에 그대로 삽입했다. Structured Outputs 전환 후에도 다음과 같이 동일성을 유지한다:

- `parsed.model_dump()` → 동일한 키 구조 (`articles`, `keywords`, `id`, `news_type`, `summary`, `top_sources`, `item`, `link`)
- `json.dumps(..., ensure_ascii=False, indent=2)` → 한글 보존, 가독성 있는 들여쓰기
- 마크다운 래퍼(제목/생성일시/원문 링크 섹션)는 라벨 외 동일

부수효과: JSON 들여쓰기·키 순서가 일관되게 정돈됨 (Pydantic 스키마 정의 순서). 데이터 의미는 변하지 않음.

## 5. 변경하지 않는 것

- `news_summary_prompt.md` — 시스템 프롬프트 그대로 활용. `<output_format>` 영역도 모델에 그대로 노출되며 Pydantic 스키마와 의미상 일치.
- 다른 4개 MCP 도구 — LLM 미사용.
- 에러 처리 패턴 — 기존 `try/except Exception` 그대로. OpenAI 세분화 예외(`APIError`, `RateLimitError`)는 도입하지 않음.
- 구버전 호환 — `ANTHROPIC_API_KEY` fallback 없음. 환경변수는 `OPENAI_API_KEY`만 인식.

## 6. 검증 계획

자동화된 테스트 인프라가 없으므로 수동 검증으로 진행한다:

1. `uv sync`로 의존성 재설치, `anthropic` 제거 및 `openai`/`pydantic` 설치 확인
2. `OPENAI_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 환경변수 설정
3. `uv run server.py`로 서버 기동, import/init 단계 에러 없는지 로그 확인
4. MCP 클라이언트에서 다음 호출:
   - `search_and_summarize_news(keywords=["AI 반도체"])`
   - `search_and_summarize_news(keywords=["AI 반도체", "삼성전자"], num_articles=3, include_keyword_curation=True)`
5. 응답 검증:
   - 마크다운 구조가 기존과 동일 (제목/푸터 라벨만 OpenAI로 변경)
   - JSON 본문이 `articles[]`, `keywords[]` 스키마 준수
   - 한글 정상 표시
6. 회귀 확인: `search_news`, `get_category_news`, `list_categories`, `generate_news_report`가 영향 없는지 1회씩 호출

## 7. 위험 요소

- **`gpt-5.4-mini` Structured Outputs 비호환 가능성:** Responses API + `text_format`(Pydantic) 조합이 모든 모델에서 균일하지 않을 수 있다. 호출 시 400 응답이 나면 `gpt-5-mini`로 모델만 다운그레이드해 재시도. (마이그레이션 본질에는 영향 없음)
- **`max_output_tokens=20000`:** Anthropic 측 `max_tokens`와 동일 의미로 사용. 모델 한도 내(`gpt-5.4-mini`는 128K 출력 토큰 한도)이므로 문제 없음.
- **출력 토큰 비용 증가:** Structured Outputs는 일반적으로 동일 토큰 수에서 약간 더 많이 출력하는 경향이 있으나, 본 워크로드는 키워드당 5~10개 기사 수준이라 실질 영향 미미.
