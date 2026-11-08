"""Testes R9: convergência do push gate em lotes grandes.

Prompts de revisão por partes avisam que são só uma parte, e correção
vazia/ausente não manda o usuário empurrar de novo. A regra de intenção
original da R9 (divergência mensagem↔diff BAIXA) foi substituída pela
R12: divergência é MEDIA, com errata (`.revisor/errata.md`) como caminho
de resolução — os testes da regra vivem em test_git_revisor_r12.py e
aqui afirmam a regra nova.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ------------------------------------------------------------ montar_prompt

def test_montar_prompt_intencao_divergencia_e_media() -> None:
    """Regra de intenção (R12): divergência é MEDIA, com errata."""
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao="feat: faz algo")
    assert "é problema MEDIA" in prompt
    assert "problema BAIXA" not in prompt
    assert "não reprove por isso" not in prompt
    assert "mensagem de um commit já feito não é corrigível" not in prompt
    assert "errata" in prompt
    assert "nunca justifica bug" in prompt
    assert "Mensagem vaga não é problema" in prompt


def test_montar_prompt_intencao_seguranca_continua_alta() -> None:
    """Exceção de segurança segue ALTA na regra de divergência."""
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao="feat: faz algo")
    assert "envolver segurança" in prompt
    assert "ALTA" in prompt


def test_montar_prompt_multipartes_frase_condicional() -> None:
    """Frase de 'só uma parte do diff' entra só com multipartes=True."""
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao="feat: faz algo",
                               multipartes=True)
    assert "só uma parte do diff" in prompt
    assert "INTENCAO: NAO_VERIFICAVEL" in prompt
    sem = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                            intencao="feat: faz algo", multipartes=False)
    assert "só uma parte do diff" not in sem
    # sem intenção, a frase multipartes também não entra
    sem_intencao = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                                     multipartes=True)
    assert "só uma parte do diff" not in sem_intencao


# ------------------------------------------------------- revisar_por_partes

@pytest.fixture()
def revisao_isolada(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Isola revisar_por_partes: sem cache, sequencial, captura prompts."""
    prompts: list[str] = []
    monkeypatch.setattr(mod, "MAX_DIFF_BYTES", 500)
    monkeypatch.setattr(mod, "cache_parte_ativo", lambda topo=None: False)
    monkeypatch.setattr(mod, "cfg", lambda chave, padrao: "1")
    monkeypatch.setattr(mod, "familia_revisor", lambda saida: "desconhecido")

    def fake_revisar(prompt: str, cwd: str) -> tuple[str, str]:
        prompts.append(prompt)
        return "APROVADO", "VEREDITO: APROVADO"

    monkeypatch.setattr(mod, "revisar_com_ia", fake_revisar)
    return prompts


def test_revisar_por_partes_multipartes_no_prompt(
        revisao_isolada: list[str]) -> None:
    """Diff que vira 2 partes: cada prompt traz a frase de multipartes."""
    texto = ("commit " + "a" * 450 + "\n") + ("commit " + "b" * 450 + "\n")
    veredito, _, _ = mod.revisar_por_partes(
        texto, "repo", "abc1234 t", intencao="feat: faz algo")
    assert veredito == "APROVADO"
    assert len(revisao_isolada) == 2
    for prompt in revisao_isolada:
        assert "PARTE " in prompt
        assert "só uma parte do diff" in prompt


def test_revisar_por_partes_uma_parte_sem_multipartes(
        revisao_isolada: list[str]) -> None:
    """Diff que cabe numa parte: prompt sem a frase de multipartes."""
    veredito, _, _ = mod.revisar_por_partes(
        "commit " + "a" * 100 + "\n", "repo", "abc1234 t",
        intencao="feat: faz algo")
    assert veredito == "APROVADO"
    assert len(revisao_isolada) == 1
    assert "só uma parte do diff" not in revisao_isolada[0]


# ----------------------------------------------------- mensagem_pos_correcao

class _FakeProcesso:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_mensagem_pos_correcao_com_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """Com ref de tentativa, mantém 'Correção pendente' e novo push."""
    monkeypatch.setattr(mod, "git",
                        lambda args, cwd=None: _FakeProcesso(0))
    titulo, corpo = mod.mensagem_pos_correcao("a" * 40, "/repo")
    assert titulo == "Correção pendente"
    assert mod.ref_tentativa("a" * 40) in corpo
    assert "rode git push de novo" in corpo


def test_mensagem_pos_correcao_sem_ref(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem ref de tentativa, não manda empurrar de novo."""
    monkeypatch.setattr(mod, "git",
                        lambda args, cwd=None: _FakeProcesso(1))
    titulo, corpo = mod.mensagem_pos_correcao("a" * 40, "/repo")
    assert titulo == "Reprovado sem correção"
    assert mod.sha7("a" * 40) in corpo
    assert "git push" not in corpo
    assert "não há correção guardada" in corpo
