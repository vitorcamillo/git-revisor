"""Testes R3b: revisão paralela de partes do diff."""
from __future__ import annotations

import importlib.util
import subprocess
import threading
import time
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
    """Repositório git temporário para testar a configuração paralela."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _texto_quatro_partes(monkeypatch: pytest.MonkeyPatch) -> str:
    """Cria quatro blocos de arquivo que cabem separados no limite."""
    monkeypatch.setattr(mod, "MAX_DIFF_BYTES", 100)
    monkeypatch.setattr(mod, "MAX_PARTES", 6)
    blocos = [f"diff --git a/f{i} b/f{i}\n" + "x" * 65 + "\n"
              for i in range(1, 5)]
    texto = "".join(blocos)
    assert len(mod.dividir_em_partes(texto)) == 4
    return texto


def _veredito_da_parte(prompt: str, vereditos: dict[int, str]) -> tuple[str, str]:
    """Devolve o veredito configurado para a parte identificada no prompt."""
    parte = int(prompt.split("PARTE ", 1)[1].split(" de ", 1)[0])
    veredito = vereditos.get(parte, "APROVADO")
    saida = (f"saída da parte {parte}\nVEREDITO: {veredito}\n"
             "--- codex-ask: modo=review ---")
    return veredito, saida


@pytest.mark.parametrize("paralelo,pico_minimo", [(3, 2), (1, 1)])
def test_paralelo_limita_chamadas_concorrentes(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        paralelo: int, pico_minimo: int) -> None:
    """O pico concorrente respeita revisor.paralelo e o limite sequencial."""
    _git(repo, "config", "revisor.paralelo", str(paralelo))
    texto = _texto_quatro_partes(monkeypatch)
    trava = threading.Lock()
    estado = {"ativas": 0, "pico": 0}

    def _fake(prompt: str, _cwd: str) -> tuple[str, str]:
        """Registra concorrência por 0,3 s e aprova a parte."""
        with trava:
            estado["ativas"] += 1
            estado["pico"] = max(estado["pico"], estado["ativas"])
        try:
            time.sleep(0.3)
            return _veredito_da_parte(prompt, {})
        finally:
            with trava:
                estado["ativas"] -= 1

    monkeypatch.setattr(mod, "revisar_com_ia", _fake)
    resultado = mod.revisar_por_partes(texto, str(repo), "commits")
    assert resultado[0] == "APROVADO"
    assert estado["pico"] >= pico_minimo
    if paralelo == 1:
        assert estado["pico"] == 1


def test_paralelo_semantica_sem_cota_precede_reprovacao(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SEM_COTA da parte 2 precede REPROVADO da parte 3 por índice."""
    _git(repo, "config", "revisor.paralelo", "3")
    texto = _texto_quatro_partes(monkeypatch)
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, _cwd: _veredito_da_parte(
            prompt, {2: "SEM_COTA", 3: "REPROVADO"}))

    veredito, saida, familias = mod.revisar_por_partes(
        texto, str(repo), "commits")

    assert veredito == "SEM_COTA"
    assert "saída da parte 2" in saida
    assert familias == {"codex"}


def test_paralelo_ordena_saidas_reprovadas_por_parte(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relatório REPROVADO concatena partes reprovadas na ordem original."""
    _git(repo, "config", "revisor.paralelo", "3")
    texto = _texto_quatro_partes(monkeypatch)
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, _cwd: _veredito_da_parte(
            prompt, {2: "REPROVADO", 4: "REPROVADO"}))

    veredito, saida, familias = mod.revisar_por_partes(
        texto, str(repo), "commits")

    assert veredito == "REPROVADO"
    assert saida == "\n\n".join((
        "saída da parte 2\nVEREDITO: REPROVADO\n"
        "--- codex-ask: modo=review ---",
        "saída da parte 4\nVEREDITO: REPROVADO\n"
        "--- codex-ask: modo=review ---"))
    assert familias == {"codex"}
