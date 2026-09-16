# main 브랜치 자동 배포

`.github/workflows/deploy.yml`은 `main` 푸시 및 `main` 대상 수동 실행 시 동작한다. PR이나 다른 브랜치에서는 배포하지 않는다. GitHub OIDC로 `usedcar-github-deploy` 역할을 받아 사용하며 AWS 액세스 키를 GitHub Secrets에 저장하지 않는다.

## 흐름

1. Git 체크아웃. 실습용 대용량 CSV의 Git LFS 다운로드는 생략한다.
2. 비공개 S3의 모델 패키지를 내려받아 커밋된 `model-manifest.json`의 SHA-256과 파일 목록을 검증한다. 매번 재학습하지 않는다.
3. 모델·API·CD 안전장치 테스트, 프론트엔드 타입 검사 및 정적 빌드를 실행한다.
4. GitHub 러너에서 API/Nginx 이미지를 빌드한다. 커밋 SHA·실행 ID·재시도 번호로 고유 태그를 부여한다.
5. 이미지와 Compose 설정을 S3 `releases/`에 업로드하고 해당 EC2에만 SSM Run Command를 보낸다.
6. EC2가 체크섬을 검증하고 이미지를 로드한다. 컨테이너 healthcheck, 실제 릴리스·모델 버전, 예측값을 확인한 후 `/opt/usedcar/current`를 갱신한다.
7. 새 서비스 시작 또는 추론 검증이 실패하면 기존 이미지로 복구하고 GitHub 실행은 실패로 유지한다. 복구도 실패하면 로그에 `ROLLBACK FAILED`를 남긴다.

GitHub 동시 실행 그룹과 서버 파일 잠금으로 중복 배포를 막는다. 진행 중 실행은 취소하지 않는다. GitHub concurrency 특성상 대기 중 연속 푸시는 최신 커밋으로 합쳐질 수 있다. 타임아웃 시 SSM 명령이 남아 있을 수 있으므로 로그의 command ID를 확인한 뒤 재실행한다. 단일 서버이므로 전환 중 짧은 중단이 발생할 수 있다.

## IAM 및 저장소

| 주체 | 권한 |
|---|---|
| `usedcar-github-deploy` | 정확한 저장소 ID/조직 ID와 `main` OIDC subject만 신뢰. 모델 읽기, 릴리스 쓰기, 대상 EC2의 SSM 명령 실행·결과 확인 |
| `usedcar-service-ec2` | 기존 SSM 운영 권한 + CD 버킷의 `releases/*` 읽기 |
| `usedcar-deploy-operator` | 해당 모델/릴리스 업로드·읽기, 대상 EC2의 SSM 배포·복구, 인스턴스 상태 조회. IAM 관리자 권한 없음 |

로컬 운영자 프로필은 **`usedcar-deploy`**이다. IAM 사용자에게는 콘솔 비밀번호를 생성하지 않았으며 CLI용 키만 로컬 `~/.aws/credentials`에 권한 0600으로 저장한다. 키 값은 저장소·문서·GitHub에 넣지 않는다.

버킷 `usedcar-cd-410618141864-ap-northeast-2`는 공개 차단·서버측 암호화·TLS 전용이다. 모델은 유지하고 전송용 릴리스는 7일 후 삭제한다. 서버에서는 로드가 끝난 이미지 전송 파일을 삭제하지만 Docker 이미지 자체는 롤백을 위해 유지한다. 장기 운영 시 디스크를 확인하고 현재·직전 버전에 쓰이지 않는 이전 이미지 태그를 선별 정리한다. `docker system prune`을 무조건 실행하지 않는다.

배포 대상·역할·정확한 OIDC subject는 `config.json`에 있다. 2026-07-15 이후 생성된 이 저장소는 조직/저장소 ID가 포함된 immutable subject를 사용한다. [GitHub OIDC 문서](https://docs.github.com/en/actions/reference/security/oidc)

## 최초 설정 또는 환경 재생성

권한 있는 AWS 운영자 세션에서 저장소 루트 기준:

```bash
python3 -m pip install -r deploy/cd/requirements.txt
python3 -m deploy.cd.model_artifact pack
python3 -m deploy.cd.bootstrap --profile YOUR_BOOTSTRAP_PROFILE
```

bootstrap은 IAM 사용자/역할, OIDC provider, 비공개 버킷과 모델 파일을 설정한다. 이때만 IAM/S3 관리 권한이 필요하며, 재실행 시 기존 키를 불필요하게 추가하지 않는다. 이미 프로필이 존재하면 대상 IAM 사용자와 일치하는지 검증한다. `config.json`의 계정이 다르면 중단한다.

## 모델 업데이트

검증된 모델 및 테스트용 이전 모델·예측 예제를 로컬 `artifacts/`에 준비한 다음:

```bash
python3 -m deploy.cd.model_artifact pack
```

생성된 `deploy/.local/model-bundle.tar.gz`를 manifest에 나온 S3 key로 업로드한다. 예시의 `<manifest-key>`는 `model-manifest.json`의 실제 `key`로 바꾼다.

```bash
aws s3 cp deploy/.local/model-bundle.tar.gz s3://usedcar-cd-410618141864-ap-northeast-2/<manifest-key> --profile usedcar-deploy
```

manifest 변경을 검토하고 커밋·푸시한다. 모델 파일 자체는 Git에 추가하지 않는다. main 푸시 시 해당 모델과 코드가 함께 테스트·배포된다. 코드만 바뀌면 기존 manifest의 모델을 그대로 사용한다. 파일이 없거나 체크섬이 다르면 배포 전에 실패한다.

## 재시도·복구

GitHub Actions의 `Deploy main to EC2`에서 실패 로그를 확인하고 Re-run failed jobs 또는 Run workflow를 사용한다. 재시도는 새 attempt 태그로 배포된다.

EC2에는 `/opt/usedcar/current`, `/opt/usedcar/previous-release`, `/opt/usedcar/releases/<release>/release.json`이 있다. 수동 복구는 이전 릴리스 디렉터리에서:

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --no-build --wait
```

준비 상태와 예측을 확인하고 current 링크를 이전 디렉터리로 연결한다. 명령은 IAM/SSO 운영자 세션의 SSM으로 실행한다. HTTP `/api/health/ready`는 현재 `release`와 `model_version`을 반환한다.

## 로컬 검증

```bash
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 python3 -m pytest -q test_price_model.py backend/tests/test_api.py deploy/cd/tests
bash -n deploy/cd/activate.sh
```

CD 테스트는 모델 체크섬/파일 목록/경로 탈출 거절, IAM 리소스 범위, 정상 전환 및 시작·추론 실패 복구를 확인한다. 셸 전환 검증은 격리된 명령 대역을 쓰며 실제 AWS 전체 장애를 발생시키는 시험은 아니다.
