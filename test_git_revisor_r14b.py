"""Testes R14b: memória de cota dos workers e mudança grande.

Cobre funções já implementadas no `git_revisor.py`, sem rede nem
wrappers reais: `reset_de_cota`, `rodar_worker` (pulo por prazo futuro,
gravação do prazo no exit 75, limpeza no exit 0), `max_partes`,
`remover_baixo_risco` e `revisar_por_partes` (filtro de baixo risco
antes de declarar GRANDE_DEMAIS).
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ---------------------------------------------------------- reset_de_cota

def test_reset_de_cota() -> None:
    """Sem linha → None; uma linha → epoch; duas linhas → a última."""
    assert mod.reset_de_cota("tudo ok, sem marcador") is None
    assert mod.reset_de_cota("x\nworker-ask-quota-reset: 12345\ny") == 12345
    texto = "worker-ask-quota-reset: 111\nmeio\nworker-ask-quota-reset: 222\n"
    assert mod.reset_de_cota(texto) == 222


# ------------------------------------------------------------ rodar_worker

def _primeiro_cmd() -> str:
    """cmd[0] do 1º degrau com o ambiente atual (para reconhecer o degrau)."""
    return mod.comando("REVISOR_CMD_WORKER",
                       "zcode-worker --model GLM-5.3 --effort max")[0]


def test_rodar_worker_pula_degrau_sem_cota(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 75 com reset futuro (+1 h) grava o prazo; nova chamada pula o degrau."""
    reset = int(time.time()) + 3600
    chamadas: list[str] = []
    glm_cmd: list[str] = []

    def falso_chamar(cmd: list[str], cwd: str, modo_args: list[str],
                     timeout: int,
                     stderr_out: list[str] | None = None) -> tuple[int, str]:
        chamadas.append(cmd[0])
        if not glm_cmd:
            glm_cmd.append(cmd[0])
        if cmd[0] == glm_cmd[0]:
            if stderr_out is not None:
                stderr_out.append(
                    f"busy\nworker-ask-quota-reset: {reset}\n")
            return 75, ""
        return 0, "ok"

    monkeypatch.setattr(mod, "chamar_comando", falso_chamar)
    familia = mod.rodar_worker("tarefa", "/tmp")
    assert familia == "muse"
    assert mod.sem_cota_ate("glm") == reset

    chamadas.clear()
    familia2 = mod.rodar_worker("tarefa", "/tmp")
    assert familia2 == "muse"
    assert glm_cmd and chamadas
    assert glm_cmd[0] not in chamadas


