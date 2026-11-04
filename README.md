# git-revisor

[Licença MIT](LICENSE) · Wrappers disponíveis em [worker-ask-router](https://github.com/vitorcamillo/worker-ask-router).

O `git-revisor` adiciona revisão automatizada de commits por IA aos hooks do Git. Ele ajuda a encontrar problemas antes de enviar mudanças, combina revisão de diff com checks determinísticos e pode bloquear um push reprovado. Se a revisão reprovar, pode pedir a um worker externo que proponha uma correção em um worktree e submeter a alteração novamente à revisão.

## Como funciona

- Os hooks de commit e push chamam o revisor. No push, os commits ainda não aprovados são revisados antes do envio; uma reprovação bloqueia o push.
- Checks determinísticos, como testes e lint, rodam em worktrees do commit. Por padrão, o projeto tenta detectar testes conhecidos; também é possível configurar comandos por repositório. Uma falha de check reprova a mudança.
- Mudanças classificadas como baixo risco podem passar sem revisão de IA, mas os checks configurados ou detectados continuam sendo considerados.
- Quando há reprovação, o worker pode preparar uma correção isolada em worktree. A correção também passa pelos checks e por uma revisão independente antes de ser aceita.
- Commits grandes demais para revisão automática podem exigir revisão manual. Consulte `git revisor status` para ver o estado e os relatórios disponíveis.

As decisões automáticas não substituem a revisão humana. Confira o diff, os resultados dos checks e qualquer correção proposta antes de considerar a mudança pronta.

## Requisitos

- Linux com Python 3.10 ou mais recente e Git.
- Bash, `readlink`, `flock` e o módulo `fcntl` do Python, usado para locks no Linux.
- Os wrappers externos de IA usados pela configuração: por padrão, `codex-ask` para revisar e `zcode-worker`, `muse-ask`, `codex-ask` ou `claude-ask` como workers em sequência de fallback.

Esses wrappers precisam estar instalados, configurados e acessíveis no `PATH` (ou apontados pelas variáveis `REVISOR_CMD_*`). O git-revisor depende deles para revisão e correção por IA: não fornece modelos locais gratuitos nem funciona sem um revisor externo disponível. O uso pode enviar diffs e contexto do repositório ao provedor configurado e pode gerar custos conforme a conta, o modelo e as políticas desse provedor. Verifique as condições de privacidade e cobrança antes de habilitar os hooks.

## Instalação

```bash
git clone https://github.com/vitorcamillo/git-revisor-public.git
cd git-revisor-public
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Para instalar os hooks e o comando `git revisor`:

```bash
python3 git_revisor.py instalar
```

**Atenção:** `instalar` configura `core.hooksPath` na configuração global do Git e cria links para os hooks e para `~/.local/bin/git-revisor`. Se `core.hooksPath` global já estiver configurado para outro caminho, a instalação para sem sobrescrevê-lo. A instalação não deve ser executada sem entender esse efeito global.

Confira a instalação e o estado no repositório:

```bash
export PATH="$HOME/.local/bin:$PATH"
git revisor status
```

Para desligar o revisor apenas no repositório atual:

```bash
git revisor desligar
```

Para remover a instalação global criada pelo comando `instalar`:

```bash
git revisor desinstalar
```

`desinstalar` remove `core.hooksPath` global quando ele aponta para a pasta gerenciada pelo git-revisor, além dos links que o instalador criou.

## Configuração de comandos externos

Os comandos padrão podem ser substituídos por variáveis de ambiente `REVISOR_CMD_*`. Por exemplo, `REVISOR_CMD_REVISAR` escolhe o revisor, `REVISOR_CMD_WORKER` escolhe o primeiro worker e há variáveis próprias para os workers de fallback e para o comando `gitleaks`. Configure os executáveis conforme a documentação dos wrappers instalados; não coloque tokens ou segredos em arquivos versionados.

Os checks determinísticos podem ser definidos na configuração local do Git usando `revisor.check`; sem configuração manual, a detecção automática procura projetos com pytest, Maven, npm, Go ou Cargo. A configuração é local ao repositório e pode executar comandos do próprio projeto nos worktrees. Revise esses comandos antes de habilitar checks em código que não conhece.

## Contribuir

Veja [CONTRIBUTING.md](CONTRIBUTING.md) para executar a suíte, relatar problemas e propor mudanças.

