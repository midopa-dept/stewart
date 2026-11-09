"""시험 패키지. 가짜 백엔드 주소로 /v1/models(서버 문맥 길이)를 묻지 않게 끈다(시험이 직접 켜서 본다)."""
import os

os.environ.setdefault("STEWART_SERVED_CONTEXT", "0")
# 시험은 저장소의 예시 백엔드를 쓴다(개발 체크아웃의 로컬 backends.toml·사용자 설정에 기대지 않게)
os.environ.setdefault("STEWART_BACKENDS", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                       "backends.example.toml"))
