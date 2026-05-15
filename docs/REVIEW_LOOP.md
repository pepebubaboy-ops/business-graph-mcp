# Review loop after each Codex prompt

После каждого промпта Codex должен сделать небольшой PR/commit.
Нельзя просить Codex "сделай всё сразу".

## Что присылать на ревью

После каждого шага пришлите одно из:

1. ссылку на PR;
2. ссылку на commit;
3. diff;
4. вывод команд:

```bash
git status --short
git diff --stat HEAD~1..HEAD
git diff --name-only HEAD~1..HEAD
make test
```

## Что я буду проверять

- архитектура не превращается в монолит;
- MCP не содержит бизнес-логику, только адаптер;
- все confirmed relations имеют evidence;
- LLM-гипотезы остаются candidates;
- raw paths не используются в production API;
- тесты проходят;
- нет секретов в repo;
- нет arbitrary code execution;
- публичные endpoints защищены, кроме `/health` и `/openapi.json`.

## Шаблон сообщения мне после каждого шага

```text
Шаг: PR-00X
Ссылка/diff: ...
Что сделал Codex: ...
Вывод make test: ...
Что вызывает сомнение: ...
```

Я не могу мониторить репозиторий в фоне, поэтому ревью запускается
только когда вы присылаете ссылку, diff или конкретный вопрос.
