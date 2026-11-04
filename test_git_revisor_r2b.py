"""Testes do cache de resultado de check por árvore (revisor.cacheChecks)."""
from __future__ import annotations

import importlib.util
import shlex
import subprocess
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor_r2b", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Roda git no repositório de teste e devolve o processo."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


def _commit(repo: Path, arquivos: dict[str, str | None],
            mensagem: str) -> str:
    """Grava, remove e commita arquivos; devolve o SHA criado."""
    for relativo, conteudo in arquivos.items():
        caminho = repo / relativo
        if conteudo is None:
            caminho.unlink(missing_ok=True)
            continue
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", mensagem)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _emendar(repo: Path, mensagem: str) -> str:
    """`commit --amend -m` sem mudar conteúdo; devolve o novo SHA."""
    _git(repo, "commit", "-q", "--amend", "-m", mensagem)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cria um repositório isolado com o cache de checks ligado."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    _git(caminho, "config", "revisor.cacheChecks", "true")
    monkeypatch.chdir(caminho)
    return caminho


def _config_check(repo: Path, codigo: str) -> None:
    comando = f"python3 -c {shlex.quote(codigo)}"
    _git(repo, "config", "--add", "revisor.check", comando)


def _codigo_contador(contador: Path, falhar: bool = False) -> str:
    """Check manual que conta execuções num arquivo fora do repositório."""
    falha = "import sys; sys.exit(1); " if falhar else ""
    return ("from pathlib import Path; "
            f"c=Path({str(contador)!r}); "
            "c.write_text(str(int(c.read_text())+1 if c.exists() else 1)); "
            + falha)


def _gravar_git(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Substitui mod.git por um wrapper que registra os args e delega ao real."""
    reais = mod.git
    chamadas: list[list[str]] = []

    def _envolv(args: list[str], cwd: str | None = None,
                timeout: int = mod.TEMPO_GIT,
                env_extra: dict[str, str] | None = None
                ) -> subprocess.CompletedProcess:
        chamadas.append(list(args))
        return reais(args, cwd=cwd, timeout=timeout, env_extra=env_extra)

    monkeypatch.setattr(mod, "git", _envolv)
    return chamadas


def _sem_worktree_nem_checkout(chamadas: list[list[str]]) -> None:
    """Nenhuma chamada registrada cria ou atualiza worktree."""
    assert chamadas
    for args in chamadas:
        assert "worktree" not in args
        assert "checkout" not in args


def test_amend_mesma_arvore_roda_uma_vez(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`commit --amend` sem mudança de conteúdo reaproveita o cache da árvore."""
    contador = tmp_path / "execucoes.txt"
    sha1 = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, _codigo_contador(contador))
    assert mod.rodar_checks(sha1, str(repo)) == (True, "")
    assert contador.read_text(encoding="utf-8") == "1"

    sha2 = _emendar(repo, "outra")
    assert sha2 != sha1
    chamadas = _gravar_git(monkeypatch)
    assert mod.rodar_checks(sha2, str(repo)) == (True, "")
    assert contador.read_text(encoding="utf-8") == "1"
    _sem_worktree_nem_checkout(chamadas)


def test_check_falho_nao_fica_em_cache(repo: Path, tmp_path: Path) -> None:
    """Check reprovado roda de novo na 2ª chamada e ambas reprovam."""
    contador = tmp_path / "execucoes.txt"
    sha = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, _codigo_contador(contador, falhar=True))
    ok1, _rel1 = mod.rodar_checks(sha, str(repo))
    ok2, _rel2 = mod.rodar_checks(sha, str(repo))
    assert not ok1
    assert not ok2
    assert contador.read_text(encoding="utf-8") == "2"


def test_cache_desligado_roda_toda_vez(repo: Path, tmp_path: Path) -> None:
    """`revisor.cacheChecks false`: o mesmo check roda nas duas chamadas."""
    contador = tmp_path / "execucoes.txt"
    sha = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, _codigo_contador(contador))
    _git(repo, "config", "revisor.cacheChecks", "false")
    assert mod.rodar_checks(sha, str(repo)) == (True, "")
    assert mod.rodar_checks(sha, str(repo)) == (True, "")
    assert contador.read_text(encoding="utf-8") == "2"


