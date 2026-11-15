"""Testes R16: push segurado em HEAD destacado retenta pelo sha.

Com `local_ref="HEAD"` em worktree destacado, o push segurado (lock de
checks ocupado) não tem branch para seguir: o commit é preso em
`refs/revisor/retentar/<sha>`, o pedido guarda o sha como `local_ref` e a
retentativa roda a partir do worktree principal, que sobrevive à cópia.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor_r16", _ARQUIVO)
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
    """Repositório principal temporário em main, revisor desligado."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q", "-b", "main")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    _git(caminho, "config", "revisor.ordem", "check-primeiro")
    _commit(caminho, {"a.txt": "1\n"}, "feat: base")
    monkeypatch.chdir(caminho)
    return caminho


@pytest.fixture()
def copia(repo: Path, tmp_path: Path,
          monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """Worktree vinculado destacado com commit novo; devolve (caminho, sha)."""
    destino = tmp_path / "copia"
    _git(repo, "worktree", "add", "-q", "--detach", str(destino), "main")
    sha = _commit(destino, {"a.txt": "2\n"}, "feat: na copia")
    monkeypatch.chdir(destino)
    return destino, sha


def _interceptar_popen(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Troca Popen por fake que só intercepta o disparo retentar-push.

    Registra `cmd` e `cwd` de cada disparo; git e o resto usam Popen real.
    """
    disparos: list[dict] = []

    class _ProcFalso:
        pid = 99_999

    real_popen = subprocess.Popen

    def _popen_falso(cmd, *args: object, **kw: object):
        """Desvia o disparo retentar-push; os demais seguem reais."""
        if any("retentar-push" in str(parte) for parte in cmd):
            disparos.append({"cmd": list(cmd), "cwd": kw.get("cwd")})
            return _ProcFalso
        return real_popen(cmd, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(mod.subprocess, "Popen", _popen_falso)
    return disparos


def _segurar_com_lock_ocupado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gitleaks passa e o check final devolve CHECK_OCUPADO."""
    monkeypatch.setattr(mod, "gitleaks_intervalo", lambda *a, **k: 0)
    monkeypatch.setattr(mod, "rodar_checks",
                        lambda *a, **k: (False, f"{mod.CHECK_OCUPADO}: x"))


# ------------------------------------------------------------ pasta_principal

def test_pasta_principal_de_worktree_vinculado(
        repo: Path, copia: tuple[Path, str]) -> None:
    """Worktree vinculado resolve para o principal; o principal, para si."""
    caminho_copia, _sha = copia
    assert Path(mod.pasta_principal(str(caminho_copia))).resolve() == \
        repo.resolve()
    assert Path(mod.pasta_principal(str(repo))).resolve() == repo.resolve()


# --------------------------------------------- segurado em HEAD destacado

def test_head_destacado_segurado_agenda_retentativa_pelo_sha(
        repo: Path, copia: tuple[Path, str], monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture) -> None:
    """Push segurado em HEAD destacado grava o sha e dispara no principal."""
    caminho_copia, sha = copia
    disparos = _interceptar_popen(monkeypatch)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    _segurar_com_lock_ocupado(monkeypatch)
    rc = mod.revisar_intervalo([sha], sha, str(caminho_copia), [], "origin",
                               "refs/heads/main", "HEAD")
    assert rc == 1
    assert len(disparos) == 1
    assert "retentar-push" in disparos[0]["cmd"]
    assert Path(disparos[0]["cwd"]).resolve() == repo.resolve()
    pedido = mod.base_dir() / "retentar-push" / f"{sha}.json"
    dados = json.loads(pedido.read_text(encoding="utf-8"))
    assert dados["local_ref"] == sha
    assert dados["remote_ref"] == "refs/heads/main"
    # a cópia pode sumir; o commit fica preso na ref do repositório principal
    _git(repo, "worktree", "remove", "--force", str(caminho_copia))
    presa = _git(repo, "rev-parse", f"refs/revisor/retentar/{sha}")
    assert presa.stdout.strip() == sha
    saida = capsys.readouterr().out
    assert mod.MARCADOR_PUSH_SEGURADO in saida
    assert mod.MARCADOR_RETENTATIVA_AGENDADA in saida
    assert avisos == []


def test_segurado_sem_agendar_diz_a_verdade_e_notifica(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture) -> None:
    """Disparo que falha: marcador de retentativa ausente e notificação."""
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(mod, "disparar_retentar_push",
                        lambda *a, **k: False)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    _segurar_com_lock_ocupado(monkeypatch)
    rc = mod.revisar_intervalo([sha], sha, str(repo), [], "origin",
                               "refs/heads/main", "HEAD")
    assert rc == 1
    saida = capsys.readouterr().out
    assert mod.MARCADOR_PUSH_SEGURADO in saida
    assert mod.MARCADOR_RETENTATIVA_AGENDADA not in saida
    assert "NÃO foi agendada" in saida
    assert any("NÃO foi agendada" in msg for _titulo, msg in avisos)


# --------------------------------------------------- retentar-push destacado

def test_retentar_push_destacado_envia_sha_e_solta_a_ref(
        repo: Path, copia: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Retentativa do sha destacado faz push, apaga pedido e solta a ref."""
    caminho_copia, sha = copia
    _interceptar_popen(monkeypatch)
    monkeypatch.setattr(mod, "notificar", lambda titulo, msg: None)
    _segurar_com_lock_ocupado(monkeypatch)
    assert mod.revisar_intervalo([sha], sha, str(caminho_copia), [], "origin",
                                 "refs/heads/main", "HEAD") == 1
    pedido = mod.base_dir() / "retentar-push" / f"{sha}.json"
    assert pedido.exists()
    _git(repo, "worktree", "remove", "--force", str(caminho_copia))
    monkeypatch.chdir(repo)

    chamadas: list[list[str]] = []
    real = mod.git

    def fake_git(args, cwd=None, timeout=mod.TEMPO_GIT, env_extra=None):
        """Grava chamadas; push finge sucesso, o resto delega ao git real."""
        chamadas.append(list(args))
        if args and args[0] == "push":
            return subprocess.CompletedProcess(args, 0, "", "")
        return real(args, cwd=cwd, timeout=timeout, env_extra=env_extra)

    monkeypatch.setattr(mod, "git", fake_git)
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 99_999)
    monkeypatch.setattr(mod, "lock_checks_ocupado", lambda: False)
    monkeypatch.setattr(mod, "push_esperando", lambda: False)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    rc = mod.cmd_retentar_push(sha)
    assert rc == 0
    assert ["push", "origin", f"{sha}:refs/heads/main"] in chamadas
    assert any(titulo == "Push enviado" for titulo, _msg in avisos)
    assert not pedido.exists()
    solta = subprocess.run(["git", "rev-parse", "-q", "--verify",
                            f"refs/revisor/retentar/{sha}"],
                           cwd=repo, check=False, capture_output=True,
                           text=True)
    assert solta.returncode != 0


def test_retentar_push_destacado_sem_commit_avisa_e_nao_envia(
        repo: Path, copia: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Commit destacado que sumiu: avisa, não faz push nem diz 'branch andou'."""
    caminho_copia, sha = copia
    _interceptar_popen(monkeypatch)
    monkeypatch.setattr(mod, "notificar", lambda titulo, msg: None)
    _segurar_com_lock_ocupado(monkeypatch)
    assert mod.revisar_intervalo([sha], sha, str(caminho_copia), [], "origin",
                                 "refs/heads/main", "HEAD") == 1
    monkeypatch.chdir(repo)
    real = mod.git

    def fake_git(args, cwd=None, timeout=mod.TEMPO_GIT, env_extra=None):
        """rev-parse do sha falha (commit sumiu); push não pode acontecer."""
        assert not args or args[0] != "push"
        if args[:3] == ["rev-parse", "-q", "--verify"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        return real(args, cwd=cwd, timeout=timeout, env_extra=env_extra)

    monkeypatch.setattr(mod, "git", fake_git)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    assert mod.cmd_retentar_push(sha) == 1
    assert any(titulo == "Push ainda bloqueado" for titulo, _msg in avisos)


def test_pasta_principal_de_repo_bare_devolve_o_proprio(tmp_path: Path) -> None:
    """Repositório bare não tem worktree principal: devolve a pasta recebida."""
    bare = tmp_path / "remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    assert mod.pasta_principal(str(bare)) == str(bare)


def test_destacado_sem_prender_commit_nao_agenda_e_avisa(
        repo: Path, copia: tuple[Path, str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """update-ref falhou: sem retentativa, mensagem honesta e notificação."""
    caminho_copia, sha = copia
    disparos = _interceptar_popen(monkeypatch)
    _segurar_com_lock_ocupado(monkeypatch)
    real = mod.git
    monkeypatch.setattr(mod, "git", lambda args, *a, **k: (
        subprocess.CompletedProcess(args, 1, "", "sem permissão")
        if args[:1] == ["update-ref"] else real(args, *a, **k)))
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    assert mod.revisar_intervalo([sha], sha, str(caminho_copia), [], "origin",
                                 "refs/heads/main", "HEAD") == 1
    assert disparos == []
    assert any("NÃO foi agendada" in msg for _titulo, msg in avisos)
