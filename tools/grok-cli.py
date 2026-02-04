#!/usr/bin/env python3
"""
xAI Grok CLI with robust timeout handling and adaptive retry.

Usage:
  echo "prompt" | grok-cli [--model MODEL]
  grok-cli "prompt" [--model MODEL]
  grok-cli --prompt "prompt" [--model MODEL]

Models: fast (default), grok4, mini, vision
Timeout: auto-selected based on model

Examples:
  echo "2+2는?" | grok-cli
  echo "복잡한 질문" | grok-cli --model grok4
  grok-cli "간단한 질문" --model fast
  grok-cli --prompt "이미지 분석" --model vision
"""

import argparse
import os
import random
import sys
import time

# Check if Grok CLI is enabled BEFORE importing heavy dependencies
if not os.environ.get("SYNOD_ENABLE_GROK"):
    sys.stderr.write(
        "[Grok CLI] 비활성화 상태입니다.\n"
        "활성화하려면: export SYNOD_ENABLE_GROK=1\n"
        "또는 OpenRouter를 통해 사용: openrouter-cli --model grok\n"
    )
    sys.exit(2)

try:
    import httpx
    from openai import OpenAI
except ImportError:
    sys.stderr.write("Error: openai 패키지가 설치되지 않았습니다.\n")
    sys.stderr.write("설치: pip install openai\n")
    sys.exit(1)

# API 키 (main()에서 검증)
api_key = os.environ.get("XAI_API_KEY")

# xAI API base URL
BASE_URL = "https://api.x.ai/v1"

# 모델 매핑 (2026년 1월 기준)
MODEL_MAP = {
    "fast": "grok-4-fast",  # 빠르고 저렴 ($0.20/$0.50), 2M context
    "grok4": "grok-4",  # 최고 성능 ($3/$15), 2M context
    "mini": "grok-3-mini",  # 초경량
    "vision": "grok-2-vision-1212",  # 비전 모델
}

# 모델별 타임아웃 설정 (초)
TIMEOUT_CONFIG = {
    "fast": 60,
    "mini": 60,
    "grok4": 120,
    "vision": 90,
}


def create_client(timeout_sec: int) -> OpenAI:
    """Create xAI Grok client with timeout."""
    return OpenAI(
        api_key=api_key.strip(), base_url=BASE_URL, timeout=httpx.Timeout(timeout_sec, connect=10.0)
    )


def generate_with_retry(model: str, prompt: str, timeout: int, max_retries: int = 3) -> str:
    """Generate content with retry on timeout."""

    for attempt in range(max_retries):
        try:
            # Calculate timeout for current model
            actual_timeout = TIMEOUT_CONFIG.get(model, timeout)
            client = create_client(actual_timeout)

            # Build request params
            model_name = MODEL_MAP[model]
            request_params = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            }

            # Generate response
            response = client.chat.completions.create(**request_params)

            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
            else:
                raise Exception("Empty response")

        except Exception as e:
            error_str = str(e).lower()
            error_type = type(e).__name__

            # Check for retryable errors
            is_timeout = any(x in error_str for x in ["timeout", "timed out", "deadline"])
            is_rate_limit = any(x in error_str for x in ["429", "rate", "quota"])
            is_overloaded = any(x in error_str for x in ["503", "overloaded", "unavailable", "502"])

            if is_timeout or is_overloaded:
                print(
                    f"[Retry {attempt + 1}/{max_retries}] {error_type}: Retrying...",
                    file=sys.stderr,
                )
                # Exponential backoff with jitter
                wait_time = (2**attempt) + random.random()
                time.sleep(wait_time)
                continue

            elif is_rate_limit:
                wait_time = (2 ** (attempt + 2)) + random.random()
                print(
                    f"[Retry {attempt + 1}/{max_retries}] Rate limited - waiting {wait_time:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
                continue
            else:
                # Non-retryable error
                print(f"Error: {error_type}: {e}", file=sys.stderr)
                sys.exit(1)

    print(f"Error: Max retries ({max_retries}) exceeded", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="xAI Grok CLI with dynamic model selection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Models:
  fast    - 빠르고 저렴 (기본값, $0.20/$0.50 per 1M, 2M context)
  grok4   - 최고 성능 ($3/$15 per 1M, 2M context)
  mini    - 초경량 모델
  vision  - 비전 모델 (이미지 분석)

Features:
  - 2M 토큰 컨텍스트 (grok-4, grok-4-fast)
  - OpenAI 호환 API
  - 자동 재시도 및 타임아웃 관리
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
        choices=["fast", "grok4", "mini", "vision"],
        default="fast",
        help="사용할 모델 (기본값: fast)",
    )
    parser.add_argument(
        "--timeout", type=int, default=None, help="타임아웃(초) - 기본값은 모델에 따라 자동 설정"
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
        sys.stderr.write("Usage: grok-cli 'prompt' [--model fast|grok4|mini|vision]\n")
        sys.stderr.write("       grok-cli --prompt 'prompt' [options]\n")
        sys.stderr.write("       echo 'prompt' | grok-cli [options]\n")
        sys.exit(1)

    if not prompt:
        sys.stderr.write("Error: 프롬프트가 비어있습니다.\n")
        sys.exit(1)

    # API 키 검증 (이 시점에서만 필요)
    if not api_key:
        sys.stderr.write("Error: XAI_API_KEY 환경 변수가 설정되지 않았습니다.\n")
        sys.exit(1)

    # Set default timeout if not specified
    if args.timeout is None:
        args.timeout = TIMEOUT_CONFIG.get(args.model, 60)

    if args.verbose:
        print(f"Model: {MODEL_MAP[args.model]}", file=sys.stderr)
        print(f"Timeout: {args.timeout}s", file=sys.stderr)
        print(f"Base URL: {BASE_URL}", file=sys.stderr)

    # Generate with retry
    response = generate_with_retry(
        model=args.model, prompt=prompt, timeout=args.timeout, max_retries=args.retries
    )

    print(response)


if __name__ == "__main__":
    main()
