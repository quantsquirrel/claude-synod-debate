#!/usr/bin/env python3
"""
OpenRouter CLI with multi-model support and robust timeout handling.

Usage:
  echo "prompt" | openrouter-cli [--model MODEL]
  openrouter-cli "prompt" [--model MODEL]
  openrouter-cli --prompt "prompt" [--model MODEL]

Models: claude (default), gemini, llama, mistral, qwen, deepseek, grok

Examples:
  echo "2+2는?" | openrouter-cli
  echo "복잡한 질문" | openrouter-cli --model gemini
  openrouter-cli "간단한 질문" --model claude
  openrouter-cli --prompt "간단한 질문" --model llama
"""

import argparse
import os
import random
import sys
import time

try:
    import httpx
    from openai import OpenAI
except ImportError:
    sys.stderr.write("Error: openai 패키지가 설치되지 않았습니다.\n")
    sys.stderr.write("설치: pip install openai\n")
    sys.exit(1)

# API 키 (main()에서 검증)
api_key = os.environ.get("OPENROUTER_API_KEY")

# OpenRouter API 설정
BASE_URL = "https://openrouter.ai/api/v1"

# 모델 매핑 (버전 고정된 slug 사용)
MODEL_MAP = {
    "claude": "anthropic/claude-sonnet-4",
    "gemini": "google/gemini-2.5-flash-preview-05-20",
    "llama": "meta-llama/llama-3.3-70b-instruct",
    "mistral": "mistralai/mistral-large-2411",
    "qwen": "qwen/qwen-2.5-72b-instruct",
    "deepseek": "deepseek/deepseek-chat-v3-0324",
    "grok": "x-ai/grok-2-1212",
}

# OpenRouter 필수 헤더 (default_headers로 자동 주입)
DEFAULT_HEADERS = {
    "HTTP-Referer": "https://github.com/quantsquirrel/claude-synod-debate",
    "X-Title": "Synod CLI",
}

# 기본 타임아웃 설정 (초)
DEFAULT_TIMEOUT = 120


def create_client(timeout_sec: int) -> OpenAI:
    """Create OpenRouter client with timeout and default headers."""
    return OpenAI(
        api_key=api_key.strip(),
        base_url=BASE_URL,
        timeout=httpx.Timeout(timeout_sec, connect=10.0),
        default_headers=DEFAULT_HEADERS,
    )


def generate_with_retry(
    model: str,
    prompt: str,
    timeout: int,
    max_retries: int = 3,
    verbose: bool = False,
) -> str:
    """Generate content with retry on timeout/overload."""

    for attempt in range(max_retries):
        try:
            client = create_client(timeout)

            # Build request params
            model_name = MODEL_MAP[model]
            request_params = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
            }

            if verbose:
                print(f"[Attempt {attempt + 1}] Requesting {model_name}...", file=sys.stderr)

            # Non-streaming request
            response = client.chat.completions.create(**request_params)

            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
            else:
                raise Exception("Empty response from API")

        except Exception as e:
            error_str = str(e).lower()
            error_type = type(e).__name__

            # Check for retryable errors
            is_timeout = any(x in error_str for x in ["timeout", "timed out", "deadline"])
            is_rate_limit = any(x in error_str for x in ["429", "rate", "quota"])
            is_overloaded = any(x in error_str for x in ["503", "overloaded", "unavailable", "502"])

            if is_timeout or is_overloaded:
                if attempt < max_retries - 1:
                    wait_time = (2**attempt) + random.random()
                    print(
                        f"[Retry {attempt + 1}/{max_retries}] {error_type}: Retrying in {wait_time:.1f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"Error: Max retries exceeded - {error_type}: {e}", file=sys.stderr)
                    sys.exit(1)

            elif is_rate_limit:
                if attempt < max_retries - 1:
                    wait_time = (2 ** (attempt + 2)) + random.random()
                    print(
                        f"[Retry {attempt + 1}/{max_retries}] Rate limited - waiting {wait_time:.1f}s",
                        file=sys.stderr,
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"Error: Max retries exceeded - Rate limit", file=sys.stderr)
                    sys.exit(1)
            else:
                # Non-retryable error
                print(f"Error: {error_type}: {e}", file=sys.stderr)
                sys.exit(1)

    print(f"Error: Max retries ({max_retries}) exceeded", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="OpenRouter CLI with multi-model support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supported Models:
  claude    - Anthropic Claude Sonnet 4 (기본값)
  gemini    - Google Gemini 2.5 Flash
  llama     - Meta Llama 3.3 70B
  mistral   - Mistral Large
  qwen      - Qwen 2.5 72B
  deepseek  - DeepSeek Chat V3
  grok      - xAI Grok 2

Environment:
  OPENROUTER_API_KEY - Required API key from openrouter.ai
        """,
    )
    parser.add_argument(
        "positional_prompt",
        nargs="?",
        default=None,
        metavar="prompt",
        help="프롬프트 (positional argument)",
    )
    parser.add_argument(
        "--prompt", "-p", default=None, help="프롬프트 (--prompt 옵션으로도 전달 가능)"
    )
    parser.add_argument(
        "--model",
        "-m",
        choices=list(MODEL_MAP.keys()),
        default="claude",
        help="사용할 모델 (기본값: claude)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"타임아웃(초) (기본값: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument("--retries", type=int, default=3, help="최대 재시도 횟수 (기본값: 3)")
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 출력")

    args, remaining = parser.parse_known_args()

    # 프롬프트 가져오기 (우선순위: stdin > --prompt > positional > remaining)
    prompt = None

    # stdin에서 읽기 시도 (TTY가 아닐 때만)
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read().strip()
        if stdin_content:
            prompt = stdin_content

    # stdin에 내용이 없으면 명령줄 인자 사용
    if not prompt:
        if args.prompt:
            prompt = args.prompt
        elif args.positional_prompt:
            prompt = args.positional_prompt
        elif remaining:
            prompt = " ".join(remaining)

    if not prompt:
        sys.stderr.write(
            "Usage: openrouter-cli 'prompt' [--model MODEL]\n"
        )
        sys.stderr.write("       openrouter-cli --prompt 'prompt' [options]\n")
        sys.stderr.write("       echo 'prompt' | openrouter-cli [options]\n")
        sys.exit(1)

    if not prompt:
        sys.stderr.write("Error: 프롬프트가 비어있습니다.\n")
        sys.exit(1)

    # API 키 검증 (이 시점에서만 필요)
    if not api_key:
        sys.stderr.write("Error: OPENROUTER_API_KEY 환경 변수가 설정되지 않았습니다.\n")
        sys.stderr.write("Get your API key at: https://openrouter.ai/keys\n")
        sys.exit(1)

    if args.verbose:
        print(f"Model: {MODEL_MAP[args.model]}", file=sys.stderr)
        print(f"Timeout: {args.timeout}s", file=sys.stderr)
        print(f"Max Retries: {args.retries}", file=sys.stderr)

    # Generate with retry
    response = generate_with_retry(
        model=args.model,
        prompt=prompt,
        timeout=args.timeout,
        max_retries=args.retries,
        verbose=args.verbose,
    )

    print(response)


if __name__ == "__main__":
    main()
