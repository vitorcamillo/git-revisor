"""Testes da revisão em segundo plano e do contexto do repositório."""
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
    """Roda git no repositório de teste."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


def _commit(repo: Path, arquivos: dict[str, str], mensagem: str) -> str:
    """Grava e commita arquivos, devolvendo o SHA criado."""
    for relativo, conteudo in arquivos.items():
        caminho = repo / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", mensagem)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cria um repositório git isolado para cada teste."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    monkeypatch.chdir(caminho)
    return caminho


def test_ler_contexto_repo_usa_conteudo_commitado_e_aplica_teto(
        repo: Path) -> None:
    """Lê contexto na ponta, ignora working tree e limita bytes UTF-8."""
    sem_contexto = _commit(repo, {"app.py": "base\n"}, "base")
    assert mod.ler_contexto_repo(sem_contexto) is None

    texto = "regra commitada\n"
    com_contexto = _commit(
        repo, {".revisor/contexto.md": texto}, "adiciona contexto")
    assert mod.ler_contexto_repo(com_contexto) == texto

    grande = "x" * 5_000
    sha_grande = _commit(
        repo, {".revisor/contexto.md": grande}, "contexto grande")
    assert len(mod.ler_contexto_repo(sha_grande).encode("utf-8")) <= (
        mod.MAX_CONTEXTO_REPO)

    (repo / ".revisor/contexto.md").write_text("working tree", encoding="utf-8")
    assert mod.ler_contexto_repo(com_contexto) == texto


def test_montar_prompt_contexto_repo_opcional() -> None:
    """Inclui o contexto local somente quando fornecido."""
    com_contexto = mod.montar_prompt(
        "diff", "repo", "abc", contexto_repo="regra X")
    sem_contexto = mod.montar_prompt("diff", "repo", "abc")
    assert "Contexto do repositório" in com_contexto
    assert "regra X" in com_contexto
    assert "Contexto do repositório" not in sem_contexto


def test_todos_os_tetos_do_prompt_cabem() -> None:
    """Diff e metadados no limite permanecem abaixo do teto total."""
    prompt = mod.montar_prompt(
        "x" * mod.MAX_DIFF_BYTES, "repo", "abc1234",
        relatorio_anterior="r" * mod.MAX_RELATORIO,
        intencao="i" * mod.MAX_INTENCAO,
        evidencia="e" * mod.MAX_EVIDENCIA,
        contexto_repo="c" * mod.MAX_CONTEXTO_REPO)
    assert len(prompt.encode("utf-8")) <= mod.MAX_PROMPT_BYTES


def test_revisar_diff_repassa_contexto_repo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O prompt da revisão contém contexto commitado na ponta."""
    base = _commit(repo, {"app.py": "base\n"}, "base")
    ponta = _commit(repo, {
        "app.py": "alterado\n",
        ".revisor/contexto.md": "toda rota passa por gateway",
    }, "altera app")
    _git(repo, "config", "revisor.cacheVeredito", "false")
    prompts: list[str] = []

    def revisar(prompt: str, _repo: str) -> tuple[str, str]:
        """Captura o prompt sem chamar uma IA."""
        prompts.append(prompt)
        return "APROVADO", "VEREDITO: APROVADO"

    monkeypatch.setattr(mod, "revisar_com_ia", revisar)
    mod.revisar_diff(base, ponta, str(repo), "abc1234")
    assert "toda rota passa por gateway" in prompts[0]


def test_cmd_revisar_adiado_revisa_sha_atual_e_ignora_ancestral(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisa o SHA atual, mas descarta SHA coberto por commit posterior."""
    sha = _commit(repo, {"app.py": "um\n"}, "um")
    _git(repo, "config", "revisor.debounce", "0")
    monkeypatch.setattr(mod.time, "sleep", lambda _n: None)
    chamadas: list[str] = []
    logs: list[str] = []
    monkeypatch.setattr(mod, "cmd_revisar", lambda valor: chamadas.append(valor) or 7)
    monkeypatch.setattr(mod, "log", logs.append)

    assert mod.cmd_revisar_adiado(sha) == 7
    assert chamadas == [sha]
    assert mod.despachar("revisar-adiado", [sha]) == 7
    assert chamadas == [sha, sha]

    novo = _commit(repo, {"app.py": "dois\n"}, "dois")
    assert mod.cmd_revisar_adiado(sha) == 0
    assert chamadas == [sha, sha]
    assert any("debounce:" in mensagem for mensagem in logs)
    assert novo != sha


def test_hook_post_commit_escolhe_subcomando_por_debounce(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """Usa revisar-adiado por padrão e revisar quando debounce é zero."""
    sha = _commit(repo, {"app.py": "um\n"}, "um")
    _git(repo, "config", "revisor.enabled", "true")
    _git(repo, "config", "revisor.segundoPlano", "true")
    monkeypatch.delenv("REVISOR_INTERNO", raising=False)
    monkeypatch.setattr(mod, "operacao_em_andamento", lambda: False)
    chamadas: list[tuple[tuple, dict]] = []
    real = mod.subprocess.Popen

    def popen(*args: object, **kwargs: object) -> None:
        """Registra a chamada ao subprocesso sem iniciá-lo."""
        if "git_revisor" not in str(args[0]):
            return real(*args, **kwargs)
        chamadas.append((args, kwargs))

    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    assert mod.habilitado()
    assert not mod.interno()
    assert mod.hook_post_commit() == 0
    assert chamadas[-1][0][0][2] == "revisar-adiado"
    assert chamadas[-1][0][0][3] == sha
    assert capsys.readouterr().out == (
        f"revisor: revisando {sha[:7]} em segundo plano em 120s "
        "(git revisor status)\n")

    _git(repo, "config", "revisor.debounce", "0")
    assert mod.hook_post_commit() == 0
    assert chamadas[-1][0][0][2] == "revisar"
    assert chamadas[-1][0][0][3] == sha
