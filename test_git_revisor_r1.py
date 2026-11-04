"""Testes R1: correção do push em fundo com HEAD:<branch> e push interno."""
from __future__ import annotations

import importlib.util
import json
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
    """Repositório git na branch main, revisor desligado, como cwd."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _escrever_pedido(repo: Path, sha: str) -> Path:
    """Grava o pedido de correção em fundo que cmd_corrigir_push consome."""
    pasta = mod.base_dir() / "push-fundo"
    pasta.mkdir(parents=True, exist_ok=True)
    dados = {"novos": [sha], "remote": "origin",
             "remote_ref": "refs/heads/main", "local_ref": "refs/heads/main"}
    caminho = pasta / f"{sha}.json"
    caminho.write_text(json.dumps(dados) + "\n", encoding="utf-8")
    return caminho


def _git_push_estourando(monkeypatch: pytest.MonkeyPatch) -> None:
    """Troca mod.git: delega ao real, mas `git push` levanta TimeoutExpired."""
    real_git = mod.git

    def _falso(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Delega tudo ao git real menos push, que estoura o tempo."""
        if args[0] == "push":
            timeout = kwargs.get("timeout", 0)
            assert isinstance(timeout, int)
            raise subprocess.TimeoutExpired(["git", *args], timeout)
        return real_git(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(mod, "git", _falso)


# ----------------------------------------------------------- resolver_ref_local

def test_resolver_ref_local(repo: Path) -> None:
    """Branch resolve como está; HEAD vira refs/heads/main; resto é None."""
    resolver = mod.resolver_ref_local
    sha_a = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha_b = _commit(repo, {"b.txt": "2\n"}, "feat: b")
    assert resolver("refs/heads/x", sha_a, str(repo)) == "refs/heads/x"
    assert resolver("HEAD", sha_b, str(repo)) == "refs/heads/main"
    assert resolver("HEAD", sha_a, str(repo)) is None  # sha não é o HEAD
    assert resolver(sha_a, sha_a, str(repo)) is None  # sha solto
    _git(repo, "checkout", "-q", "--detach", "HEAD")
    assert resolver("HEAD", sha_b, str(repo)) is None  # HEAD destacado


# -------------------------------------------------------- caminho_correcao_push

def test_caminho_correcao_push_head_em_branch(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """push HEAD:<branch> em branch agenda a correção com refs/heads/<branch>."""
    monkeypatch.delenv("REVISOR_TRAVA_SEGURA", raising=False)
    monkeypatch.delenv("REVISOR_SEM_CORRECAO", raising=False)
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    chamadas: list[dict] = []

    def _disparar_falso(novos: list[str], local_sha: str, remote: str,
                        remote_ref: str, topo: str,
                        local_ref: str | None = None) -> int:
        """Registra os argumentos e simula pedido agendado."""
        chamadas.append({"local_sha": local_sha, "local_ref": local_ref})
        return 1

    def _proibido(*_a: object, **_k: object) -> int:
        """Falha se a correção for para o caminho em frente."""
        raise AssertionError("corrigir_no_push não deveria ser chamada")

    monkeypatch.setattr(mod, "disparar_correcao_push", _disparar_falso)
    monkeypatch.setattr(mod, "corrigir_no_push", _proibido)
    rc = mod.caminho_correcao_push([sha], sha, "relatório", str(repo),
                                   "origin", "refs/heads/main", "HEAD")
    assert rc == 1
    assert chamadas == [{"local_sha": sha, "local_ref": "refs/heads/main"}]


def test_caminho_correcao_push_head_destacado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HEAD destacado não é branch: correção vai para o caminho em frente."""
    monkeypatch.delenv("REVISOR_TRAVA_SEGURA", raising=False)
    monkeypatch.delenv("REVISOR_SEM_CORRECAO", raising=False)
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "--detach")
    caminho_em_frente: list[tuple] = []

    def _corrigir_falso(novos: list[str], local_sha: str, relatorio: str,
                        topo: str) -> int:
        """Registra a chamada do caminho em frente."""
        caminho_em_frente.append((tuple(novos), local_sha, relatorio, topo))
        return 1

    def _proibido(*_a: object, **_k: object) -> int:
        """Falha se a correção for agendada em fundo."""
        raise AssertionError("disparar_correcao_push não deveria ser chamada")

    monkeypatch.setattr(mod, "corrigir_no_push", _corrigir_falso)
    monkeypatch.setattr(mod, "disparar_correcao_push", _proibido)
    rc = mod.caminho_correcao_push(["abc1234"], "abc1234", "relatório",
                                   str(repo), "origin", "refs/heads/main",
                                   "HEAD")
    assert rc == 1
    assert caminho_em_frente == [(("abc1234",), "abc1234", "relatório",
                                  str(repo))]


def test_caminho_correcao_push_sem_correcao(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REVISOR_SEM_CORRECAO=1 bloqueia sem corrigir em frente nem em fundo."""
    monkeypatch.setenv("REVISOR_SEM_CORRECAO", "1")
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: a")

    def _proibido(nome: str):
        """Fabrica espião que falha se a correção partir."""
        def _falha(*_a: object, **_k: object) -> int:
            """Falha sempre: correção não deveria partir."""
            raise AssertionError(f"{nome} não deveria ser chamada")
        return _falha

    monkeypatch.setattr(mod, "corrigir_no_push", _proibido("corrigir_no_push"))
    monkeypatch.setattr(mod, "disparar_correcao_push",
                        _proibido("disparar_correcao_push"))
    assert mod.caminho_correcao_push([sha], sha, "relatório", str(repo),
                                     "origin", "refs/heads/main",
                                     "refs/heads/main") == 1


# --------------------------------------------------------------- push interno

def test_cmd_corrigir_push_estouro_de_tempo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git push interno que estoura o tempo vira notificação, sem traceback."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    pedido = _escrever_pedido(repo, sha)
    _git_push_estourando(monkeypatch)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))

    def _fluxo_falso(_sha_original: str, _relatorio: str, _topo: str,
                     lote: list[str] | None = None) -> int:
        """Avança a branch com um commit descendente do sha reprovado."""
        _commit(repo, {"fix.txt": "x\n"}, "fix: correção")
        return 0

    monkeypatch.setattr(mod, "fluxo_correcao", _fluxo_falso)
    rc = mod.cmd_corrigir_push(sha)
    assert rc == 0
    assert avisos and avisos[0][0] == "Envio falhou"
    assert "estourou o tempo ou falhou" in avisos[0][1]
    assert not pedido.exists()


