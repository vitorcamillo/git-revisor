"""Testes da re-revisão ancorada (r14a): delta da correção e retomada sem IA."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).with_name("git_revisor.py")
_spec = importlib.util.spec_from_file_location("git_revisor", _ARQUIVO)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Roda git no repositório de teste."""
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


def _commit(repo: Path, arquivos: dict[str, str], mensagem: str) -> str:
    """Grava e commita arquivos, devolvendo o SHA criado."""
    for relativo, conteudo in arquivos.items():
        caminho = repo / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", mensagem)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Cria um repositório git isolado para cada teste."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q", "-b", "main")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    monkeypatch.chdir(caminho)
    return caminho


def _base(repo: Path, monkeypatch: pytest.MonkeyPatch,
          chamadas: list[str], logs: list[str]) -> None:
    """Mocks comuns: log/notificação capturados e IA sem autorrevisão."""
    monkeypatch.setattr(mod, "log", logs.append)
    monkeypatch.setattr(mod, "notificar", lambda *a, **k: None)
    monkeypatch.setattr(mod, "autorrevisao", lambda *a, **k: False)
    monkeypatch.setattr(mod, "rodadas_max", lambda: 1)
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        lambda *a, **k: chamadas.append("rapido") or (True, ""))
    monkeypatch.setattr(mod, "rodar_checks",
                        lambda *a, **k: chamadas.append("completo") or (True, ""))


# ------------------------------------------------- bloco de rodada no prompt

def test_prompt_rodada_correcao_ancora_relatorio_anterior() -> None:
    """Com relatorio_anterior, o prompt ancora e limita a reprovação."""
    prompt = mod.montar_prompt(
        "diff --git a/app.py b/app.py\n+x = 1\n", "repo", "abc1234 feat",
        relatorio_anterior="- [ALTA] app.py:1 — bug — corrigir\n"
                           "VEREDITO: REPROVADO")
    assert "RODADA DE CORREÇÃO" in prompt
    assert "vai como BAIXA" in prompt
    assert "problema ALTA em qualquer lugar" in prompt
    assert "não tocou" in prompt


def test_prompt_sem_relatorio_anterior_sem_bloco() -> None:
    """Sem relatorio_anterior, nenhum bloco de rodada de correção."""
    prompt = mod.montar_prompt(
        "diff --git a/app.py b/app.py\n+x = 1\n", "repo", "abc1234 feat")
    assert "RODADA DE CORREÇÃO" not in prompt


# ------------------------------------------------------------------ delta

def test_prompt_delta_aparece_e_corta_em_max_delta() -> None:
    """Delta entra rotulado e é cortado em MAX_DELTA bytes."""
    delta = "x\n" * 20_000  # 40_000 bytes > MAX_DELTA
    assert len(delta.encode("utf-8")) > mod.MAX_DELTA
    prompt = mod.montar_prompt("diff pequeno\n", "repo", "abc1234 feat",
                               delta_correcao=delta)
    assert "Delta da correção (o que mudou desde a versão reprovada" in prompt
    cortado = delta.encode("utf-8")[:mod.MAX_DELTA].decode("utf-8", "ignore")
    assert cortado in prompt
    assert delta not in prompt


def test_prompt_delta_enorme_estourado_sai_sem_delta() -> None:
    """Delta que estoura o teto sai sem delta, com aviso, sem erro."""
    diff_grande = "d\n" * 55_000  # ~110 KiB: cabe sem delta, não com delta
    delta = "y\n" * 20_000  # cortado em 12_000 bytes, ainda estoura o teto
    prompt = mod.montar_prompt(diff_grande, "repo", "abc1234 feat",
                               delta_correcao=delta)
    assert "delta omitido por tamanho" in prompt
    assert "Delta da correção (o que mudou desde a versão reprovada" \
        not in prompt


def test_prompt_sem_delta_estourado_ainda_levanta_erro() -> None:
    """Sem delta (ou com delta já omitido), o estouro continua sendo erro."""
    diff_grande = "d\n" * 70_000  # ~140 KiB > MAX_PROMPT_BYTES
    with pytest.raises(RuntimeError):
        mod.montar_prompt(diff_grande, "repo", "abc1234 feat")
    with pytest.raises(RuntimeError):
        mod.montar_prompt(diff_grande, "repo", "abc1234 feat",
                          delta_correcao="y\n" * 20_000)


# ------------------------------------------------- delta no laço de correção

