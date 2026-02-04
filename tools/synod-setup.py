#!/usr/bin/env python3
"""
synod-setup - Synod 초기 설정 및 모델 가용성 테스트

Usage:
    synod-setup
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 테스트할 모델 정의
MODELS_TO_TEST = {
    "gemini": {
        "cli": "gemini-3.py",
        "models": ["flash", "pro"],
        "env_key": "GOOGLE_API_KEY",
    },
    "openai": {
        "cli": "openai-cli.py",
        "models": ["gpt4o", "o3"],
        "env_key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "cli": "deepseek-cli.py",
        "models": ["chat", "reasoner"],
        "env_key": "DEEPSEEK_API_KEY",
    },
    "groq": {
        "cli": "groq-cli.py",
        "models": ["70b", "8b"],
        "env_key": "GROQ_API_KEY",
    },
    "grok": {
        "cli": "grok-cli.py",
        "models": ["fast", "grok4"],
        "env_key": "XAI_API_KEY",
    },
    "mistral": {
        "cli": "mistral-cli.py",
        "models": ["large", "small"],
        "env_key": "MISTRAL_API_KEY",
    },
    "openrouter": {
        "cli": "openrouter-cli.py",
        "models": ["claude", "llama", "qwen"],
        "env_key": "OPENROUTER_API_KEY",
    },
}

# 테스트 대상 모델 (Gemini + OpenAI + OpenRouter 핵심 모델)
TEST_TARGETS = [
    ("gemini", "flash"),
    ("gemini", "pro"),
    ("openai", "gpt4o"),
    ("openai", "o3"),
    ("openrouter", "claude"),
]

# 테스트 프롬프트 (Synod Solver와 유사한 복잡도)
TEST_PROMPT = "Explain the SOLID principles in software engineering in 3 sentences."

# 타임아웃 기준 (초)
TIMEOUT_THRESHOLD = 120
SLOW_THRESHOLD = 60


@dataclass
class TestResult:
    provider: str
    model: str
    success: bool
    latency_sec: float
    status: str  # "recommended" | "usable" | "slow" | "timeout" | "failed"
    error: str | None = None


def check_cli_exists(provider: str) -> tuple[bool, str]:
    """CLI 도구 존재 확인."""
    cli_name = MODELS_TO_TEST[provider]["cli"]
    tools_dir = Path(__file__).parent
    cli_path = tools_dir / cli_name
    return cli_path.exists(), str(cli_path)


def check_api_key(provider: str) -> tuple[bool, str]:
    """API 키 환경변수 확인."""
    env_key = MODELS_TO_TEST[provider]["env_key"]
    has_key = os.environ.get(env_key) is not None
    return has_key, env_key


def test_model(provider: str, model: str, timeout: int = TIMEOUT_THRESHOLD) -> TestResult:
    """모델에 테스트 프롬프트 전송 및 응답 시간 측정."""
    cli_name = MODELS_TO_TEST[provider]["cli"]
    cli_path = Path(__file__).parent / cli_name

    start_time = time.time()
    try:
        result = subprocess.run(
            ["python3", str(cli_path), "--model", model, TEST_PROMPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        latency = time.time() - start_time

        if result.returncode == 0:
            # 응답 시간 기준 분류
            if latency < 10:
                status = "recommended"
            elif latency < SLOW_THRESHOLD:
                status = "usable"
            else:
                status = "slow"

            return TestResult(
                provider=provider,
                model=model,
                success=True,
                latency_sec=latency,
                status=status,
            )
        else:
            return TestResult(
                provider=provider,
                model=model,
                success=False,
                latency_sec=latency,
                status="failed",
                error=result.stderr[:200] if result.stderr else f"Exit code: {result.returncode}",
            )

    except subprocess.TimeoutExpired:
        return TestResult(
            provider=provider,
            model=model,
            success=False,
            latency_sec=timeout,
            status="timeout",
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        return TestResult(
            provider=provider,
            model=model,
            success=False,
            latency_sec=0,
            status="failed",
            error=str(e),
        )


def print_results(results: list[TestResult]) -> None:
    """결과 출력."""
    print("\nProvider    Model              Latency    Status")
    print("─" * 55)

    for r in results:
        latency_str = f"{r.latency_sec:.1f}초" if r.success else "─"
        status_icon = {
            "recommended": "✓ 권장",
            "usable": "✓ 사용 가능",
            "slow": "⚠ 느림",
            "timeout": "✗ 타임아웃",
            "failed": "✗ 실패",
        }.get(r.status, "?")

        print(f"{r.provider:<12}{r.model:<19}{latency_str:<11}{status_icon}")

        if r.error:
            print(f"            └─ {r.error[:50]}")


def generate_recommendations(results: list[TestResult]) -> dict:
    """권장 설정 생성."""
    recommendations = {}

    # Provider별 가장 좋은 모델 선택
    for provider in ["gemini", "openai"]:
        provider_results = [r for r in results if r.provider == provider and r.success]
        if provider_results:
            # recommended > usable > slow 순서로 정렬
            status_order = {"recommended": 0, "usable": 1, "slow": 2}
            best = min(provider_results, key=lambda r: (status_order.get(r.status, 99), r.latency_sec))
            recommendations[provider] = best.model

    return recommendations


def save_results(results: list[TestResult], recommendations: dict) -> None:
    """결과를 JSON 파일로 저장."""
    output_path = Path("~/.synod/setup-result.json").expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": [
            {
                "provider": r.provider,
                "model": r.model,
                "success": r.success,
                "latency_sec": r.latency_sec,
                "status": r.status,
                "error": r.error,
            }
            for r in results
        ],
        "recommendations": recommendations,
    }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n[저장됨] {output_path}")


def main():
    print("[Synod Setup] 초기 설정을 시작합니다...\n")

    # Step 1: CLI 도구 확인
    print("Step 1/3: CLI 도구 확인")
    available_providers = []
    for provider in MODELS_TO_TEST:
        exists, path = check_cli_exists(provider)
        icon = "✓" if exists else "✗"
        print(f"  {icon} {MODELS_TO_TEST[provider]['cli']}")
        if exists:
            available_providers.append(provider)

    # Step 2: API 키 확인
    print("\nStep 2/3: API 키 확인")
    providers_with_keys = []
    for provider in available_providers:
        has_key, env_key = check_api_key(provider)
        icon = "✓" if has_key else "✗"
        status = "설정됨" if has_key else "설정 안됨"
        print(f"  {icon} {env_key} ({status})")
        if has_key:
            providers_with_keys.append(provider)

    # Step 3: 모델 테스트
    print(f"\nStep 3/3: 모델 응답 시간 측정 (타임아웃: {TIMEOUT_THRESHOLD}초)")

    # 테스트 대상: Gemini + OpenAI 핵심 모델
    targets = [
        (provider, model)
        for provider, model in TEST_TARGETS
        if provider in providers_with_keys
    ]

    if not targets:
        print("\n[오류] 테스트 가능한 모델이 없습니다. API 키를 확인하세요.")
        sys.exit(1)

    results = []
    for provider, model in targets:
        print(f"  테스트 중: {provider}/{model}...", end="", flush=True)
        result = test_model(provider, model)
        results.append(result)
        icon = "✓" if result.success else "✗"
        print(f" {icon} ({result.latency_sec:.1f}s)")

    # 결과 출력
    print_results(results)

    # 권장 설정
    recommendations = generate_recommendations(results)
    if recommendations:
        print("\n[권장 환경변수 설정]")
        if "gemini" in recommendations:
            print(f"  export SYNOD_GEMINI_MODEL={recommendations['gemini']}")
        if "openai" in recommendations:
            print(f"  export SYNOD_OPENAI_MODEL={recommendations['openai']}")

    # 결과 저장
    save_results(results, recommendations)

    # 최종 상태
    success_count = sum(1 for r in results if r.success)
    total_count = len(results)
    print(f"\n[완료] {success_count}/{total_count} 모델 사용 가능")

    if success_count >= 2:
        print("Synod를 사용할 준비가 되었습니다!")
        sys.exit(0)
    else:
        print("최소 2개 모델이 필요합니다. 설정을 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
