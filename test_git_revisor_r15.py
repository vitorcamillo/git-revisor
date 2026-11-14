"""Testes R15: aviso global de espera pelo lock de checks.

A suíte completa em fundo (`suite-completa`) cede o lock GLOBAL de checks
também a push de OUTRO repositório: quem espera o lock deixa um aviso na
pasta global `checks-esperando` (`marcar_check_esperando`) e o `abortar`
da suíte passa a ser `alguem_esperando` (push local OU aviso global).
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Roda git no repositório de teste e devolve o processo."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


def _commit(repo: Path, arquivos: dict[str, str], mensagem: str) -> str:
    """Grava arquivos, commita com a mensagem dada e devolve o sha."""
    for relativo, conteudo in arquivos.items():
        caminho = repo / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", mensagem)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repositório git temporário com o revisor desligado, como cwd."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _condicoes_livres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Máquina liberada para suite-completa/retentar-push (sem espera)."""
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 99_999)
    monkeypatch.setattr(mod, "lock_checks_ocupado", lambda: False)
    monkeypatch.setattr(mod, "push_esperando", lambda: False)


# ------------------------------------------------ marcar/alguem esperando

def test_marcar_check_esperando_alimenta_alguem_esperando(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aviso global de pid vivo faz `alguem_esperando()` True mesmo sem
    push local; apagado o aviso, volta a False."""
    monkeypatch.setattr(mod, "push_esperando", lambda: False)
    caminho = mod.marcar_check_esperando()
    assert caminho is not None
    assert caminho.exists()
    assert mod.alguem_esperando() is True
    mod._apagar_aviso(caminho)
    assert mod.alguem_esperando() is False


def test_aviso_global_com_pid_morto_e_lixo_apagado(repo: Path) -> None:
    """Aviso global de pid morto não segura ninguém: `alguem_esperando()`
    False e o arquivo lixo é apagado na leitura."""
    pid = 2**22 + 12345
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    pasta = mod.pasta_checks_esperando()
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / str(pid)
    arq.write_text("", encoding="utf-8")
    assert mod.alguem_esperando() is False
    assert not arq.exists()


# ---------------------------------------------------- trava_checks (avisos)

def test_trava_checks_avisa_durante_espera_e_limpa_dentro(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cede=False: o aviso global está de pé ANTES de obter o lock e já
    foi apagado dentro do `with`; `livre` é o do trava falso."""
    gravados: list[bool] = []

    @contextlib.contextmanager
    def fake_trava(espera, arquivo=None):
        gravados.append(mod._aviso_vivo(mod.pasta_checks_esperando()))
        yield True

    monkeypatch.setattr(mod, "trava", fake_trava)
    dentro: list[tuple[bool, bool]] = []
    with mod.trava_checks() as livre:
        dentro.append((livre,
                       mod._aviso_vivo(mod.pasta_checks_esperando())))
    assert gravados == [True]
    assert dentro == [(True, False)]


def test_trava_checks_cede_nao_avisa(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cede=True (a própria suíte em fundo): nada de aviso global."""
    gravados: list[bool] = []

    @contextlib.contextmanager
    def fake_trava(espera, arquivo=None):
        gravados.append(mod._aviso_vivo(mod.pasta_checks_esperando()))
        yield True

    monkeypatch.setattr(mod, "trava", fake_trava)
    with mod.trava_checks(cede=True) as livre:
        assert livre is True
    assert gravados == [False]


def test_trava_checks_sem_lock_nao_deixa_aviso(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock não obtido (yield False): nenhum aviso sobra na pasta global."""
    @contextlib.contextmanager
    def fake_trava(espera, arquivo=None):
        yield False

    monkeypatch.setattr(mod, "trava", fake_trava)
    with mod.trava_checks() as livre:
        assert livre is False
    pasta = mod.pasta_checks_esperando()
    assert not pasta.exists() or not any(pasta.iterdir())


# --------------------------------------- cmd_suite_completa com novo abortar

def test_suite_completa_passa_alguem_esperando_como_abortar(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Suíte verde em fundo repassa `abortar=alguem_esperando` ao
    `rodar_checks`: cede a push de qualquer repositório."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _commit(repo, {"NOTES.md": "mudou\n"}, "docs: notas")
    capturados: list[object] = []

    def _fake_rodar_checks(*a: object, **k: object) -> tuple[bool, str]:
        capturados.append(k["abortar"])
        return True, ""

    monkeypatch.setattr(mod, "rodar_checks", _fake_rodar_checks)
    _condicoes_livres(monkeypatch)
    assert mod.cmd_suite_completa(sha) == 0
    assert capturados == [mod.alguem_esperando]
