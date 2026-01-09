#!/usr/bin/env python3
"""
News MCP Server

A Model Context Protocol (MCP) server that provides access to news articles.
Uses Naver News Search API.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp
import anthropic
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    LoggingLevel
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("news-mcp-server")

# Naver News Search API 설정
NAVER_API_BASE_URL = "https://openapi.naver.com/v1/search/news.json"


class NewsMCPServer:
    # 뉴스 카테고리 정의
    NEWS_CATEGORIES = {
        "politics": {
            "name": "정치",
            "keywords": ["정치", "국회", "대통령", "정당", "선거"],
            "description": "정치, 국회, 정당 관련 뉴스"
        },
        "economy": {
            "name": "경제",
            "keywords": ["경제", "주식", "부동산", "금융", "기업"],
            "description": "경제, 금융, 증시, 부동산 관련 뉴스"
        },
        "society": {
            "name": "사회",
            "keywords": ["사회", "사건", "사고", "교육", "환경"],
            "description": "사회, 교육, 환경, 사건사고 관련 뉴스"
        },
        "culture": {
            "name": "생활/문화",
            "keywords": ["문화", "여행", "음식", "건강", "패션"],
            "description": "생활, 문화, 여행, 건강 관련 뉴스"
        },
        "tech": {
            "name": "IT/과학",
            "keywords": ["IT", "과학", "기술", "AI", "스마트폰"],
            "description": "IT, 과학, 기술, 인공지능 관련 뉴스"
        },
        "world": {
            "name": "세계",
            "keywords": ["국제", "세계", "미국", "중국", "일본"],
            "description": "국제, 세계 각국 관련 뉴스"
        },
        "sports": {
            "name": "스포츠",
            "keywords": ["스포츠", "축구", "야구", "농구", "올림픽"],
            "description": "스포츠, 프로야구, 축구, 올림픽 관련 뉴스"
        },
        "entertainment": {
            "name": "연예",
            "keywords": ["연예", "드라마", "영화", "K-POP", "아이돌"],
            "description": "연예, 드라마, 영화, K-POP 관련 뉴스"
        }
    }
    
    def __init__(self):
        self.app = Server("news-mcp-server")
        self.session: Optional[aiohttp.ClientSession] = None
        
        # 환경 변수에서 API 키 로드
        self.client_id = os.environ.get("news_client_id")
        self.client_secret = os.environ.get("news_client_secret")
        
        if not self.client_id or not self.client_secret:
            logger.warning("Naver API credentials not found in environment variables")
        else:
            logger.info("Naver API credentials loaded successfully")
        
        # Claude API 키 로드
        self.claude_api_key = os.environ.get("claude_api_key")
        self.claude_client: Optional[anthropic.Anthropic] = None
        
        if not self.claude_api_key:
            logger.warning("Claude API key not found in environment variables")
        else:
            self.claude_client = anthropic.Anthropic(api_key=self.claude_api_key)
            logger.info("Claude API client initialized successfully")
        
        # 뉴스 요약 프롬프트 로드
        self.summary_prompt = self._load_summary_prompt()
        
        # Register handlers
        self._setup_handlers()
    
    def _load_summary_prompt(self) -> str:
        """news_summary_prompt.md 파일에서 시스템 프롬프트를 로드합니다."""
        prompt_file = Path(__file__).parent / "news_summary_prompt.md"
        try:
            if prompt_file.exists():
                content = prompt_file.read_text(encoding="utf-8")
                logger.info("News summary prompt loaded successfully")
                return content
            else:
                logger.warning(f"Prompt file not found: {prompt_file}")
                return self._get_default_summary_prompt()
        except Exception as e:
            logger.error(f"Error loading prompt file: {e}")
            return self._get_default_summary_prompt()
    
    def _get_default_summary_prompt(self) -> str:
        """기본 뉴스 요약 프롬프트를 반환합니다."""
        return """You are a Korean News Analyst & Summarizer. 
