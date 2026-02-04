# Synod Setup - 초기 설정 및 모델 가용성 테스트

## 설명

Synod를 처음 사용하기 전에 실행하는 초기 설정 도구입니다. 사용 가능한 AI 모델들의 상태를 테스트하여 어떤 모델을 사용할 수 있는지 확인합니다.

## 사용법

```bash
/synod-setup
```

옵션 없이 실행합니다.

## 동작 과정

### Step 1: CLI 도구 확인
- `gemini-3.py` 실행 가능 여부 확인
- `openai-cli.py` 실행 가능 여부 확인

### Step 2: API 키 확인
- `GOOGLE_API_KEY` 환경 변수 설정 여부 확인
- `OPENAI_API_KEY` 환경 변수 설정 여부 확인

### Step 3: 모델 응답 시간 테스트
간단한 테스트 프롬프트를 각 모델에 전송하여 응답 시간을 측정합니다.
- 타임아웃: 120초
- 테스트 프롬프트: "Hello"

## 출력 형식

테스트 결과는 테이블 형태로 표시되며, 각 모델의 상태를 다음과 같이 분류합니다:

| 상태 | 설명 | 응답 시간 |
|------|------|-----------|
| **recommended** | 권장 사용 | < 5초 |
| **usable** | 사용 가능 | 5초 ~ 30초 |
| **slow** | 느림 (비권장) | 30초 ~ 120초 |
| **timeout** | 타임아웃 | > 120초 |
| **failed** | 실패 (API 키/권한 오류) | - |

## 결과 파일

테스트 결과는 다음 위치에 JSON 형식으로 저장됩니다:

```
~/.synod/setup-result.json
```

이 파일은 Synod가 모델 선택 시 참조하여 사용 불가능한 모델을 자동으로 제외합니다.

## 권장 사항

- `/synod` 명령을 처음 사용하기 전에 반드시 실행하세요
- API 키를 새로 설정하거나 변경한 경우 다시 실행하세요
- 모델 응답이 비정상적으로 느려진 경우 재테스트하세요

## 예시

```bash
$ /synod-setup

🔍 Synod 설정 테스트를 시작합니다...

[1/3] CLI 도구 확인 중...
  ✓ gemini-3.py 발견
  ✓ openai-cli.py 발견

[2/3] API 키 확인 중...
  ✓ GOOGLE_API_KEY 설정됨
  ✓ OPENAI_API_KEY 설정됨

[3/3] 모델 응답 테스트 중...
  ✓ gemini-3-flash-preview: 2.3초 (recommended)
  ✓ gpt-4-turbo: 4.8초 (recommended)
  ⚠ claude-opus-4: 35초 (slow)
  ✗ gemini-1.5-pro: 타임아웃 (timeout)

결과가 ~/.synod/setup-result.json에 저장되었습니다.
```
