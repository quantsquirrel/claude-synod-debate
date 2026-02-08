#!/usr/bin/env python3
"""
DeepSeek CLI with robust timeout handling and adaptive retry.

Usage:
  echo "prompt" | deepseek-cli [--model MODEL] [--reasoning LEVEL]
  deepseek-cli "prompt" [--model MODEL] [--reasoning LEVEL]
  deepseek-cli --prompt "prompt" [--model MODEL] [--reasoning LEVEL]

Models: chat (default), reasoner
Reasoning: low, medium (default), high (reasoner only)

Examples:
  echo "2+2는?" | deepseek-cli
  echo "복잡한 수학" | deepseek-cli --model reasoner --reasoning high
  deepseek-cli "간단한 질문" --model chat
  deepseek-cli --prompt "간단한 질문" --model chat
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
api_key = os.environ.get("DEEPSEEK_API_KEY")

PROVIDER = "deepseek"

# 모델 매핑
MODEL_MAP = {"chat": "deepseek-chat", "reasoner": "deepseek-reasoner"}


def get_model_with_override(model_key: str) -> str:
    """Get model name with optional environment variable override."""
    env_var = f"SYNOD_DEEPSEEK_{model_key.upper()}"
    override = os.environ.get(env_var)
    if override:
        print(f"[Override] Using {override} (via {env_var})", file=sys.stderr)
        return override
    return MODEL_MAP.get(model_key, MODEL_MAP["chat"])

# Reasoning Effort는 reasoner 모델에서만 작동
REASONER_MODELS = ["reasoner"]

# 모델별 타임아웃 설정 (초)
TIMEOUT_CONFIG = {
    ("chat", "low"): 60,
    ("chat", "medium"): 60,
    ("chat", "high"): 120,
    ("reasoner", "low"): 180,
    ("reasoner", "medium"): 300,
    ("reasoner", "high"): 600,
}

# Reasoning 레벨 (다운그레이드용)
REASONING_LEVELS = ["high", "medium", "low"]


def create_client(timeout_sec: int) -> OpenAI:
    """Create DeepSeek client with timeout."""
    return OpenAI(
        api_key=api_key.strip(),
        base_url="https://api.deepseek.com",
        timeout=httpx.Timeout(timeout_sec, connect=10.0),
    )


def generate_with_retry(
    model: str,
    prompt: str,
    reasoning: str,
    timeout: int,
    max_retries: int = 3,
    adaptive: bool = True,
) -> str:
    """Generate content with adaptive retry on timeout."""

    current_reasoning = reasoning
    current_idx = (
        REASONING_LEVELS.index(current_reasoning) if current_reasoning in REASONING_LEVELS else 1
    )

    for attempt in range(max_retries):
        try:
            # Calculate timeout for current config
            actual_timeout = TIMEOUT_CONFIG.get((model, current_reasoning), timeout)
            client = create_client(actual_timeout)

            # Build request params (with env override support)
            model_name = get_model_with_override(model)
            request_params = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
            }

            # Add reasoning_effort for reasoner model
            if model in REASONER_MODELS:
                request_params["reasoning_effort"] = current_reasoning
                # Reasoner requires streaming
                request_params["stream"] = True

                # Stream response
                stream = client.chat.completions.create(**request_params)
                content = ""
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content += chunk.choices[0].delta.content

                if content:
                    return content
                else:
                    raise Exception("Empty response")
            else:
                # Non-streaming for chat model
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
                if (
                    adaptive
                    and model in REASONER_MODELS
                    and current_idx < len(REASONING_LEVELS) - 1
                ):
                    # Downgrade reasoning level
                    current_idx += 1
                    current_reasoning = REASONING_LEVELS[current_idx]
                    print(
                        f"[Retry {attempt + 1}/{max_retries}] Timeout - downgrading reasoning to '{current_reasoning}'",
                        file=sys.stderr,
                    )
                else:
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
        description="DeepSeek CLI with dynamic model selection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Models:
  chat      - 범용 대화형 모델 (기본값, V3)
  reasoner  - 추론 특화 모델 (R1, 수학/논리에 최적)

Reasoning Levels (reasoner only):
  low     - 속도 우선, 경제적 (180초)
  medium  - 균형 (기본값, 300초)
  high    - 최대 추론 깊이 (600초)
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
        choices=["chat", "reasoner"],
        default="chat",
        help="사용할 모델 (기본값: chat)",
    )
    parser.add_argument(
        "--reasoning",
        "-r",
        choices=["low", "medium", "high"],
        default="medium",
        help="Reasoning 레벨 - reasoner 모델 전용 (기본값: medium)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="타임아웃(초) - 기본값은 모델/레벨에 따라 자동 설정",
    )
    parser.add_argument("--retries", type=int, default=3, help="최대 재시도 횟수 (기본값: 3)")
    parser.add_argument(
        "--no-adaptive",
        action="store_true",
        help="적응형 재시도 비활성화 (reasoning 레벨 다운그레이드 안함)",
    )
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
            "Usage: deepseek-cli 'prompt' [--model chat|reasoner] [--reasoning low|medium|high]\n"
        )
        sys.stderr.write("       deepseek-cli --prompt 'prompt' [options]\n")
        sys.stderr.write("       echo 'prompt' | deepseek-cli [options]\n")
        sys.exit(1)

    if not prompt:
        sys.stderr.write("Error: 프롬프트가 비어있습니다.\n")
        sys.exit(1)

    # API 키 검증 (이 시점에서만 필요)
    if not api_key:
        sys.stderr.write("Error: DEEPSEEK_API_KEY 환경 변수가 설정되지 않았습니다.\n")
        sys.exit(1)

    if args.verbose:
        print(f"Model: {MODEL_MAP[args.model]}", file=sys.stderr)
        print(f"Reasoning: {args.reasoning}", file=sys.stderr)
        print(
            f"Timeout: {TIMEOUT_CONFIG.get((args.model, args.reasoning), args.timeout)}s",
            file=sys.stderr,
        )

    # Dynamic timeout: use stats-based P99+epsilon if available
    effective_timeout = args.timeout
    if _stats_available and os.environ.get("SYNOD_V2_ADAPTIVE_TIMEOUT", "0") == "1":
        stats = ModelStats()
        if stats.has_sufficient_data(PROVIDER, args.model):
            effective_timeout = int(stats.get_dynamic_timeout(PROVIDER, args.model) / 1000)
            if args.verbose:
                print(f"[Adaptive] Timeout: {effective_timeout}s (P99+epsilon)", file=sys.stderr)

    # Generate with retry + latency tracking
    start_time = time.time()
    response = generate_with_retry(
        model=args.model,
        prompt=prompt,
        reasoning=args.reasoning,
        timeout=effective_timeout,
        max_retries=args.retries,
        adaptive=not args.no_adaptive,
    )

    # Record latency stats
    if _stats_available:
        latency_ms = (time.time() - start_time) * 1000
        try:
            ModelStats().record_latency(PROVIDER, args.model, latency_ms)
        except Exception:
            pass

    print(response)


if __name__ == "__main__":
    main()
