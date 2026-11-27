"""Testes das vagas de checks e do filtro rápido de testes pesados (r17)."""
from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).parent.parent / "git_revisor.py"
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_LISTA = ["py", "-m", "pytest", "-q"]
_ARQUIVOS = ["tests/tv/test_a.py", "tests/test_x_browser.py", "tests/test_leve.py"]
_IGNORAR = ["tests/tv/*", "*_browser.py"]


def test_rapido_ignora_pesados():
    """Só o teste leve entra no comando rápido."""
    assert mod.selecionar_teste_rapido(
        "", _LISTA, _ARQUIVOS, ignorar=_IGNORAR) == [
        "py", "-m", "pytest", "-q", "tests/test_leve.py"]


def test_rapido_so_pesados_coleta():
    """Sem leve, o rápido só coleta os pesados (`--collect-only`)."""
    assert mod.selecionar_teste_rapido(
        "", _LISTA, _ARQUIVOS[:2], ignorar=_IGNORAR) == [
        "py", "-m", "pytest", "-q", "--collect-only",
        "tests/tv/test_a.py", "tests/test_x_browser.py"]


def test_rapido_sem_ignorar_mantem_tudo():
    """`ignorar=None` mantém o comportamento atual (os três arquivos)."""
    assert mod.selecionar_teste_rapido("", _LISTA, _ARQUIVOS) == [
        "py", "-m", "pytest", "-q", *_ARQUIVOS]


def test_padroes_rapido_ignorar_filtra_vazios(monkeypatch):
    """`padroes_rapido_ignorar(None)` devolve só valores não vazios."""
    monkeypatch.setattr(mod, "cfg_todos",
                        lambda chave: ["tests/tv/*", "", "  ", "*_browser.py"]
                        if chave == "rapidoIgnorar" else [])
    assert mod.padroes_rapido_ignorar() == ["tests/tv/*", "*_browser.py"]


def _cfg_vagas(monkeypatch, vagas: int):
    """Fixa `revisor.vagasChecks` sem depender de git config."""
    monkeypatch.setattr(
        mod, "cfg",
        lambda chave, padrao="": {"vagasChecks": str(vagas)}.get(chave, padrao))


def test_vaga_extra_quando_vaga0_ocupada(monkeypatch):
    """Vaga 0 presa + carga ok: `trava_vaga_checks(0)` pega a vaga 1."""
    _cfg_vagas(monkeypatch, 2)
    monkeypatch.setattr(mod, "carga_ok", lambda: True)
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 999999)
    with mod.trava(None, arquivo=mod.arquivo_trava_checks()):
        with mod.trava_vaga_checks(0) as livre:
            assert livre is True


def test_sem_vaga_extra_com_carga_alta(monkeypatch):
    """Vaga 0 presa + carga alta: `trava_vaga_checks(0)` não pega nada."""
    _cfg_vagas(monkeypatch, 2)
    monkeypatch.setattr(mod, "carga_ok", lambda: False)
    with mod.trava(None, arquivo=mod.arquivo_trava_checks()):
        with mod.trava_vaga_checks(0) as livre:
            assert livre is False


def test_uma_vaga_ocupada_trava_e_acusa(monkeypatch):
    """Uma vaga só, presa: nada livre e `lock_checks_ocupado()` True."""
    _cfg_vagas(monkeypatch, 1)
    with mod.trava(None, arquivo=mod.arquivo_trava_checks()):
        with mod.trava_vaga_checks(0) as livre:
            assert livre is False
        assert mod.lock_checks_ocupado() is True
    assert mod.lock_checks_ocupado() is False


def test_lock_ocupado_so_com_todas_vagas_presas(monkeypatch):
    """Vaga 0 presa + vaga 1 livre: False; ambas presas: True."""
    _cfg_vagas(monkeypatch, 2)
    monkeypatch.setattr(mod, "carga_ok", lambda: True)
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 999999)
    vagas = mod.arquivos_vagas_checks()
    assert [vaga.name for vaga in vagas] == ["checks.lock", "checks.lock.1"]
    with mod.trava(None, arquivo=vagas[0]):
        assert mod.lock_checks_ocupado() is False
        with mod.trava(None, arquivo=vagas[1]):
            assert mod.lock_checks_ocupado() is True
    assert mod.lock_checks_ocupado() is False