Summarize news articles in a compact, readable way.
Prioritize verifiable facts, numbers, and who/what/when outcomes.
Output in Korean with JSON format."""
    
    def _setup_handlers(self):
        """Setup MCP server handlers"""
        
        @self.app.list_resources()
        async def handle_list_resources() -> List[Resource]:
            """List available resources"""
            return [
                Resource(
                    uri="news://search",
                    name="News Search",
                    description="Search for news articles",
                    mimeType="application/json"
                ),
                Resource(
                    uri="news://categories",
                    name="News Categories",
                    description="사용 가능한 뉴스 카테고리 목록 (Available news categories)",
                    mimeType="application/json"
                )
            ]
        
        @self.app.read_resource()
        async def handle_read_resource(uri: str) -> str:
            """Read a specific resource"""
            if uri == "news://search":
                return json.dumps({
                    "description": "Use the search_news tool to find news articles",
                    "example": "search_news with query='technology'"
                }, ensure_ascii=False)
            elif uri == "news://categories":
                return json.dumps(self._get_categories(), ensure_ascii=False)
            else:
                raise ValueError(f"Unknown resource: {uri}")
        
        @self.app.list_tools()
        async def handle_list_tools() -> List[Tool]:
            """List available tools"""
            # 카테고리 ID 목록 생성
            category_ids = list(self.NEWS_CATEGORIES.keys())
            category_desc = ", ".join([f"{k}({v['name']})" for k, v in self.NEWS_CATEGORIES.items()])
            
            return [
                Tool(
                    name="search_news",
                    description="네이버 뉴스에서 키워드로 뉴스 기사를 검색합니다 (Search for news articles by keyword using Naver News API)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "검색어 (Search query keywords)"
                            },
                            "display": {
                                "type": "integer",
                                "description": "한 번에 표시할 검색 결과 개수 (기본값: 10, 최댓값: 100)",
                                "default": 10
                            },
                            "start": {
                                "type": "integer",
                                "description": "검색 시작 위치 (기본값: 1, 최댓값: 1000)",
                                "default": 1
                            },
                            "sort": {
                                "type": "string",
                                "enum": ["sim", "date"],
                                "description": "검색 결과 정렬 방법 - sim: 정확도순(기본값), date: 날짜순",
                                "default": "sim"
                            }
                        },
                        "required": ["query"]
                    }
                ),
                Tool(
                    name="get_category_news",
                    description=f"특정 카테고리의 최신 뉴스를 가져옵니다. 사용 가능한 카테고리: {category_desc}",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": category_ids,
                                "description": f"뉴스 카테고리 ID: {category_desc}"
                            },
                            "display": {
                                "type": "integer",
                                "description": "한 번에 표시할 검색 결과 개수 (기본값: 10, 최댓값: 100)",
                                "default": 10
                            },
                            "sort": {
                                "type": "string",
                                "enum": ["sim", "date"],
                                "description": "검색 결과 정렬 방법 - sim: 정확도순, date: 날짜순(기본값)",
                                "default": "date"
                            }
                        },
                        "required": ["category"]
                    }
                ),
                Tool(
                    name="list_categories",
                    description="사용 가능한 모든 뉴스 카테고리 목록과 설명을 반환합니다 (List all available news categories)",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="generate_news_report",
                    description="특정 주제에 대해 뉴스를 검색하고 종합적인 리서치 레포트를 생성합니다. 주제를 입력하면 관련 뉴스를 수집하여 트렌드, 주요 이슈, 핵심 내용을 정리한 레포트를 만들어줍니다.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "topic": {
                                "type": "string",
                                "description": "레포트를 생성할 주제 (예: '인공지능 산업 동향', '부동산 시장', '전기차 시장')"
                            },
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "추가 검색 키워드 (선택사항, 기본: 주제를 기반으로 자동 생성)"
                            },
                            "num_articles": {
                                "type": "integer",
                                "description": "수집할 뉴스 기사 수 (기본값: 15, 최대: 50)",
                                "default": 15
                            },
                            "include_links": {
                                "type": "boolean",
                                "description": "레포트에 원문 링크 포함 여부 (기본값: true)",
                                "default": True
                            }
                        },
                        "required": ["topic"]
                    }
                ),
                Tool(
                    name="search_and_summarize_news",
                    description="키워드로 뉴스를 검색하고 Claude AI를 활용하여 전문적인 요약을 제공합니다. 뉴스 유형(사실/분석/예측/혼합)을 분류하고, 핵심 팩트와 수치를 중심으로 간결하게 요약합니다.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "검색할 키워드 목록 (예: ['AI 반도체', '삼성전자'])"
                            },
                            "num_articles": {
                                "type": "integer",
                                "description": "키워드당 검색할 뉴스 기사 수 (기본값: 5, 최대: 10)",
                                "default": 5
                            },
                            "include_keyword_curation": {
                                "type": "boolean",
                                "description": "각 키워드별 신뢰할 수 있는 3개 뉴스 소스 큐레이션 포함 여부 (기본값: true)",
                                "default": True
                            }
                        },
                        "required": ["keywords"]
                    }
                )
            ]
        
        @self.app.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            """Handle tool calls"""
            try:
                if name == "search_news":
                    return await self._search_news(**arguments)
                elif name == "get_category_news":
                    return await self._get_category_news(**arguments)
                elif name == "list_categories":
                    return self._list_categories()
                elif name == "generate_news_report":
                    return await self._generate_news_report(**arguments)
                elif name == "search_and_summarize_news":
                    return await self._search_and_summarize_news(**arguments)
                else:
                    raise ValueError(f"Unknown tool: {name}")
            except Exception as e:
                logger.error(f"Error in tool {name}: {str(e)}")
                return [TextContent(type="text", text=f"Error: {str(e)}")]
    
    async def _ensure_session(self):
        """Ensure we have an active aiohttp session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def _search_news(
        self, 
        query: str, 
        display: int = 10, 
        start: int = 1, 
        sort: str = "sim"
    ) -> List[TextContent]:
        """
        네이버 뉴스 검색 API를 호출하여 뉴스 기사를 검색합니다.
        
        Args:
            query: 검색어
            display: 한 번에 표시할 검색 결과 개수 (기본값: 10, 최댓값: 100)
            start: 검색 시작 위치 (기본값: 1, 최댓값: 1000)
            sort: 정렬 방법 - sim(정확도순), date(날짜순)
        """
        await self._ensure_session()
        
        # API 자격 증명 확인
        if not self.client_id or not self.client_secret:
            return [TextContent(
                type="text",
                text="오류: 네이버 API 자격 증명이 설정되지 않았습니다. "
                     "환경 변수 news_client_id와 news_client_secret을 설정해주세요."
            )]
        
        # 파라미터 유효성 검사
        display = max(1, min(display, 100))  # 1~100
        start = max(1, min(start, 1000))  # 1~1000
        if sort not in ["sim", "date"]:
            sort = "sim"
        
        # 요청 헤더 설정
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret
        }
        
        # 요청 파라미터 설정
        params = {
            "query": query,
            "display": display,
            "start": start,
            "sort": sort
        }
        
        try:
            async with self.session.get(
                NAVER_API_BASE_URL,
                headers=headers,
                params=params
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Naver API error: {response.status} - {error_text}")
                    return [TextContent(
                        type="text",
                        text=f"API 오류 (상태 코드: {response.status}): {error_text}"
                    )]
                
                data = await response.json()
                return self._format_news_results(data, query)
                
        except aiohttp.ClientError as e:
            logger.error(f"Network error: {str(e)}")
            return [TextContent(
                type="text",
                text=f"네트워크 오류: {str(e)}"
            )]
    
    def _get_categories(self) -> Dict[str, Any]:
        """카테고리 정보를 반환합니다."""
        return {
            "categories": [
                {
                    "id": cat_id,
                    "name": cat_info["name"],
                    "description": cat_info["description"],
                    "sample_keywords": cat_info["keywords"]
                }
                for cat_id, cat_info in self.NEWS_CATEGORIES.items()
            ]
        }
    
    def _list_categories(self) -> List[TextContent]:
        """카테고리 목록을 텍스트 형식으로 반환합니다."""
        lines = [
            "## 📂 뉴스 카테고리 목록",
            "",
            "다음 카테고리로 뉴스를 검색할 수 있습니다:",
            ""
        ]
        
        for cat_id, cat_info in self.NEWS_CATEGORIES.items():
            lines.append(f"### {cat_info['name']} (`{cat_id}`)")
            lines.append(f"- **설명:** {cat_info['description']}")
            lines.append(f"- **관련 키워드:** {', '.join(cat_info['keywords'])}")
            lines.append("")
        
        lines.append("---")
        lines.append("💡 **사용법:** `get_category_news` 도구에서 category 파라미터에 카테고리 ID를 사용하세요.")
        lines.append("예: `get_category_news(category='tech')` → IT/과학 뉴스 검색")
        
        return [TextContent(type="text", text="\n".join(lines))]
    
    async def _get_category_news(
        self,
        category: str,
        display: int = 10,
        sort: str = "date"
    ) -> List[TextContent]:
        """
        특정 카테고리의 뉴스를 검색합니다.
        
        Args:
            category: 카테고리 ID
            display: 표시할 결과 개수
            sort: 정렬 방법
        """
        if category not in self.NEWS_CATEGORIES:
            available = ", ".join(self.NEWS_CATEGORIES.keys())
            return [TextContent(
                type="text",
                text=f"오류: 알 수 없는 카테고리 '{category}'. 사용 가능한 카테고리: {available}"
            )]
        
        cat_info = self.NEWS_CATEGORIES[category]
        # 카테고리의 첫 번째 키워드로 검색
        query = cat_info["keywords"][0]
        
        return await self._search_news(
            query=query,
            display=display,
            sort=sort
        )
    
    def _clean_html_tags(self, text: str) -> str:
        """HTML 태그를 제거합니다."""
        # <b> 태그 등 HTML 태그 제거
        clean_text = re.sub(r'<[^>]+>', '', text)
        # HTML 엔티티 변환
        clean_text = clean_text.replace("&quot;", '"')
        clean_text = clean_text.replace("&amp;", '&')
        clean_text = clean_text.replace("&lt;", '<')
        clean_text = clean_text.replace("&gt;", '>')
        clean_text = clean_text.replace("&apos;", "'")
        return clean_text
    
    def _format_news_results(self, data: Dict[str, Any], query: str) -> List[TextContent]:
        """API 응답을 읽기 좋은 형식으로 포맷팅합니다."""
        total = data.get("total", 0)
        start = data.get("start", 1)
        display = data.get("display", 0)
        items = data.get("items", [])
        
        if not items:
            return [TextContent(
                type="text",
                text=f"'{query}'에 대한 검색 결과가 없습니다."
            )]
        
        result_lines = [
            f"## 📰 '{query}' 뉴스 검색 결과",
            f"총 {total:,}개의 결과 중 {start}~{start + len(items) - 1}번째 결과",
            ""
        ]
        
        for i, item in enumerate(items, start):
            title = self._clean_html_tags(item.get("title", "제목 없음"))
            description = self._clean_html_tags(item.get("description", "내용 없음"))
            original_link = item.get("originallink", "")
            naver_link = item.get("link", "")
            pub_date = item.get("pubDate", "알 수 없음")
            
            result_lines.append(f"### {i}. {title}")
            result_lines.append(f"**발행일:** {pub_date}")
            result_lines.append(f"**요약:** {description}")
            if original_link:
                result_lines.append(f"**원문 링크:** {original_link}")
            if naver_link and naver_link != original_link:
                result_lines.append(f"**네이버 뉴스:** {naver_link}")
            result_lines.append("")
        
        return [TextContent(type="text", text="\n".join(result_lines))]
    
    async def _generate_news_report(
        self,
        topic: str,
        keywords: Optional[List[str]] = None,
        num_articles: int = 15,
        include_links: bool = True
    ) -> List[TextContent]:
        """
        특정 주제에 대한 뉴스 레포트를 생성합니다.
        
        Args:
            topic: 레포트 주제
            keywords: 추가 검색 키워드 (선택)
            num_articles: 수집할 기사 수
            include_links: 링크 포함 여부
        """
        await self._ensure_session()
        
        # API 자격 증명 확인
        if not self.client_id or not self.client_secret:
            return [TextContent(
                type="text",
                text="오류: 네이버 API 자격 증명이 설정되지 않았습니다. "
                     "환경 변수 news_client_id와 news_client_secret을 설정해주세요."
            )]
        
        # 파라미터 유효성 검사
        num_articles = max(5, min(num_articles, 50))
        
        # 검색 키워드 준비 (주제 + 추가 키워드)
        search_queries = [topic]
        if keywords:
            search_queries.extend(keywords[:3])  # 최대 3개의 추가 키워드
        
        # 모든 수집된 기사를 저장
        all_articles = []
        seen_titles = set()  # 중복 제거용
        
        # 각 키워드로 검색 수행
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret
        }
        
        articles_per_query = max(5, num_articles // len(search_queries))
        
        for query in search_queries:
            params = {
                "query": query,
                "display": min(articles_per_query, 100),
                "start": 1,
                "sort": "date"  # 최신순으로 검색
            }
            
            try:
                async with self.session.get(
                    NAVER_API_BASE_URL,
                    headers=headers,
                    params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get("items", [])
                        
                        for item in items:
                            title = self._clean_html_tags(item.get("title", ""))
                            # 중복 제거
                            if title and title not in seen_titles:
                                seen_titles.add(title)
                                all_articles.append({
                                    "title": title,
                                    "description": self._clean_html_tags(item.get("description", "")),
                                    "originallink": item.get("originallink", ""),
                                    "link": item.get("link", ""),
                                    "pubDate": item.get("pubDate", ""),
                                    "search_query": query
                                })
            except aiohttp.ClientError as e:
                logger.error(f"Network error for query '{query}': {str(e)}")
                continue
        
        if not all_articles:
            return [TextContent(
                type="text",
                text=f"'{topic}'에 대한 뉴스 기사를 찾을 수 없습니다."
            )]
        
        # 수집된 기사 수 제한
        all_articles = all_articles[:num_articles]
        
        # 레포트 생성
        report = self._build_report(topic, search_queries, all_articles, include_links)
        
        return [TextContent(type="text", text=report)]
    
    async def _search_and_summarize_news(
        self,
        keywords: List[str],
        num_articles: int = 5,
        include_keyword_curation: bool = True
    ) -> List[TextContent]:
        """
        키워드로 뉴스를 검색하고 Claude AI로 요약합니다.
        
        Args:
            keywords: 검색할 키워드 목록
            num_articles: 키워드당 검색할 기사 수
            include_keyword_curation: 키워드별 큐레이션 포함 여부
        """
        await self._ensure_session()
        
        # API 자격 증명 확인
        if not self.client_id or not self.client_secret:
            return [TextContent(
                type="text",
                text="오류: 네이버 API 자격 증명이 설정되지 않았습니다. "
                     "환경 변수 news_client_id와 news_client_secret을 설정해주세요."
            )]
        
        if not self.claude_client:
            return [TextContent(
                type="text",
                text="오류: Claude API 키가 설정되지 않았습니다. "
                     "환경 변수 claude_api_key를 설정해주세요."
            )]
        
        # 파라미터 유효성 검사
        num_articles = max(1, min(num_articles, 10))
        keywords = keywords[:5]  # 최대 5개 키워드
        
        # 각 키워드별로 뉴스 검색
        all_news_data = []
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret
        }
        
        for keyword in keywords:
            params = {
                "query": keyword,
                "display": num_articles,
                "start": 1,
                "sort": "date"
            }
            
            try:
                async with self.session.get(
                    NAVER_API_BASE_URL,
                    headers=headers,
                    params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get("items", [])
                        
                        for idx, item in enumerate(items, 1):
                            all_news_data.append({
                                "id": f"{keyword}_{idx}",
                                "keyword": keyword,
                                "제목": self._clean_html_tags(item.get("title", "")),
                                "본문": self._clean_html_tags(item.get("description", "")),
                                "link": item.get("originallink", "") or item.get("link", ""),
                                "pubDate": item.get("pubDate", "")
                            })
            except aiohttp.ClientError as e:
                logger.error(f"Network error for keyword '{keyword}': {str(e)}")
                continue
        
        if not all_news_data:
            return [TextContent(
                type="text",
                text=f"검색된 뉴스가 없습니다. 키워드: {', '.join(keywords)}"
            )]
        
        # Claude API로 요약 요청
        try:
            summary_result = await self._summarize_with_claude(
                all_news_data, 
                keywords if include_keyword_curation else None
            )
            return [TextContent(type="text", text=summary_result)]
        except Exception as e:
            logger.error(f"Claude API error: {str(e)}")
            return [TextContent(
                type="text",
                text=f"요약 생성 중 오류가 발생했습니다: {str(e)}"
            )]
    
    async def _summarize_with_claude(
        self,
        news_data: List[Dict[str, Any]],
        keywords_for_curation: Optional[List[str]] = None
    ) -> str:
        """Claude API를 사용하여 뉴스를 요약합니다."""
        
        # 뉴스 데이터를 Claude에 전달할 형식으로 변환
        news_list = []
        for item in news_data:
            news_list.append({
                "id": item["id"],
                "제목": item["제목"],
                "본문": item["본문"]
            })
        
        # 사용자 메시지 구성
        user_message_parts = []
        user_message_parts.append("다음 뉴스 기사들을 분석하고 요약해주세요.\n")
        user_message_parts.append(f"news_list: {json.dumps(news_list, ensure_ascii=False)}\n")
        
        if keywords_for_curation:
            user_message_parts.append(f"\nkeywords: {json.dumps(keywords_for_curation, ensure_ascii=False)}")
        
        user_message = "\n".join(user_message_parts)
        
        # Claude API 호출 (동기 방식으로 별도 스레드에서 실행)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.claude_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=20000,
                system=self.summary_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ]
            )
        )
        
        # 응답 추출
        result_text = response.content[0].text
        
        # 결과를 보기 좋게 포맷팅
        formatted_result = self._format_summary_result(result_text, news_data)
        
        return formatted_result
    
    def _format_summary_result(
        self,
        claude_response: str,
        original_news: List[Dict[str, Any]]
    ) -> str:
        """Claude 응답을 보기 좋게 포맷팅합니다."""
        from datetime import datetime
        
        lines = [
            "=" * 60,
            "# 📰 AI 뉴스 요약 리포트",
            "=" * 60,
            "",
            f"**생성일시:** {datetime.now().strftime('%Y년 %m월 %d일 %H:%M')}",
            f"**분석 기사 수:** {len(original_news)}개",
            "",
            "---",
            "",
            "## 📋 Claude AI 분석 결과",
            "",
        ]
        
        # Claude 응답 추가
        lines.append(claude_response)
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 원문 링크 섹션 추가
        lines.append("## 🔗 원문 링크")
        lines.append("")
        
        # 키워드별로 그룹화
        keywords_seen = {}
        for item in original_news:
            keyword = item.get("keyword", "기타")
            if keyword not in keywords_seen:
                keywords_seen[keyword] = []
            keywords_seen[keyword].append(item)
        
        for keyword, items in keywords_seen.items():
            lines.append(f"### 🏷️ {keyword}")
            for item in items:
                lines.append(f"- [{item['제목']}]({item['link']})")
                lines.append(f"  📅 {item['pubDate']}")
            lines.append("")
        
        lines.append("---")
        lines.append("*본 요약은 Claude AI를 활용하여 자동 생성되었습니다.*")
        
        return "\n".join(lines)
    
    def _build_report(
        self,
        topic: str,
        search_queries: List[str],
        articles: List[Dict[str, Any]],
        include_links: bool
    ) -> str:
        """레포트 문서를 생성합니다."""
        from datetime import datetime
        
        lines = [
            "=" * 60,
            f"# 📊 뉴스 리서치 레포트: {topic}",
            "=" * 60,
            "",
            f"**생성일시:** {datetime.now().strftime('%Y년 %m월 %d일 %H:%M')}",
            f"**분석 기사 수:** {len(articles)}개",
            f"**검색 키워드:** {', '.join(search_queries)}",
            "",
            "---",
            "",
            "## 📋 레포트 개요",
            "",
            f"본 레포트는 '{topic}'에 관한 최신 뉴스 기사를 수집하여 정리한 것입니다.",
            "아래에서 주요 뉴스 내용과 핵심 포인트를 확인하실 수 있습니다.",
            "",
            "---",
            "",
            "## 📰 주요 뉴스 요약",
            ""
        ]
        
        # 기사별 요약
        for i, article in enumerate(articles, 1):
            lines.append(f"### {i}. {article['title']}")
            lines.append(f"📅 **발행일:** {article['pubDate']}")
            lines.append(f"")
            lines.append(f"**내용 요약:**")
            lines.append(f"> {article['description']}")
            lines.append(f"")
            
            if include_links:
                if article['originallink']:
                    lines.append(f"🔗 [원문 보기]({article['originallink']})")
                if article['link'] and article['link'] != article['originallink']:
                    lines.append(f"🔗 [네이버 뉴스]({article['link']})")
            
            lines.append("")
            lines.append("---")
            lines.append("")
        
        # 레포트 푸터
        lines.extend([
            "",
            "---",
            f"*본 레포트는 네이버 뉴스 검색 API를 통해 자동 생성되었습니다.*"
        ])
        
        return "\n".join(lines)
    
    async def run(self):
        """Run the MCP server"""
        # Setup initialization options
        init_options = InitializationOptions(
            server_name="news-mcp-server",
            server_version="1.0.0",
            capabilities={
                "resources": {},
                "tools": {},
                "prompts": {},
                "logging": {}
            }
        )
        
        async with stdio_server() as (read_stream, write_stream):
            await self.app.run(
                read_stream,
                write_stream,
                init_options
            )


async def main():
    """Main entry point"""
    server = NewsMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
