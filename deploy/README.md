# 웹 서비스 운영

현재 권장 배포 경로는 [main 브랜치 GitHub Actions CD](cd/README.md)다. 아래 `provision.py`/`release.py` 절차는 최초 수동 배포 기록과 복구 참고용이다. CD용 S3 버킷과 읽기 권한은 지속적으로 유지하며, 최초 배포의 임시 버킷 정리와 구분한다.

Next.js 정적 화면 + Nginx + FastAPI, EC2 한 대 구성이다. 현재 모델은 사고 이력을 포함한다. 서버는 모델에 포함된 지원 차종·입력 범위·학습 기간을 읽으며, 원본 CSV나 학습 데이터는 배포하지 않는다.

## 로컬 실행

프로젝트 루트에서 Python 3.11 가상환경을 만들고 설치한다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

다른 터미널의 `frontend/`에서:

```bash
npm ci
npm run dev
```

`http://localhost:3000`에서 사용한다. 개발 서버가 `/api/`를 로컬 FastAPI로 전달한다. 운영 정적 빌드는 `npm run build`로 생성하며 빌드 시 외부 폰트를 다운로드하지 않는다.

## 컨테이너 실행

먼저 `frontend/`에서 `npm ci && npm run build`를 실행한다. 프로젝트 루트에서:

```bash
docker compose -f deploy/compose.yaml up -d --build --wait
```

`http://localhost/`로 접속한다. API 포트는 호스트에 공개하지 않는다. 학습 라이브러리 버전은 루트 requirements.txt를 따르며, 프론트엔드는 package-lock.json으로 고정한다. 기존 모델 및 price_model.py가 같은 릴리스의 파일인지 확인한다.

## AWS 구조

- 서울 리전, t3.small 1대, gp3 20GB, Elastic IP 1개.
- HTTP 80번만 공개하며 SSH는 열지 않는다. HTTP는 암호화되지 않으므로 개인정보를 입력받지 않는다.
- `usedcar-service-ec2` 역할: 신뢰 주체는 `ec2.amazonaws.com`, 기본 권한은 `AmazonSSMManagedInstanceCore`뿐이다.
- IMDSv2 필수, hop limit 1. 앱 컨테이너에 AWS 키를 전달하지 않는다.
- 배포 시에만 비공개 S3 버킷의 릴리스 객체 읽기 권한을 임시 추가하고 완료 후 권한과 버킷을 삭제한다.
- 루트 키는 초기 구축 후 삭제한다. 이후 운영은 AWS 콘솔의 IAM/SSO 운영자 세션에서 SSM Run Command/Session Manager로 수행한다. EC2 서비스 역할은 사람의 로그인 역할이 아니다.

`provision.py`는 최초 생성 도구다. `deploy/.local/aws-state.json`에 생성한 리소스 식별자를 저장한다. 이 상태 파일은 비밀 키를 포함하지 않지만 실수로 다른 인프라를 변경하지 않도록 계정·리전 검사를 수행한다. 상태 파일을 잃어버린 채 최초 생성 스크립트를 다시 실행하지 않는다.

```bash
python3 deploy/provision.py --profile YOUR_ADMIN_PROFILE
python3 deploy/release.py associate --profile YOUR_ADMIN_PROFILE
python3 deploy/release.py status --profile YOUR_ADMIN_PROFILE
python3 deploy/release.py deploy --profile YOUR_ADMIN_PROFILE
python3 deploy/release.py cleanup-staging --profile YOUR_ADMIN_PROFILE
```

IAM 역할을 처음 만들면 EC2에 전파되기까지 수십 초가 걸릴 수 있다. 프로필 이름 오류가 발생하면 저장된 상태를 유지한 채 다시 실행한다. `deploy`는 SSM이 Online인 상태에서 실행한다. 명령 성공과 실제 HTTP 동작을 확인한 후에만 `cleanup-staging`을 실행한다. 같은 계정에 대한 권한 있는 운영자 프로필을 사용하며 예시의 프로필 문자열을 그대로 실행하지 않는다.

## 상태 확인 및 복구

SSM에서 다음 명령으로 상태와 로그를 본다.

```bash
cd /opt/usedcar/current
docker compose -f deploy/compose.yaml --env-file deploy/.env ps
docker compose -f deploy/compose.yaml --env-file deploy/.env logs --tail=100 api web
curl --fail http://127.0.0.1/api/health/ready
```

컨테이너 프로세스 종료 및 서버 재부팅 후에는 Docker가 자동 재시작한다. healthcheck에서 unhealthy로만 바뀌고 프로세스가 종료되지 않은 경우에는 자동 재시작되지 않으므로 SSM에서 `docker compose ... restart api web`으로 복구한다. 메모리가 부족하면 실제 사용량을 확인한 후 t3.medium으로 변경한다. Standard CPU credit 모드이므로 장시간 높은 부하에서는 성능이 제한될 수 있다.

배포는 짧은 중단을 허용한다. `/opt/usedcar/previous-release`에 이전 릴리스 경로가 있으면 해당 경로에서 다음 명령으로 되돌린다.

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --no-build --wait
```

실행 전 현재 디렉터리가 이전 릴리스인지 확인하고, 성공 시 `/opt/usedcar/current`를 그 디렉터리로 다시 연결한다. 모델·앱·화면 이미지가 모두 함께 되돌아간다. 이전 이미지는 복구 검증 전 삭제하지 않는다. 릴리스 원본은 로컬 `deploy/.local/`에도 보관한다.

## 검증

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 -m pytest backend/tests -q
```

API 테스트는 저장 모델과의 수치·경고 일치, 입력 거절, 미준비 상태, 모델 파일 누락, 동시 요청 제한 및 취소된 요청의 슬롯 보존을 확인한다. 프론트엔드 타입 검사는 빌드에 포함된다. 운영 검증은 화면/정적 리소스의 응답, 예측값 일치, 422/429, 공개 포트 제한 및 재부팅 복구를 포함한다. 실제 측정 결과와 AWS 리소스 식별자는 `DEPLOYMENT.md`에 기록한다.

현재 단위는 미확정이다. 원화/km로 표시하지 않으며, 현재 시세나 신뢰구간을 제공하지 않는다. 단위 검증 또는 모델 교체 시 metadata와 화면 문구를 함께 업데이트한다.
