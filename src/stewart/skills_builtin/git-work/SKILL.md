---
name: git-work
description: 커밋·push·가지·되돌리기 같은 git 일을 하거나, git 인증·키체인에서 막혔을 때 쓴다. 안전하게 하는 차례와 사람 손 카드로 넘기는 법.
surfaces: [code]
---

# git 작업

## 먼저 보기
- `git status`, `git log --oneline -5`, `git branch --show-current`, 원격은 `git remote -v`.
- 남이 만든 미커밋·stash·다른 가지는 건드리지 않는다.

## 커밋
1. `git diff` 로 바뀐 것을 보고, 비밀(키·토큰·.env)이 섞이지 않았는지 확인한다.
2. 관련 파일만 `git add <파일…>`(통째 `add -A` 는 무엇이 들어가는지 본 뒤에만).
3. 메시지는 무엇을·왜를 한 줄로(필요하면 빈 줄 뒤 자세히).

## push
- `git push` 가 거부되면 원인을 먼저 본다: 원격이 앞섰으면 `git fetch` 후 `git log HEAD..@{u}` 로 보고 합칠지 사람에게 묻는다.
- 하지 않는다: force push, 원격 가지·태그 지우기, `reset --hard`, `clean -fd`, 남의 가지 지우기 — 사람이 시키지 않으면.

## 인증에서 막히면
- `could not read Username`, `Authentication failed`, 키체인 허용 창(-25308 등), SSH `Permission denied (publickey)` 는 사람만 풀 수 있다.
- 같은 계열 명령을 되풀이하지 말고 사람 손 카드로 넘긴다: 어디서(어느 기기·어느 셸), 붙여 넣을 명령(비밀은 넣지 않고 묻게), 하는 일, 끝나면 풀리는 것.
- 키체인은 로그인 세션에 묶인다: 에이전트를 띄운 셸이 원격(SSH·tmux)이면 그 셸과 같은 쪽에서 풀어야 할 수 있다.
