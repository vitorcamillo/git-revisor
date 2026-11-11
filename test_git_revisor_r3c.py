"""Testes R3c: agrupamento e revisão de pushes grandes."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Roda git no repositório temporário."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Prepara repositório isolado para os cenários de pre-push."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _commit(repo: Path, nome: str, conteudo: str) -> str:
    """Cria commit pequeno e devolve seu sha completo."""
    (repo / nome).write_text(conteudo, encoding="utf-8")
    _git(repo, "add", nome)
    _git(repo, "commit", "-q", "-m", f"feat: {nome}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _push_de_tres(repo: Path) -> list[str]:
    """Cria três commits consecutivos de código."""
    return [_commit(repo, f"src{i}.py", f"valor = {i}\n")
            for i in range(1, 4)]


def test_grupos_do_push_guloso_e_limite_de_grupos(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Agrupa a maior sequência válida e limita a soma dos grupos."""
    shas = _push_de_tres(repo)
    ordem = {sha: i for i, sha in enumerate(shas)}
    monkeypatch.setattr(mod, "MAX_PARTES", 2)
    monkeypatch.setattr(mod, "pai1_ou_vazio", lambda sha: f"base:{sha}")
    monkeypatch.setattr(
        mod, "diff_entre",
        lambda base, ponta: f"partes:{ordem[ponta] - ordem[base[5:]] + 1}")
    monkeypatch.setattr(
        mod, "dividir_em_partes",
        lambda diff: [""] * int(diff.split(":")[1]))
    monkeypatch.setattr(mod, "cfg", lambda chave, padrao: padrao if chave == "maxPartes" else "30")

    assert mod.grupos_do_push(shas) == [shas[:2], shas[2:]]

    monkeypatch.setattr(mod, "cfg", lambda chave, padrao: padrao if chave == "maxPartes" else "2")
    assert mod.grupos_do_push(shas) is None


def test_grupos_do_push_um_grupo_e_commit_isolado_grande(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commits pequenos cabem juntos e um commit grande invalida o push."""
    shas = _push_de_tres(repo)
    ordem = {sha: i for i, sha in enumerate(shas)}
    monkeypatch.setattr(mod, "MAX_PARTES", 3)
    monkeypatch.setattr(mod, "pai1_ou_vazio", lambda sha: f"base:{sha}")
    monkeypatch.setattr(
        mod, "diff_entre",
        lambda base, ponta: f"partes:{ordem[ponta] - ordem[base[5:]] + 1}")
    monkeypatch.setattr(
        mod, "dividir_em_partes",
        lambda diff: [""] * int(diff.split(":")[1]))
    monkeypatch.setattr(mod, "cfg", lambda chave, padrao: padrao if chave == "maxPartes" else "30")

    assert mod.grupos_do_push(shas) == [shas]

    monkeypatch.setattr(
        mod, "diff_entre",
        lambda _base, ponta: "partes:4" if ponta == shas[0] else "partes:1")
    assert mod.grupos_do_push(shas) is None


def _preparar_intervalo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desliga checks externos e gitleaks para testar apenas os grupos."""
    monkeypatch.setattr(mod, "gitleaks_intervalo", lambda *_args: 0)
    monkeypatch.setattr(
        mod, "rodar_checks", lambda *_args, **_kwargs: (True, ""))


def test_revisar_intervalo_aprova_e_marca_todos_os_grupos(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Push grande aprova grupo a grupo e persiste cada aprovação."""
    shas = _push_de_tres(repo)
    _preparar_intervalo(monkeypatch)
    monkeypatch.setattr(
        mod, "grupos_do_push", lambda _novos: [shas[:2], shas[2:]])
    chamadas = iter(["GRANDE_DEMAIS", "APROVADO", "APROVADO"])
    monkeypatch.setattr(
        mod, "revisar_diff",
        lambda *_args, **_kwargs: (
            next(chamadas),
            "VEREDITO: APROVADO\n--- codex-ask: modo=review ---",
            {"codex"}))

    assert mod.revisar_intervalo(shas, shas[-1], str(repo)) == 0
    assert all(mod.esta_aprovado(sha) for sha in shas)


def test_revisar_intervalo_reprovacao_intermediaria_preserva_progresso(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """Reprovação intermediária bloqueia sem apagar aprovações anteriores."""
    shas = _push_de_tres(repo)
    _preparar_intervalo(monkeypatch)
    monkeypatch.setattr(
        mod, "grupos_do_push", lambda _novos: [[s] for s in shas])
    chamadas = iter(["GRANDE_DEMAIS", "APROVADO", "REPROVADO"])
    monkeypatch.setattr(
        mod, "revisar_diff",
        lambda *_args, **_kwargs: (
            next(chamadas), "problema encontrado\nVEREDITO: REPROVADO",
            {"codex"}))

    assert mod.revisar_intervalo(shas, shas[-1], str(repo)) == 1
    assert mod.esta_aprovado(shas[0])
    assert not mod.esta_aprovado(shas[1])
    assert not mod.esta_aprovado(shas[2])
    saida = capsys.readouterr().out
    assert "grupo 1/3 aprovado" in saida
    assert f"git revisor corrigir {shas[1][:7]}" in saida
