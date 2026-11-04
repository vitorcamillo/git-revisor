"""Testes do worktree persistente e do log de duração dos checks."""
from __future__ import annotations

import importlib.util
import shlex
import subprocess
import tempfile
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor_r2", _ARQUIVO)
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


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cria um repositório isolado e configura o usuário Git."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    _git(caminho, "config", "revisor.cacheChecks", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _config_check(repo: Path, codigo: str) -> None:
    comando = f"python3 -c {shlex.quote(codigo)}"
    _git(repo, "config", "--add", "revisor.check", comando)


def _codigo_marcador(contador: Path, exigir: bool = False) -> str:
    """Gera check manual que conta execuções fora do worktree."""
    marcador = "assert p.exists(); " if exigir else ""
    return ("from pathlib import Path; "
            f"c=Path({str(contador)!r}); "
            "c.write_text(str(int(c.read_text())+1 if c.exists() else 1)); "
            "p=Path('target/marcador'); " + marcador +
            "p.parent.mkdir(exist_ok=True); p.write_text('ok')")


def test_dois_commits_reaproveitam_worktree(repo: Path,
                                           tmp_path: Path) -> None:
    """O marcador de build sobrevive a uma alteração simples entre checks."""
    contador = tmp_path / "execucoes.txt"
    _commit(repo, {"app.py": "valor = 1\n"}, "base")
    sha1 = _commit(repo, {"app.py": "valor = 2\n"}, "alteracao um")
    _config_check(repo, _codigo_marcador(contador))
    assert mod.rodar_checks(sha1, str(repo)) == (True, "")

    sha2 = _commit(repo, {"app.py": "valor = 3\n"}, "alteracao dois")
    _git(repo, "config", "--replace-all", "revisor.check",
         f"python3 -c {shlex.quote(_codigo_marcador(contador, exigir=True))}")
    assert mod.rodar_checks(sha2, str(repo), base=sha1) == (True, "")
    assert contador.read_text(encoding="utf-8") == "2"
    assert (mod.pasta_cache_repo() / "wt").is_dir()


def test_arquivo_apagado_dispara_limpeza_total(repo: Path,
                                               tmp_path: Path) -> None:
    """Uma remoção versionada elimina artefatos antigos de target/."""
    contador = tmp_path / "execucoes.txt"
    _commit(repo, {"app.py": "valor = 1\n", "remover.txt": "x\n"}, "base")
    sha1 = _commit(repo, {"app.py": "valor = 2\n"}, "alteracao")
    _config_check(repo, _codigo_marcador(contador))
    assert mod.rodar_checks(sha1, str(repo)) == (True, "")

    sha2 = _commit(repo, {"app.py": "valor = 3\n", "remover.txt": None},
                   "remove arquivo")
    codigo = ("from pathlib import Path; "
              "assert not Path('target/marcador').exists()")
    _git(repo, "config", "--replace-all", "revisor.check",
         f"python3 -c {shlex.quote(codigo)}")
    assert mod.rodar_checks(sha2, str(repo), base=sha1) == (True, "")
    marcador = mod.pasta_cache_repo() / "wt" / "target" / "marcador"
    assert not marcador.exists()


def test_worktree_desligado_remove_temporario(repo: Path,
                                               tmp_path: Path) -> None:
    """A opção false mantém o fluxo temporário e não deixa worktree solto."""
    _git(repo, "config", "revisor.worktreePersistente", "false")
    sha = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, "pass")
    temporarios = Path(tempfile.gettempdir())
    antes = {p for p in temporarios.glob("revisor-*")}
    assert mod.rodar_checks(sha, str(repo)) == (True, "")
    depois = {p for p in temporarios.glob("revisor-*")}
    assert not (mod.pasta_cache_repo() / "wt").exists()
    assert depois - antes == set()


def test_lock_persistente_ocupado_usa_temporario(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock ocupado força fallback temporário, removido após o check."""
    sha = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, "pass")
    caminhos: list[Path] = []
    executar = mod._executar_checks

    def _capturar(checks, tmp, timeout_check, pulos=None):
        caminhos.append(Path(tmp))
        return executar(checks, tmp, timeout_check, pulos)

    monkeypatch.setattr(mod, "_executar_checks", _capturar)
    lock = mod.pasta_cache_repo() / "wt.lock"
    with mod.trava(0, arquivo=lock) as conseguiu:
        assert conseguiu
        assert mod.rodar_checks(sha, str(repo)) == (True, "")
    assert caminhos and caminhos[0].name.startswith("revisor-")
    assert not caminhos[0].exists()
    assert not (mod.pasta_cache_repo() / "wt").exists()


def test_log_registra_duracao_do_check(repo: Path) -> None:
    """O log do revisor inclui o código de saída e duração do check."""
    sha = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, "pass")
    assert mod.rodar_checks(sha, str(repo)) == (True, "")
    log = (mod.base_dir() / "log.txt").read_text(encoding="utf-8")
    assert "check exit=0 dur=" in log


def test_cmd_limpar_remove_worktree_persistente(repo: Path) -> None:
    """O comando limpar remove o worktree de checks sob lock."""
    sha = _commit(repo, {"app.py": "valor = 1\n"}, "base")
    _config_check(repo, "pass")
    assert mod.rodar_checks(sha, str(repo)) == (True, "")
    wt = mod.pasta_cache_repo() / "wt"
    assert wt.exists()
    assert mod.cmd_limpar() == 0
    assert not wt.exists()
