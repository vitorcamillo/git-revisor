"""Testes da faxina automática (r14c): refs, estado, avisos e cache."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _git(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Roda git no repositório de teste."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, env=env)


def _commit(repo: Path, arquivos: dict[str, str], mensagem: str,
            quando: str | None = None) -> str:
    """Grava e commita arquivos, devolvendo o SHA criado."""
    for relativo, conteudo in arquivos.items():
        caminho = repo / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    _git(repo, "add", "-A")
    env = None
    if quando is not None:
        env = dict(os.environ, GIT_AUTHOR_DATE=quando,
                   GIT_COMMITTER_DATE=quando)
    _git(repo, "commit", "-q", "-m", mensagem, env=env)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _ref_existe(repo: Path, ref: str) -> bool:
    p = subprocess.run(["git", "rev-parse", "-q", "--verify", ref], cwd=repo,
                       capture_output=True, text=True)
    return p.returncode == 0 and bool(p.stdout.strip())


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repositório isolado com cache/estado próprios."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q", "-b", "main")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    monkeypatch.chdir(caminho)
    return caminho


def _envelhecer(caminho: Path, dias: float) -> None:
    velho = time.time() - dias * 86400
    os.utime(caminho, (velho, velho))


# ------------------------------------------------------------- tentativas

def test_tentativa_velha_apagada_recente_mantida(repo: Path) -> None:
    """Ref velha sai; ref recente em branch e sem aprovação fica."""
    velho = _commit(repo, {"a.txt": "velho\n"}, "velho",
                    quando="2020-01-01T00:00:00")
    novo = _commit(repo, {"b.txt": "novo\n"}, "novo")
    _git(repo, "update-ref", f"refs/revisor/tentativa/{velho}", velho)
    _git(repo, "update-ref", f"refs/revisor/tentativa/{novo}", novo)
    cont = mod.faxina_repo(str(repo))
    assert cont["tentativas"] >= 1
    assert not _ref_existe(repo, f"refs/revisor/tentativa/{velho}")
    assert _ref_existe(repo, f"refs/revisor/tentativa/{novo}")


def test_tentativa_aprovada_apagada(repo: Path) -> None:
    """Ref recente de commit aprovado sai pela regra de aprovado."""
    sha = _commit(repo, {"a.txt": "x\n"}, "feat")
    mod.marcar_aprovado(sha, "ok")
    _git(repo, "update-ref", f"refs/revisor/tentativa/{sha}", sha)
    cont = mod.faxina_repo(str(repo))
    assert cont["tentativas"] == 1
    assert not _ref_existe(repo, f"refs/revisor/tentativa/{sha}")


# ------------------------------------------------------------------ estado

def test_estado_relatorios_e_log(repo: Path) -> None:
    """relatorios/ velho sai, recente fica; log.txt velho nunca sai."""
    _commit(repo, {"a.txt": "x\n"}, "feat")
    base = mod.base_dir()
    rel = base / "relatorios"
    rel.mkdir(parents=True, exist_ok=True)
    velho_arq = rel / "velho.md"
    novo_arq = rel / "novo.md"
    velho_arq.write_text("velho", encoding="utf-8")
    novo_arq.write_text("novo", encoding="utf-8")
    _envelhecer(velho_arq, 31)
    _envelhecer(novo_arq, 1)
    log_txt = base / "log.txt"
    log_txt.write_text("log", encoding="utf-8")
    _envelhecer(log_txt, 60)
    sub = rel / "subdir"
    sub.mkdir(exist_ok=True)
    cont = mod.faxina_repo(str(repo))
    assert cont["estado"] >= 1
    assert not velho_arq.exists()
    assert novo_arq.exists()
    assert log_txt.exists()
    assert sub.is_dir()


def test_dias_cfg_invalido_usa_padrao(repo: Path) -> None:
    """Config inválida (< 1 ou texto) cai no padrão."""
    assert mod._dias_cfg("retencaoEstadoDias", 30) == 30
    _git(repo, "config", "revisor.retencaoEstadoDias", "0")
    assert mod._dias_cfg("retencaoEstadoDias", 30) == 30
    _git(repo, "config", "revisor.retencaoEstadoDias", "lixo")
    assert mod._dias_cfg("retencaoEstadoDias", 30) == 30
    _git(repo, "config", "revisor.retencaoEstadoDias", "7")
    assert mod._dias_cfg("retencaoEstadoDias", 30) == 7


# ------------------------------------------------------------------ avisos

def test_aviso_pid_inexistente_apagado(repo: Path) -> None:
    """Aviso de pid morto sai na faxina."""
    _commit(repo, {"a.txt": "x\n"}, "feat")
    pasta = mod.pasta_push_esperando()
    pasta.mkdir(parents=True, exist_ok=True)
    aviso = pasta / "99999999"
    aviso.write_text("", encoding="utf-8")
    cont = mod.faxina_repo(str(repo))
    assert cont["avisos"] >= 1
    assert not aviso.exists()


# ------------------------------------------------------------------- cache

def test_cache_orfao_removido(repo: Path) -> None:
    """wt/.git apontando para gitdir inexistente remove a pasta inteira."""
    raiz = Path(os.environ["XDG_CACHE_HOME"]) / "git-revisor"
    pasta = raiz / "fake-abc123"
    wt = pasta / "wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /caminho/que/nao/existe\n",
                             encoding="utf-8")
    n = mod.faxina_cache_global()
    assert n >= 1
    assert not pasta.exists()


def test_cache_sem_git_removido(repo: Path) -> None:
    """wt sem .git remove a pasta inteira."""
    raiz = Path(os.environ["XDG_CACHE_HOME"]) / "git-revisor"
    pasta = raiz / "fake-semgit"
    wt = pasta / "wt"
    wt.mkdir(parents=True)
    n = mod.faxina_cache_global()
    assert n >= 1
    assert not pasta.exists()


def test_cache_de_origem_apagada_sai_sem_esperar(repo: Path,
                                                tmp_path: Path) -> None:
    """Origem (worktree temporário) apagada remove o cache recente."""
    _commit(repo, {"a.txt": "x\n"}, "feat")
    pasta = mod.pasta_cache_repo()
    _git(repo, "worktree", "add", "--detach", str(pasta / "wt"), "HEAD")
    origem = tmp_path / "wt-temporario"
    origem.mkdir()
    (pasta / "origem").write_text(str(origem), encoding="utf-8")
    assert mod.faxina_cache_global() == 0  # origem viva: fica
    origem.rmdir()
    assert mod.faxina_cache_global() >= 1
    assert not pasta.exists()


def test_cache_vivo_recente_mantido_velho_removido(repo: Path) -> None:
    """Cache vivo recente fica; envelhecido sai do worktree list do dono."""
    _commit(repo, {"a.txt": "x\n"}, "feat")
    pasta = mod.pasta_cache_repo()
    wt = pasta / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), "HEAD")
    assert wt.exists()
    assert mod.faxina_cache_global() == 0
    assert wt.exists()
    _envelhecer(wt, 20)
    _envelhecer(pasta, 20)
    fechadura = pasta / "wt.lock"
    if fechadura.exists():
        _envelhecer(fechadura, 20)
    n = mod.faxina_cache_global()
    assert n >= 1
    assert not wt.exists()
    lista = subprocess.run(["git", "worktree", "list", "--porcelain"],
                           cwd=repo, capture_output=True, text=True)
    assert str(wt) not in lista.stdout


# ---------------------------------------------------------- faxina_se_devida

def test_faxina_se_devida_respeita_carimbo(repo: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Segunda chamada antes de 24 h não roda; carimbo velho roda de novo."""
    _commit(repo, {"a.txt": "x\n"}, "feat")
    chamadas = {"repo": 0, "cache": 0}

    def _repo(_topo: str) -> dict[str, int]:
        chamadas["repo"] += 1
        return {"tentativas": 0, "estado": 0, "avisos": 0}

    def _cache() -> int:
        chamadas["cache"] += 1
        return 0

    monkeypatch.setattr(mod, "faxina_repo", _repo)
    monkeypatch.setattr(mod, "faxina_cache_global", _cache)
    mod.faxina_se_devida(str(repo))
    assert chamadas == {"repo": 1, "cache": 1}
    mod.faxina_se_devida(str(repo))
    assert chamadas == {"repo": 1, "cache": 1}
    _envelhecer(mod.base_dir() / "faxina-ultima", 2)
    estado = Path(os.environ["XDG_STATE_HOME"]) / "git-revisor"
    _envelhecer(estado / "faxina-cache-ultima", 2)
    mod.faxina_se_devida(str(repo))
    assert chamadas == {"repo": 2, "cache": 2}


# ---------------------------------------------------------------- exceções

def test_excecao_num_passo_nao_propaga(repo: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha num passo não derruba os outros nem levanta exceção."""
    sha = _commit(repo, {"a.txt": "x\n"}, "feat")
    _git(repo, "update-ref", f"refs/revisor/tentativa/{sha}", sha)
    base = mod.base_dir()
    rel = base / "relatorios"
    rel.mkdir(parents=True, exist_ok=True)
    velho_arq = rel / "velho.md"
    velho_arq.write_text("velho", encoding="utf-8")
    _envelhecer(velho_arq, 31)

    def _quebra(_sha: str) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "em_alguma_branch", _quebra)
    cont = mod.faxina_repo(str(repo))
    assert isinstance(cont, dict)
    assert not velho_arq.exists()
    assert mod.faxina_cache_global() == 0
