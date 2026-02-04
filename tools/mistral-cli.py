#!/usr/bin/env python3
"""
Mistral AI CLI with robust timeout handling, streaming, and retry logic.

Usage:
  echo "prompt" | mistral-cli [options]
  mistral-cli "prompt" [options]
  mistral-cli --model medium --temperature 0.5 "prompt"

Models:
  - large: mistral-large-latest (최고 추론, 120s timeout)
  - medium: mistral-medium-3 (균형, 코딩 강점, 90s timeout)
  - small: mistral-small-latest (비용 효율, 60s timeout)
  - codestral: codestral-latest (코드 특화, 90s timeout)
  - devstral: devstral-2 (소프트웨어 엔지니어링, 90s timeout)
"""

import argparse
import os
import sys
import time

# Check if Mistral CLI is enabled BEFORE importing heavy dependencies
if not os.environ.get("SYNOD_ENABLE_MISTRAL"):
    sys.stderr.write(
        "[Mistral CLI] 비활성화 상태입니다.\n"
        "활성화하려면: export SYNOD_ENABLE_MISTRAL=1\n"
        "또는 OpenRouter를 통해 사용: openrouter-cli --model mistral\n"
    )
    sys.exit(2)

# Suppress warnings
import warnings

warnings.filterwarnings("ignore")

try:
    import httpx
    from mistralai import Mistral
except ImportError:
    print("Error: mistralai not installed. Run: pip install mistralai", file=sys.stderr)
    sys.exit(1)

# Model mapping
MODEL_MAP = {
    "large": "mistral-large-latest",
    "medium": "mistral-medium-3",
    "small": "mistral-small-latest",
    "codestral": "codestral-latest",
    "devstral": "devstral-2",
}

# Default timeout per model (seconds)
DEFAULT_TIMEOUT = {
    "large": 120,
    "medium": 90,
    "small": 60,
    "codestral": 90,
    "devstral": 90,
}


def create_client(timeout_seconds: int) -> Mistral:
    """Create Mistral client with timeout."""
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("Error: MISTRAL_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    timeout_config = httpx.Timeout(connect=5.0, read=float(timeout_seconds), write=5.0, pool=5.0)
    http_client = httpx.Client(timeout=timeout_config)

    return Mistral(api_key=api_key, http_client=http_client)


def generate_with_retry(
    client: Mistral,
    model: str,
    prompt: str,
    temperature: float,
    use_streaming: bool,
    max_retries: int = 3,
) -> str:
    """Generate content with retry on timeout."""

    for attempt in range(max_retries):
        try:
            messages = [{"role": "user", "content": prompt}]

            if use_streaming:
                # Streaming mode - prevents timeout for long responses
                stream = client.chat.stream(model=model, messages=messages, temperature=temperature)
                full_response = ""
                for chunk in stream:
                    if chunk.data.choices[0].delta.content:
                        content = chunk.data.choices[0].delta.content
                        full_response += content
                        print(content, end="", flush=True)
                print()  # Final newline
                return full_response
            else:
                # Non-streaming mode
                response = client.chat.complete(
                    model=model, messages=messages, temperature=temperature
                )
                return response.choices[0].message.content

        except Exception as e:
            error_str = str(e).lower()
            error_type = type(e).__name__

            # Check for retryable errors
            is_timeout = any(
                x in error_str for x in ["timeout", "504", "gateway", "deadline", "timed out"]
            )
            is_rate_limit = any(
                x in error_str for x in ["429", "rate", "quota", "resource_exhausted"]
            )
            is_overloaded = any(x in error_str for x in ["503", "overloaded", "unavailable"])

            if is_timeout or is_overloaded:
                wait_time = 2**attempt
                print(f"[Retry {attempt + 1}/{max_retries}] {error_type}: {e}", file=sys.stderr)
                print(f"Waiting {wait_time}s before retry...", file=sys.stderr)
                time.sleep(wait_time)
                continue

            elif is_rate_limit:
                wait_time = 2 ** (attempt + 2)  # Longer backoff for rate limits
                print(
                    f"[Retry {attempt + 1}/{max_retries}] Rate limited - waiting {wait_time}s",
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
        description="Mistral AI CLI with robust error handling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  echo "Hello" | mistral-cli
  mistral-cli "Explain quantum computing"
  mistral-cli --model medium "Write a Python function"
  mistral-cli --model codestral --temperature 0.3 "Refactor this code"
  mistral-cli --no-stream "Quick response"
        """,
    )
    parser.add_argument("prompt", nargs="?", default=None, help="Prompt text")
    parser.add_argument(
        "-m",
        "--model",
        choices=["large", "medium", "small", "codestral", "devstral"],
        default="medium",
        help="Model to use (default: medium)",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.7, help="Temperature for generation (default: 0.7)"
    )
    parser.add_argument(
        "--timeout", type=int, default=None, help="Timeout in seconds (default: model-specific)"
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming (not recommended for long prompts)",
    )
    parser.add_argument("--retries", type=int, default=3, help="Max retries (default: 3)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args, remaining = parser.parse_known_args()

    # Get prompt from args, remaining args, or stdin
    if args.prompt:
        prompt = args.prompt
    elif remaining:
        prompt = " ".join(remaining)
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        parser.print_help()
        sys.exit(1)

    if not prompt:
        print("Error: No prompt provided", file=sys.stderr)
        sys.exit(1)

    # Determine timeout
    timeout = args.timeout if args.timeout else DEFAULT_TIMEOUT[args.model]

    # Create client with timeout
    client = create_client(timeout)

    # Get model name
    model_name = MODEL_MAP[args.model]

    if args.verbose:
        print(f"Model: {model_name}", file=sys.stderr)
        print(f"Temperature: {args.temperature}", file=sys.stderr)
        print(f"Streaming: {not args.no_stream}", file=sys.stderr)
        print(f"Timeout: {timeout}s", file=sys.stderr)

    # Generate response
    if not args.no_stream:
        # Streaming prints inline, return value is for consistency
        generate_with_retry(
            client=client,
            model=model_name,
            prompt=prompt,
            temperature=args.temperature,
            use_streaming=True,
            max_retries=args.retries,
        )
    else:
        # Non-streaming prints at the end
        response = generate_with_retry(
            client=client,
            model=model_name,
            prompt=prompt,
            temperature=args.temperature,
            use_streaming=False,
            max_retries=args.retries,
        )
        print(response)


if __name__ == "__main__":
    main()
