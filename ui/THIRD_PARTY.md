# ui/ 에 빌려 온 제3자 코드·꼴

stewart 웹 화면(ui/)은 아래 MIT 프로젝트에서 부품의 꼴이나 코드 조각을 빌려 왔다. 옮긴 파일 머리에 출처를 한 줄 적었다.

| 부품 | 우리 파일 | 원본 | 판 |
|---|---|---|---|
| diff 줄 번호 4칸 격자·묶음 나눔 | ui/src/diffrows.js · thread.jsx DiffCard · app.css `.dg*` | llama.cpp `tools/ui/src/lib/components/app/chat/ChatMessages/ChatMessage/ChatMessageToolCall/ChatMessageToolCallBlockEditFile.svelte` | 55 |
| 생각 블록: 받는 동안 따라가기(위로 올리면 멈춤) | ui/src/think.jsx | llama.cpp `tools/ui/src/lib/components/app/chat/ChatMessages/ChatMessageReasoningBlock.svelte` | 56 |
| 생각 블록: 받는 중 소제목(마지막 제목·굵은 줄) | ui/src/think.jsx reasoningHeading | opencode `packages/session-ui/src/timeline/projection.ts` reasoningHeading | 56 |
| 승인 단축키(⌘/Ctrl+Enter 허용·⇧⌘/Ctrl+Backspace 거부)·단추 힌트·두 번 보내기 막기 | ui/src/dock.jsx answer·AskCard · app.css .kbd | OpenHands `src/components/shared/buttons/conversation-confirmation-buttons.tsx` | 57 |
| 작업 묶음 제목 요약("명령 3개 실행 · 파일 1개 읽음")·0 아닌 exit 뱃지·결과 전문 복사 | ui/src/thread.jsx stepSummary·exitOf·Step | OpenHands `src/components/conversation-events/chat/event-message-components/event-group.tsx`, `src/components/features/chat/tool-visualizers/primitives/output-pane.tsx` | 58 |

## llama.cpp — https://github.com/ggml-org/llama.cpp

```
MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## opencode — https://github.com/anomalyco/opencode

```
MIT License

Copyright (c) 2025 opencode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## OpenHands — https://github.com/OpenHands/OpenHands

```
The MIT License (MIT)

Copyright © 2025 OpenHands contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