def test_pushes_internos_env_e_tempo(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path) -> None:
    """Pushes internos passam REVISOR_SEM_CORRECAO=1 e TEMPO_PUSH_INTERNO."""
    monkeypatch.delenv("REVISOR_TRAVA_SEGURA", raising=False)
    monkeypatch.delenv("REVISOR_SEM_CORRECAO", raising=False)
    monkeypatch.delenv("REVISOR_TEMPO_PUSH_INTERNO", raising=False)
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    remoto = tmp_path / "remoto.git"
    _git(repo, "init", "--bare", "-q", str(remoto))
    _git(repo, "remote", "add", "origin", str(remoto))
    _git(repo, "push", "-q", "-u", "origin", "main")
    pedidos: list[dict] = []
    real_git = mod.git

    def _falso(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Delega ao git real; push é registrado e aprovado sem rodar."""
        if args[0] == "push":
            pedidos.append({"args": list(args),
                            "timeout": kwargs.get("timeout"),
                            "env_extra": kwargs.get("env_extra")})
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        return real_git(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(mod, "git", _falso)
    monkeypatch.setattr(mod, "notificar", lambda *_a: None)

    def _fluxo_falso(_sha_original: str, _relatorio: str, _topo: str,
                     lote: list[str] | None = None) -> int:
        """Avança a branch com um commit descendente do sha reprovado."""
        _commit(repo, {"fix.txt": "x\n"}, "fix: correção")
        return 0

    monkeypatch.setattr(mod, "fluxo_correcao", _fluxo_falso)
    _escrever_pedido(repo, sha)
    assert mod.cmd_corrigir_push(sha) == 0
    assert len(pedidos) == 1
    assert pedidos[0]["timeout"] == mod.TEMPO_PUSH_INTERNO
    assert pedidos[0]["env_extra"] == {"REVISOR_TRAVA_SEGURA": "1",
                                       "REVISOR_SEM_CORRECAO": "1"}

    pedidos.clear()
    ponta = _git(repo, "rev-parse", "HEAD").stdout.strip()
    mod.autopush(ponta, str(repo))
    assert len(pedidos) == 1
    assert pedidos[0]["args"][0] == "push"
    assert pedidos[0]["timeout"] == mod.TEMPO_PUSH_INTERNO
    assert pedidos[0]["env_extra"]["REVISOR_SEM_CORRECAO"] == "1"
