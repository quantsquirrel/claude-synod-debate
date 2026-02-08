#!/usr/bin/env python3
"""
Groq CLI with robust timeout handling and retry logic.

Usage:
  echo "prompt" | groq-cli [--model MODEL]
  groq-cli "prompt" [--model MODEL]
  groq-cli --prompt "prompt" [--model MODEL]

Models: 8b (default), 70b, mixtral
Timeout: 자동 설정 (8b: 30s, 70b: 45s, mixtral: 60s)

Examples:
  echo "2+2는?" | groq-cli
  echo "긴 문서 요약" | groq-cli --model mixtral
  groq-cli "간단한 질문" --model 8b
  groq-cli --prompt "간단한 질문" --model 70b
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

# Import model stats for latency tracking
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from model_stats import ModelStats

    _stats_available = True
except ImportError:
    _stats_available = False

# API 키 (main()에서 검증)
api_key = os.environ.get("GROQ_API_KEY")

PROVIDER = "groq"

# 모델 매핑
MODEL_MAP = {
    "8b": "llama-3.1-8b-instant",
    "70b": "llama-3.1-70b-versatile",
    "mixtral": "mixtral-8x7b-32768",
}


def get_model_with_override(model_key: str) -> str:
    """Get model name with optional environment variable override."""
    env_var = f"SYNOD_GROQ_{model_key.upper()}"
    override = os.environ.get(env_var)
    if override:
        print(f"[Override] Using {override} (via {env_var})", file=sys.stderr)
        return override
    return MODEL_MAP.get(model_key, MODEL_MAP["8b"])

# 모델별 타임아웃 설정 (초) - Groq는 초고속!
TIMEOUT_CONFIG = {
    "8b": 30,
    "70b": 45,
    "mixtral": 60,
}


def create_client(timeout_sec: int) -> OpenAI:
    """Create Groq client with timeout."""
    return OpenAI(
        api_key=api_key.strip(),
        base_url="https://api.groq.com/openai/v1",
        timeout=httpx.Timeout(timeout_sec, connect=10.0),
    )


def generate_with_retry(model: str, prompt: str, timeout: int, max_retries: int = 3) -> str:
    """Generate content with retry on timeout and rate limits."""

    for attempt in range(max_retries):
        try:
            # Calculate timeout for current model
            actual_timeout = TIMEOUT_CONFIG.get(model, timeout)
            client = create_client(actual_timeout)

            # Build request params (with env override support)
            model_name = get_model_with_override(model)
            request_params = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,  # Groq는 스트리밍이 더 빠름
            }

            # Generate response with streaming
            response = client.chat.completions.create(**request_params)

            full_response = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    print(content, end="", flush=True)
                    full_response += content

            print()  # 마지막 줄바꿈

            if full_response:
                return full_response
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
        description="Groq CLI with dynamic model selection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Models:
  8b      - 초고속, 경제적 (llama-3.1-8b-instant, 기본값)
  70b     - 균형잡힌 성능 (llama-3.1-70b-versatile)
  mixtral - 긴 컨텍스트 32K (mixtral-8x7b-32768)

Groq 특징:
  - 초고속 추론 (세계 최고 속도)
  - OpenAI 호환 API
  - 무료 티어 제공
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
        choices=["8b", "70b", "mixtral"],
        default="8b",
        help="사용할 모델 (기본값: 8b)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="타임아웃(초) - 기본값은 모델에 따라 자동 설정 (8b:30s, 70b:45s, mixtral:60s)",
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
        sys.stderr.write("Usage: groq-cli 'prompt' [--model 8b|70b|mixtral]\n")
        sys.stderr.write("       groq-cli --prompt 'prompt' [options]\n")
        sys.stderr.write("       echo 'prompt' | groq-cli [options]\n")
        sys.exit(1)

    if not prompt:
        sys.stderr.write("Error: 프롬프트가 비어있습니다.\n")
        sys.exit(1)

    # API 키 검증
    if not api_key:
        sys.stderr.write("Error: GROQ_API_KEY 환경 변수가 설정되지 않았습니다.\n")
        sys.exit(1)

    # 타임아웃 설정
    timeout = args.timeout if args.timeout else TIMEOUT_CONFIG[args.model]

    # Dynamic timeout: use stats-based P99+epsilon if available
    if _stats_available and os.environ.get("SYNOD_V2_ADAPTIVE_TIMEOUT", "0") == "1":
        stats = ModelStats()
        if stats.has_sufficient_data(PROVIDER, args.model):
            timeout = int(stats.get_dynamic_timeout(PROVIDER, args.model) / 1000)
            if args.verbose:
                print(f"[Adaptive] Timeout: {timeout}s (P99+epsilon)", file=sys.stderr)

    if args.verbose:
        print(f"Model: {get_model_with_override(args.model)}", file=sys.stderr)
        print(f"Timeout: {timeout}s", file=sys.stderr)
        print("Base URL: https://api.groq.com/openai/v1", file=sys.stderr)

    # Generate with retry + latency tracking
    start_time = time.time()
    generate_with_retry(model=args.model, prompt=prompt, timeout=timeout, max_retries=args.retries)

    # Record latency stats
    if _stats_available:
        latency_ms = (time.time() - start_time) * 1000
        try:
            ModelStats().record_latency(PROVIDER, args.model, latency_ms)
        except Exception:
            pass


if __name__ == "__main__":
    main()
