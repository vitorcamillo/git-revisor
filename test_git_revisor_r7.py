"""Testes R7: cache de veredito APROVADO por parte do diff."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

LIMITE = 300  # MAX_DIFF_BYTES nos testes: cada bloco ~228 bytes, 2 partes


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Roda git no repositório de teste e devolve o processo."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repositório git temporário com o revisor desligado, como cwd."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _bloco(nome: str, corpo: str) -> str:
    """Bloco de diff de um arquivo, para dividir_em_partes cortar por ele."""
    return f"diff --git a/{nome} b/{nome}\n{corpo}\n"


def _texto(corpo_b: str = "y" * 200) -> str:
    """Texto que divide em 2 partes com MAX_DIFF_BYTES = LIMITE."""
    return _bloco("a.txt", "x" * 200) + _bloco("b.txt", corpo_b)


def _ia(monkeypatch: pytest.MonkeyPatch,
        rodape: str = "--- codex-ask: modo review ---") -> dict[str, Any]:
    """Troca revisar_com_ia por fake controlável e MAX_DIFF_BYTES pequeno.

    `estado["decisao"](prompt)` devolve o veredito; chamadas e prompts
    ficam em `estado["chamadas"]` e `estado["prompts"]`.
    """
    estado: dict[str, Any] = {"chamadas": 0, "prompts": [],
                              "decisao": (lambda _p: "APROVADO")}

    def _fake(prompt: str, _cwd: str) -> tuple[str, str]:
        estado["chamadas"] += 1
        estado["prompts"].append(prompt)
        veredito = estado["decisao"](prompt)
        return veredito, f"- relatório {estado['chamadas']}\n{rodape}"

    monkeypatch.setattr(mod, "revisar_com_ia", _fake)
    monkeypatch.setattr(mod, "MAX_DIFF_BYTES", LIMITE)
    return estado


def _pasta_partes() -> Path:
    """Pasta de cache de partes no repositório de teste."""
    return mod.base_dir() / "partes"


def test_duas_partes_aprovadas_reaproveitam_cache(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2ª chamada idêntica faz 0 chamadas de IA e devolve APROVADO."""
    estado = _ia(monkeypatch)
    texto = _texto()
    veredito1, saida1, familias1 = mod.revisar_por_partes(
        texto, str(repo), "sha feat: x")
    assert veredito1 == "APROVADO"
    assert saida1 == "aprovado em 2 parte(s)"
    assert familias1 == {"codex"}
    assert estado["chamadas"] == 2

    veredito2, saida2, familias2 = mod.revisar_por_partes(
        texto, str(repo), "sha feat: x")
    assert veredito2 == "APROVADO"
    assert saida2 == "aprovado em 2 parte(s) (2 do cache)"
    assert familias2 == {"codex"}
    assert estado["chamadas"] == 2  # nenhuma chamada nova

    arquivos = sorted(_pasta_partes().iterdir())
    assert len(arquivos) == 2
    linhas = arquivos[0].read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1
    dados = json.loads(linhas[0])
    assert set(dados) == {"quando", "resumo", "familia"}
    assert dados["familia"] == "codex"