def test_fluxo_laco_envia_delta_entre_corrigido_e_correcao(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O prompt da IA no laço recebe diff_entre(sha_atual, novo)."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    prompts: list[str] = []

    def _ia(prompt: str, _cwd: str) -> tuple[str, str]:
        prompts.append(prompt)
        return ("APROVADO", "- nenhum problema relevante\n"
                "INTENCAO: CUMPRIDA\nVEREDITO: APROVADO")

    monkeypatch.setattr(mod, "revisar_com_ia", _ia)
    monkeypatch.setattr(mod, "integrar_correcao",
                        lambda *a, **k: 0)
    pares: list[tuple[str, str]] = []

    def _tentativa(_original: str, atual: str, _relatorio: str,
                   _topo: str) -> tuple[str, str]:
        novo = _commit(repo, {"app.py": "x = 2\n"}, "fix: correcao")
        pares.append((atual, novo))
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 0
    assert pares and prompts
    esperado = mod.diff_entre(pares[0][0], pares[0][1])
    assert pares[0][0] == sha
    assert any("Delta da correção" in p and esperado in p for p in prompts)


# ------------------------- retomada re-revisa ancorada no relatório e no delta

def test_retomada_reprovada_rerevisa_ancorada_antes_do_worker(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retomada re-revisa (aprovar poupa worker) com relatório salvo e delta."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    ordem: list[str] = []
    prompts: list[str] = []

    def _ia(prompt: str, _cwd: str) -> tuple[str, str]:
        ordem.append("IA")
        prompts.append(prompt)
        return ("REPROVADO", "achado novo\nVEREDITO: REPROVADO")

    monkeypatch.setattr(mod, "revisar_com_ia", _ia)
    relatorios_worker: list[str] = []

    def _tentativa(_original: str, atual: str, relatorio: str,
                   _topo: str) -> tuple[str, str]:
        relatorios_worker.append(relatorio)
        ordem.append("tentativa")
        novo = _commit(repo, {"app.py": "x = 3\n"}, "fix: correcao nova")
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    anterior = _commit(repo, {"app.py": "x = 2\n"}, "fix: correcao guardada")
    assert mod.guardar_tentativa(sha, anterior, str(repo))
    salvo = "- [ALTA] app.py:1 — bug — corrigir\nVEREDITO: REPROVADO"
    mod.salvar_relatorio(anterior, salvo)
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    assert ordem[0] == "IA"
    assert "RODADA DE CORREÇÃO" in prompts[0] and "bug — corrigir" in prompts[0]
    assert "Delta da correção" in prompts[0] and "+x = 2" in prompts[0]
    assert relatorios_worker and relatorios_worker[0].startswith("achado novo")


def test_retomada_inconclusiva_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tentativa inconclusiva com relatório vai à IA como hoje."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    ordem: list[str] = []

    def _ia(prompt: str, _cwd: str) -> tuple[str, str]:
        ordem.append("IA")
        return ("INCONCLUSIVO", "sem veredito")

    monkeypatch.setattr(mod, "revisar_com_ia", _ia)
    monkeypatch.setattr(mod, "tentativa_correcao",
                        lambda *a, **k: (_commit(
                            repo, {"x.txt": "1\n"}, "fix: x"), "codex"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    anterior = _commit(repo, {"app.py": "x = 2\n"}, "fix: correcao guardada")
    assert mod.guardar_tentativa(sha, anterior, str(repo))
    mod.salvar_relatorio(anterior, "relatório antigo\nVEREDITO: REPROVADO")
    mod.marcar_inconclusivo(anterior, "SEM_COTA")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    assert "IA" in ordem


def test_retomada_sem_relatorio_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tentativa sem relatório salvo vai à IA como hoje."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    ordem: list[str] = []

    def _ia(prompt: str, _cwd: str) -> tuple[str, str]:
        ordem.append("IA")
        return ("INCONCLUSIVO", "sem veredito")

    monkeypatch.setattr(mod, "revisar_com_ia", _ia)
    monkeypatch.setattr(mod, "tentativa_correcao",
                        lambda *a, **k: (_commit(
                            repo, {"x.txt": "1\n"}, "fix: x"), "codex"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    anterior = _commit(repo, {"app.py": "x = 2\n"}, "fix: correcao guardada")
    assert mod.guardar_tentativa(sha, anterior, str(repo))
    assert mod.ler_relatorio(anterior) is None
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    assert "IA" in ordem