def test_rodar_worker_exit75_sem_reset_usa_padrao(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 75 sem reset no stderr → prazo ≈ agora + 600 s."""
    def falso_chamar(cmd: list[str], cwd: str, modo_args: list[str],
                     timeout: int,
                     stderr_out: list[str] | None = None) -> tuple[int, str]:
        if stderr_out is not None:
            stderr_out.append("busy, sem linha de reset")
        return 75, ""

    monkeypatch.setattr(mod, "chamar_comando", falso_chamar)
    antes = time.time()
    mod.rodar_worker("tarefa", "/tmp")
    prazo = mod.sem_cota_ate("glm")
    assert prazo is not None
    assert abs(prazo - (int(antes) + mod.COTA_ESPERA_PADRAO_S)) <= 5


def test_rodar_worker_exit75_reset_distante_usa_padrao(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset a +48 h (além de 24 h) é ignorado → prazo ≈ agora + 600 s."""
    reset_longe = int(time.time()) + 48 * 3600

    def falso_chamar(cmd: list[str], cwd: str, modo_args: list[str],
                     timeout: int,
                     stderr_out: list[str] | None = None) -> tuple[int, str]:
        if stderr_out is not None:
            stderr_out.append(f"worker-ask-quota-reset: {reset_longe}\n")
        return 75, ""

    monkeypatch.setattr(mod, "chamar_comando", falso_chamar)
    antes = time.time()
    mod.rodar_worker("tarefa", "/tmp")
    prazo = mod.sem_cota_ate("glm")
    assert prazo is not None
    assert abs(prazo - (int(antes) + mod.COTA_ESPERA_PADRAO_S)) <= 5


def test_rodar_worker_prazo_vencido_volta_a_chamar_e_apaga(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Prazo vencido não pula o degrau; exit 0 apaga o registro."""
    mod.marcar_sem_cota("glm", int(time.time()) - 10)
    assert mod.sem_cota_ate("glm") is not None
    chamadas: list[str] = []

    def falso_chamar(cmd: list[str], cwd: str, modo_args: list[str],
                     timeout: int,
                     stderr_out: list[str] | None = None) -> tuple[int, str]:
        chamadas.append(cmd[0])
        return 0, "ok"

    monkeypatch.setattr(mod, "chamar_comando", falso_chamar)
    familia = mod.rodar_worker("tarefa", "/tmp")
    assert chamadas and chamadas[0] == _primeiro_cmd()
    assert familia in ("glm", "muse", "codex", "claude")
    assert mod.sem_cota_ate("glm") is None


def test_rodar_worker_json_corrompido(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON de cota corrompido não quebra: trata como sem registro."""
    caminho = mod.arquivo_cota_workers()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("{invalido", encoding="utf-8")
    chamadas: list[str] = []

    def falso_chamar(cmd: list[str], cwd: str, modo_args: list[str],
                     timeout: int,
                     stderr_out: list[str] | None = None) -> tuple[int, str]:
        chamadas.append(cmd[0])
        return 0, "ok"

    monkeypatch.setattr(mod, "chamar_comando", falso_chamar)
    familia = mod.rodar_worker("tarefa", "/tmp")
    assert chamadas and chamadas[0] == _primeiro_cmd()
    assert familia is not None


# -------------------------------------------------------------- max_partes

@pytest.mark.parametrize(("valor", "esperado"), [
    (None, "padrao"),
    ("8", 8),
    ("50", 12),
    ("0", 1),
    ("x", "padrao"),
])
def test_max_partes(monkeypatch: pytest.MonkeyPatch, valor: str | None,
                    esperado: object) -> None:
    """Limites de revisor.maxPartes: padrão, 8, teto 12, piso 1, inválido."""
    if valor is None:
        monkeypatch.setattr(mod, "cfg",
                            lambda chave, padrao: padrao)
    else:
        monkeypatch.setattr(mod, "cfg",
                            lambda chave, padrao, v=valor: v)
    obtido = mod.max_partes()
    if esperado == "padrao":
        assert obtido == mod.MAX_PARTES
    else:
        assert obtido == esperado


# ------------------------------------------------------ remover_baixo_risco

def _bloco(caminho: str, corpo: str = "x" * 350) -> str:
    return (f"diff --git a/{caminho} b/{caminho}\n"
            f"--- a/{caminho}\n+++ b/{caminho}\n"
            f"@@ -1 +1 @@\n+{corpo}\n")


def test_remover_baixo_risco(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove docs/x.md e package-lock.json; mantém código e alto risco."""
    monkeypatch.setattr(mod, "cfg_todos", lambda chave: [])
    texto = "".join([
        _bloco("docs/x.md"),
        _bloco("package-lock.json"),
        _bloco("src/a.py"),
        _bloco("docs/auth.md"),
    ])
    filtrado, removidos = mod.remover_baixo_risco(texto)
    assert removidos == ["docs/x.md", "package-lock.json"]
    assert "src/a.py" in filtrado
    assert "docs/auth.md" in filtrado
    assert "docs/x.md" not in filtrado
    assert "package-lock.json" not in filtrado


# -------------------------------------------------------- revisar_por_partes

@pytest.fixture()
def revisao_cota(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Isola revisar_por_partes: 500 B por parte, sem cache, sequencial."""
    prompts: list[str] = []
    monkeypatch.setattr(mod, "MAX_DIFF_BYTES", 500)
    monkeypatch.setattr(mod, "cache_parte_ativo", lambda topo=None: False)
    monkeypatch.setattr(mod, "cfg_todos", lambda chave: [])
    monkeypatch.setattr(
        mod, "cfg",
        lambda chave, padrao: ("2" if chave == "maxPartes"
                               else ("1" if chave == "paralelo" else padrao)))
    monkeypatch.setattr(mod, "familia_revisor", lambda saida: "desconhecido")

    def fake_revisar(prompt: str, cwd: str) -> tuple[str, str]:
        prompts.append(prompt)
        return "APROVADO", "VEREDITO: APROVADO"

    monkeypatch.setattr(mod, "revisar_com_ia", fake_revisar)
    return prompts


def test_revisar_por_partes_filtro_cabe(revisao_cota: list[str]) -> None:
    """Acima do limite mas cabendo após o filtro: revisa e avisa no prompt."""
    texto = (_bloco("docs/a.md") + _bloco("package-lock.json")
             + _bloco("src/codigo.py"))
    veredito, _, _ = mod.revisar_por_partes(texto, "repo", "commits")
    assert veredito == "APROVADO"
    assert revisao_cota
    assert any("Arquivos de baixo risco omitidos" in p for p in revisao_cota)


def test_revisar_por_partes_so_codigo_grande_demais(
        revisao_cota: list[str]) -> None:
    """Só código acima do limite: GRANDE_DEMAIS sem chamar a IA."""
    texto = _bloco("src/a.py") + _bloco("src/b.py") + _bloco("src/c.py")
    veredito, _, _ = mod.revisar_por_partes(texto, "repo", "commits")
    assert veredito == "GRANDE_DEMAIS"
    assert revisao_cota == []