def test_rodada_de_correcao_revisa_so_a_parte_alterada(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com relatorio_anterior e B alterada, só B vai à IA; A vem do cache."""
    estado = _ia(monkeypatch)
    estado["decisao"] = (lambda p: "APROVADO" if "a.txt" in p else "REPROVADO")
    veredito, _saida, _familias = mod.revisar_por_partes(
        _texto(), str(repo), "sha feat: x")
    assert veredito == "REPROVADO"
    assert estado["chamadas"] == 2

    estado["prompts"].clear()
    veredito, _saida, _familias = mod.revisar_por_partes(
        _texto(corpo_b="z" * 200), str(repo), "sha feat: x",
        relatorio_anterior="relatório anterior")
    assert veredito == "REPROVADO"
    assert estado["chamadas"] == 3  # só a parte B foi ao revisor
    assert "a.txt" not in estado["prompts"][-1]
    assert "b.txt" in estado["prompts"][-1]
    assert len(list(_pasta_partes().iterdir())) == 1  # só A foi gravada


def test_reprovado_nunca_e_gravado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REPROVADO em toda parte: cache vazio e IA chamada de novo."""
    estado = _ia(monkeypatch)
    estado["decisao"] = (lambda _p: "REPROVADO")
    for _ in range(2):
        veredito, _saida, _familias = mod.revisar_por_partes(
            _texto(), str(repo), "sha feat: x")
        assert veredito == "REPROVADO"
    assert estado["chamadas"] == 4
    pasta = _pasta_partes()
    assert not pasta.exists() or not list(pasta.iterdir())


@pytest.mark.parametrize("rodape,familia", [
    ("--- zcode-worker: modo=review GLM-5.3 ---", "glm"),
    ("--- muse-ask: modo=review muse-max ---", "muse"),
    ("saída sem rodapé de revisor", "desconhecido"),
])
def test_familia_autorreferida_nao_e_gravada(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        rodape: str, familia: str) -> None:
    """glm/muse/desconhecido não gravam cache: IA é chamada de novo."""
    estado = _ia(monkeypatch, rodape=rodape)
    assert mod.familia_revisor(rodape) == familia
    for _ in range(2):
        veredito, _saida, _familias = mod.revisar_por_partes(
            _texto(), str(repo), "sha feat: x")
        assert veredito == "APROVADO"
    assert estado["chamadas"] == 4
    pasta = _pasta_partes()
    assert not pasta.exists() or not list(pasta.iterdir())


def test_cache_parte_desligado_chama_ia_de_novo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """revisor.cacheParte false: mesma chamada 2 vezes vai à IA 2 vezes."""
    _git(repo, "config", "revisor.cacheParte", "false")
    estado = _ia(monkeypatch)
    texto = _texto()
    for _ in range(2):
        veredito, _saida, _familias = mod.revisar_por_partes(
            texto, str(repo), "sha feat: x")
        assert veredito == "APROVADO"
    assert estado["chamadas"] == 4


def test_cache_com_json_invalido_e_ignorado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Arquivo de cache corrompido vale como sem cache: só essa parte à IA."""
    estado = _ia(monkeypatch)
    texto = _texto()
    assert mod.revisar_por_partes(texto, str(repo), "sha feat: x")[0] == "APROVADO"
    arquivos = sorted(_pasta_partes().iterdir())
    assert len(arquivos) == 2
    arquivos[0].write_text("{ json quebrado", encoding="utf-8")
    veredito, saida, _familias = mod.revisar_por_partes(
        texto, str(repo), "sha feat: x")
    assert veredito == "APROVADO"
    assert saida == "aprovado em 2 parte(s) (1 do cache)"
    assert estado["chamadas"] == 3


def test_inconclusivo_em_parte_sem_cache_vale_para_o_todo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """INCONCLUSIVO na parte não cacheada vence mesmo com a outra em cache."""
    estado = _ia(monkeypatch)
    estado["decisao"] = (lambda p: "APROVADO" if "a.txt" in p else "REPROVADO")
    veredito, _saida, _familias = mod.revisar_por_partes(
        _texto(), str(repo), "sha feat: x")
    assert veredito == "REPROVADO"
    assert estado["chamadas"] == 2  # A aprovada e gravada; B reprovada

    estado["decisao"] = (lambda _p: "INCONCLUSIVO")
    veredito, _saida, familias = mod.revisar_por_partes(
        _texto(corpo_b="z" * 200), str(repo), "sha feat: x",
        relatorio_anterior="relatório anterior")
    assert veredito == "INCONCLUSIVO"
    assert estado["chamadas"] == 3  # só a parte B foi ao revisor
    assert "a.txt" not in estado["prompts"][-1]
    assert familias == {"codex"}
