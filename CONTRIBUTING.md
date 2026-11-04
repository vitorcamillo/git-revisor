# Como contribuir

Contribuições são bem-vindas. Para mudanças maiores, abra uma issue antes de começar para alinhar o comportamento esperado. Mantenha alterações focadas e inclua testes para corrigir bugs ou adicionar comportamento.

## Preparar o ambiente e executar os testes

Use Python 3.10 ou mais recente e instale as dependências de desenvolvimento:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Os testes usam repositórios Git temporários e não precisam instalar os hooks globais. Não execute `python3 git_revisor.py instalar` para rodar a suíte.

## Issues

Inclua:

- sistema operacional, versões de Python e Git e a forma como o git-revisor foi instalado;
- passos mínimos para reproduzir o problema e o resultado esperado;
- saída relevante de `git revisor status`, com caminhos, nomes e dados privados removidos;
- quais wrappers externos e checks estão configurados, sem expor credenciais.

Antes de publicar logs ou diffs, remova tokens, URLs privadas, nomes de clientes, dados pessoais e qualquer conteúdo que não possa ser compartilhado. Não anexe `.env`, arquivos de configuração de autenticação, dumps ou logs completos sem revisão.

## Pull requests

Descreva o problema, a mudança proposta e como ela foi verificada. Para bugs, inclua um caso que falhava antes e passe a funcionar. Para comportamento novo, cubra os caminhos de sucesso, falha e bloqueio relevantes.

Casos de teste úteis incluem:

- push aprovado e push bloqueado por revisão reprovada;
- check determinístico com sucesso ou falha, incluindo a execução isolada em worktree;
- correção proposta por worker passando novamente pelos checks e por revisão independente;
- instalação que preserva um `core.hooksPath` global existente;
- relatórios de status sem dados sensíveis.

Não inclua segredos, dados de clientes ou diffs privados na issue, no PR, nos testes ou em fixtures. Confira também que a descrição e os logs anexados não revelam informações enviadas a provedores externos.

