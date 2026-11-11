---
name: documents
description: PDF·Word(.docx)·Excel(.xlsx)·PowerPoint(.pptx) 파일을 읽거나 만들거나 고쳐 달라고 할 때 쓴다. 쓸 수 있는 도구를 먼저 확인하고, 없으면 꾸러미 없이 되는 길과 필요한 것을 알린다.
---

# 문서 파일 다루기(pdf·docx·xlsx·pptx)

## 먼저 무엇이 있는지 본다
```bash
python3 -c "import importlib.util as u; print({m: bool(u.find_spec(m)) for m in ['pypdf','pdfplumber','docx','openpyxl','pptx','reportlab']})"
```
- 명령 도구: `pdftotext`(poppler), `pandoc`, `libreoffice --headless`, macOS 의 `textutil`(docx·rtf·html 바꾸기).
- 없으면 설치하기 전에 사람에게 묻는다(새 꾸러미). uv 프로젝트면 `uv run --with <꾸러미> python …` 로 한 번만 쓸 수도 있다(망 필요).

## 꾸러미 없이 읽기(파이썬 표준 라이브러리 — docx·xlsx·pptx 는 zip 안의 XML)
- docx: `word/document.xml` 의 `<w:t>` 글을 문단(`<w:p>`)마다 모은다.
- xlsx: `xl/sharedStrings.xml`(문자열 표) + `xl/worksheets/sheetN.xml` 의 `<c r="A1" t="s"><v>번호</v>` — t="s" 면 문자열 표 번호.
- pptx: `ppt/slides/slideN.xml` 의 `<a:t>` 글을 슬라이드마다.
- PDF 는 표준 라이브러리로 믿을 만하게 못 읽는다 — pypdf·pdfplumber·pdftotext 중 하나가 필요하다고 알린다. 스캔본(그림)이면 글자 인식(OCR)이 따로 필요하다.

## 만들기·고치기
- docx: python-docx(제목·문단·표). xlsx: openpyxl(셀·수식·서식 — 수식은 값이 아니라 식으로 들어간다). pptx: python-pptx(슬라이드·글상자). PDF 만들기: reportlab 또는 docx/html 을 만든 뒤 libreoffice·pandoc 으로 바꾸기.
- 원본을 고칠 때는 사본에 쓰고, 바뀐 것을 다시 읽어 확인한 뒤 알린다.

## 보고 꼴
- 쓴 도구, 읽은 범위(쪽·시트·슬라이드 수), 못 읽은 부분(그림·스캔·암호), 만든 파일 경로.
