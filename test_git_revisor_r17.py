"""Testes das vagas de checks e do filtro rápido de testes pesados (r17)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
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
    vagas = mod.arquivos_vagas_checks()
    assert [vaga.name for vaga in vagas] == ["checks.lock", "checks.lock.1"]
    with mod.trava(None, arquivo=vagas[0]):
        assert mod.lock_checks_ocupado() is False
        with mod.trava(None, arquivo=vagas[1]):
            assert mod.lock_checks_ocupado() is True
    assert mod.lock_checks_ocupado() is False
