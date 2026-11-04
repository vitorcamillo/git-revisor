"""Fixtures compartilhadas dos testes do git-revisor."""

import pytest


@pytest.fixture(autouse=True)
def cache_isolado(tmp_path, monkeypatch, request):
    """Isola o cache de repositório em cada teste."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # lock de checks real fica fora
    monkeypatch.chdir(tmp_path)
    nome_modulo = request.module.__name__
    if (nome_modulo.endswith(("test_git_revisor",
                              "test_git_revisor_adaptativo"))
            and (alvo := getattr(request.module, "mod", None) or getattr(request.module, "gr", None))):
        # As suítes antigas cobrem o worktree temporário e exigem `git worktree list`
        # com 1 entrada; o persistente é coberto em `test_git_revisor_r2.py`.
        monkeypatch.setattr(alvo, "worktree_persistente_ativo",
                            lambda topo=None: False)
        for var, valor in (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "revisor.worktreePersistente"),
                           ("GIT_CONFIG_VALUE_0", "false")):  # vale nos subprocessos (hooks)
            monkeypatch.setenv(var, valor)