def test_conteudo_diferente_roda_de_novo(repo: Path, tmp_path: Path) -> None:
    """Árvore nova (commit mudando arquivo coberto) roda o check de novo."""
    contador = tmp_path / "execucoes.txt"
    sha1 = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, _codigo_contador(contador))
    assert mod.rodar_checks(sha1, str(repo)) == (True, "")
    sha2 = _commit(repo, {"app.py": "valor = 2\n"}, "muda app")
    assert mod.rodar_checks(sha2, str(repo)) == (True, "")
    assert contador.read_text(encoding="utf-8") == "2"


def test_auto_segunda_na_mesma_arvore_pula_deteccao(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No automático, 2ª chamada na árvore não detecta nem cria worktree."""
    contador = tmp_path / "execucoes.txt"
    chamadas_deteccao: list[int] = []

    def _deteccao_falsa(_tmp: str, _topo: str,
                        pulos: list[str] | None = None
                        ) -> list[tuple[str, list[str], str]]:
        chamadas_deteccao.append(1)
        return [("", ["python3", "-c", _codigo_contador(contador)], "")]

    monkeypatch.setattr(mod, "_auto_check_ativo", lambda _topo: True)
    monkeypatch.setattr(mod, "detectar_checks", _deteccao_falsa)
    sha1 = _commit(repo, {"app.py": "x = 1\n"}, "base")
    assert mod.rodar_checks(sha1, str(repo)) == (True, "")
    assert chamadas_deteccao == [1]

    sha2 = _emendar(repo, "outra")
    chamadas = _gravar_git(monkeypatch)
    assert mod.rodar_checks(sha2, str(repo)) == (True, "")
    assert chamadas_deteccao == [1]
    assert contador.read_text(encoding="utf-8") == "1"
    _sem_worktree_nem_checkout(chamadas)


def test_deteccao_em_cache_depende_da_base(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A detecção em cache valida os checks que cobrem a base desta chamada."""
    contadores = {pasta: tmp_path / f"{pasta}.txt" for pasta in ("a", "b")}

    def _codigo_contador_pasta(pasta: str) -> str:
        contador = contadores[pasta]
        return ("from pathlib import Path; "
                f"c=Path({str(contador)!r}); "
                "c.write_text(str(int(c.read_text())+1 if c.exists() else 1))")

    checks = [(pasta, ["python3", "-c", _codigo_contador_pasta(pasta)], pasta)
              for pasta in ("a", "b")]
    monkeypatch.setattr(mod, "_auto_check_ativo", lambda _topo: True)
    monkeypatch.setattr(mod, "detectar_checks",
                        lambda _tmp, _topo, _pulos=None: checks)

    inicial = _commit(repo, {"a/x": "a0\n", "b/y": "b0\n"}, "base")
    base_a = _commit(repo, {"b/y": "b1\n"}, "base a")
    arvore_final_a = _commit(repo, {"a/x": "a1\n"}, "final a")
    _git(repo, "checkout", "-q", inicial)
    base_b = _commit(repo, {"a/x": "a1\n"}, "base b")
    arvore_final_b = _commit(repo, {"b/y": "b1\n"}, "final b")
    assert mod.arvore_de(arvore_final_a) == mod.arvore_de(arvore_final_b)

    assert mod.rodar_checks(arvore_final_a, str(repo), base=base_a) == (True, "")
    assert contadores["a"].read_text(encoding="utf-8") == "1"
    assert not contadores["b"].exists()

    assert mod.rodar_checks(arvore_final_a, str(repo), base=base_b) == (True, "")
    assert contadores["a"].read_text(encoding="utf-8") == "1"
    assert contadores["b"].read_text(encoding="utf-8") == "1"


def test_arvore_de_constante_vazia() -> None:
    """`arvore_de` do sha vazio devolve a própria árvore vazia."""
    assert mod.arvore_de(mod.VAZIO_SHA) == mod.VAZIO_SHA
