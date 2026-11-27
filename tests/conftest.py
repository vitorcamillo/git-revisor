"""Fixtures compartilhadas dos testes do git-revisor."""

import pytest


@pytest.fixture(autouse=True)
def cache_isolado(tmp_path, monkeypatch, request):
    """Isola o cache de repositório em cada teste."""
    # QA cliente é ligado por padrão: fora da suíte de QA, nenhum teste
    # pode disparar QA real (os testes de QA removem esta variável).
    monkeypatch.setenv("REVISOR_QA", "0")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # lock de checks real fica fora
    # ordem.json real da máquina (painel Harness) mudaria a cadeia de correção
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    # vagas extras de checks dependem da carga da máquina e do git config
    # global do usuário: os testes fixam uma vaga só (o lock único)
    extras = [("revisor.vagasChecks", "1")]
    nome_modulo = request.module.__name__
    if (nome_modulo.endswith(("test_git_revisor",
                              "test_git_revisor_adaptativo"))
            and (alvo := getattr(request.module, "mod", None) or getattr(request.module, "gr", None))):
        # As suítes antigas cobrem o worktree temporário e exigem `git worktree list`
        # com 1 entrada; o persistente é coberto em `test_git_revisor_r2.py`.
        monkeypatch.setattr(alvo, "worktree_persistente_ativo",
                            lambda topo=None: False)
        extras.append(("revisor.worktreePersistente", "false"))  # vale nos subprocessos (hooks)
    for i, (chave, valor) in enumerate(extras):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", chave)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", valor)
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(extras)))