def test_lock_ocupado_vaga_extra_sem_carga(monkeypatch):
    """Vaga 0 presa + extra livre, carga alta: ocupado (extra não conta)."""
    _cfg_vagas(monkeypatch, 2)
    monkeypatch.setattr(mod, "carga_ok", lambda: False)
    vagas = mod.arquivos_vagas_checks()
    with mod.trava(None, arquivo=vagas[0]):
        assert mod.lock_checks_ocupado() is True
    assert mod.lock_checks_ocupado() is False


def test_lock_ocupado_vaga_extra_sem_memoria(monkeypatch):
    """Vaga 0 presa + extra livre, memória baixa: ocupado."""
    _cfg_vagas(monkeypatch, 2)
    monkeypatch.setattr(mod, "carga_ok", lambda: True)
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 0)
    vagas = mod.arquivos_vagas_checks()
    with mod.trava(None, arquivo=vagas[0]):
        assert mod.lock_checks_ocupado() is True


def test_check_final_executa_pesado_sem_collect_only(monkeypatch, tmp_path):
    """Check final selecionado não usa `rapidoIgnorar`: pesado executa."""
    pesado = tmp_path / "tests" / "tv" / "test_a.py"
    pesado.parent.mkdir(parents=True)
    pesado.write_text("def test_a():\n    assert True\n", encoding="utf-8")

    class _Proc:
        """CompletedProcess mínimo para o fake de `mod.git`."""

        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def _git_falso(args, cwd=None, **_kw):
        if args == ["config", "--get-all", "revisor.check"]:
            return _Proc(0, f"{sys.executable} -m pytest -q\n")
        if args == ["config", "--get-all", "revisor.rapidoIgnorar"]:
            return _Proc(0, "tests/tv/*\n*_browser.py\n")
        return _Proc(1)

    monkeypatch.setattr(mod, "git", _git_falso)
    monkeypatch.setattr(mod, "cache_checks_ativo", lambda _topo: False)
    monkeypatch.setattr(mod, "log", lambda _msg: None)

    @contextlib.contextmanager
    def _wt(_sha, _topo):
        yield str(tmp_path)

    @contextlib.contextmanager
    def _trava():
        yield True

    monkeypatch.setattr(mod, "worktree_checks", _wt)
    monkeypatch.setattr(mod, "trava_checks", _trava)
    recebidos = {}

    def _executar(comandos, _tmp, _timeout, _pulos=None, **_kw):
        recebidos["comandos"] = comandos
        return True, ""

    monkeypatch.setattr(mod, "_executar_checks", _executar)
    ok, rel = mod._rodar_checks_selecionados(
        "abc", str(tmp_path), ["tests/tv/test_a.py"])
    assert (ok, rel) == (True, "")
    assert recebidos["comandos"] == [
        ("", [sys.executable, "-m", "pytest", "-q",
              "tests/tv/test_a.py"], "", True)]
    assert "--collect-only" not in recebidos["comandos"][0][1]


class _ArqFalso:
    """Arquivo falso que registra o fechamento."""

    def __init__(self):
        self.fechado = False

    def close(self):
        self.fechado = True


class _PaiFalso:
    """Pai falso com `mkdir` inofensivo."""

    def mkdir(self, parents=False, exist_ok=False):
        pass


class _CaminhoFalso:
    """Caminho falso cuja abertura pode falhar."""

    def __init__(self, arq=None, erro=False):
        self.parent = _PaiFalso()
        self._arq = arq
        self._erro = erro

    def open(self, _modo):
        if self._erro:
            raise OSError("falha ao abrir")
        return self._arq


def test_trava_vaga_falha_ao_abrir_fecha_ja_abertos(monkeypatch):
    """Falha ao abrir a 2ª vaga fecha a 1ª já aberta (sem vazar)."""
    arq = _ArqFalso()
    vagas = [_CaminhoFalso(arq), _CaminhoFalso(erro=True)]
    monkeypatch.setattr(mod, "arquivos_vagas_checks", lambda: vagas)
    with pytest.raises(OSError):
        with mod.trava_vaga_checks(0):
            pass
    assert arq.fechado is True
