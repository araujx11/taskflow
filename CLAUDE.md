# TaskFlow

Gerenciador de tarefas de linha de comando em Python. É um projeto de aprendizado (Git, GitHub, Claude Code).

## Como rodar o CLI
Na raiz do projeto:

- `python src/main.py add "Título da tarefa"` — adiciona uma tarefa
- `python src/main.py list` — lista (`-s pending|done|all`, `--json`; alias `ls`)
- `python src/main.py done 1 3` — marca uma ou mais tarefas como concluídas
- `python src/main.py delete 2` — apaga tarefas (alias `rm`)

As tarefas ficam em `~/.taskflow/tasks.json`. O caminho pode ser trocado com `--file` ou com a variável `TASKFLOW_FILE`.
Formato: `{"version": 1, "next_id": N, "tasks": [...]}`; cada tarefa tem `id`, `title`, `done`, `created_at`, `completed_at`.

## Estrutura de pastas
- `src/main.py` — todo o CLI (armazenamento, comandos, argparse)
- `.claude/agents/task-reviewer.md` — definição do subagente
- `docs/` e `tests/` — existem, mas estão vazias por enquanto
- `README.md` — ainda só tem o título

## Agente: task-reviewer
Subagente somente leitura (ferramentas: Read, Glob, Grep), responde em português. Lê o `tasks.json` e aponta tarefas duplicadas ou parecidas, sugere títulos mais claros para tarefas vagas e propõe uma ordem de prioridade. Não altera tarefas: mudanças são feitas pelo CLI.

## Convenções
- Nunca trabalhar direto na `main`: criar uma branch e abrir um Pull Request.
- Commits em português, com prefixo `feat:`, `fix:` ou `docs:`.
- Responder sempre em português.

## Ambiente
- Windows 11 (PowerShell; o Bash do Git também está disponível)
- Python 3 (usa só a biblioteca padrão)
