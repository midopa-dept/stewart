---
name: stewart-config
description: 사람이 stewart 자체 설정을 바꾸고 싶어 할 때 — 모델(백엔드)·프로필·규칙(늘 허용·금지)·허브·웹 검색·기억·스킬 설정을 어디서 어떻게 바꾸는지 물을 때 쓴다.
---

# stewart 설정 바꾸기 안내

## 어느 파일인가(사용자 설정 폴더 = $XDG_CONFIG_HOME/stewart, 보통 ~/.config/stewart)
| 바꿀 것 | 파일 |
|---|---|
| 모델 서버·모델 이름·문맥 길이·이미지 받기(vision) | `backends.toml`(또는 STEWART_BACKENDS) — `[backends.<이름>]` 절, 맨 위 `default` |
| 늘 허용·묻기·금지 명령 꼴 | `rules.toml`(사용자), 저장소 `.stewart/rules.toml`(묻기·금지만 먹힘) |
| 모든 프로젝트에 지킬 지시 | `STEWART.md`(사용자 전역), 저장소 `AGENTS.md`·`STEWART.md` |
| 프로필(도구 목록·지시문·단계 상한) | `profiles/<이름>.toml`(사용자 폴더), 내장 것을 extends 로 이어 쓰기 |
| 허브(포트·알림·화면·계정·웹 검색·기억 뿌리) | `hub.toml` — `[hub]`·`[web]`·`[memory]`·`[forge]` 절 |
| 셸 | `config.toml` 의 `[shell] path`·`args` |
| 스킬 | `skills/<이름>/SKILL.md`(사용자), 저장소 `.stewart/skills/` |

## 차례
1. 무엇을 바꾸려는지 한 줄로 확인한다.
2. 그 파일이 있는지 보고(없으면 만든다), 지금 값을 읽어 보여 준다. 비밀(키·토큰)은 파일에 쓰지 않는다 — 환경 변수 이름(api_key_env)만.
3. 바꿀 줄을 보여 주고, 사람의 설정 폴더는 사람이 허락한 뒤에만 고친다.
4. 대부분은 다음 세션(허브는 다시 켤 때)부터 먹힌다고 알린다.

## 자주 묻는 것
- 다른 모델로: `stewart chat -b <백엔드 이름>` 또는 backends.toml 의 default.
- 늘 묻는 명령을 그만 묻게: 승인 칸의 [늘 허용] 또는 rules.toml 의 allow 꼴.
- 웹 검색 켜기: hub.toml `[web] url = "<검색 서버 주소>"` 또는 환경 변수 STEWART_SEARXNG_URL.
