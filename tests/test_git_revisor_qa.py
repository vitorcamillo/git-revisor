"""Testes da etapa de QA cliente (`git revisor qa`).

Seguem o estilo de `tests/test_git_revisor_aceite.py` (importar o módulo
como `mod`, fixture `repo` e helper `_commit`). Sem rede e sem IA: os
comandos de QA/verificador são stubs de shell via env.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_ARQUIVO = Path(__file__).parent.parent / "git_revisor.py"
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


def _liga_qa(repo: Path) -> str:
    """Liga o QA e commita o roteiro; devolve o sha da ponta."""
    _git(repo, "config", "revisor.qa", "true")
    return _commit(repo, {".revisor/qa.md": "# Roteiro\n- abrir /\n"},
                   "feat: roteiro do QA\n\nAceite:\n1. home abre\n")


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


def _stub(caminho: Path, corpo: str) -> str:
    """Escreve um stub de shell executável e devolve o caminho."""
    caminho.write_text("#!/bin/sh\n" + corpo, encoding="utf-8")
    caminho.chmod(caminho.stat().st_mode | stat.S_IXUSR)
    return str(caminho)


@pytest.fixture()
def sem_espera(monkeypatch: pytest.MonkeyPatch) -> None:
    """Espera do QA zerada: sem dormir de verdade nos testes."""
    monkeypatch.setenv("REVISOR_QA_ESPERA", "0")
    monkeypatch.setenv("REVISOR_QA_INTERVALO", "0")


def _base_dir() -> Path:
    """Pasta de estado do revisor no repositório atual (cwd do teste)."""
    return mod.base_dir()


def _log() -> str:
    arq = _base_dir() / "log.txt"
    return arq.read_text(encoding="utf-8") if arq.exists() else ""


# ------------------------------------------------- critério 1 (qa_ligado)

def test_desligado_sem_config(repo: Path) -> None:
    """Sem `revisor.qa`, mesmo com roteiro, nada de QA é disparado."""
    sha = _commit(repo, {".revisor/qa.md": "# Roteiro\n"}, "feat: x")
    assert mod.qa_ligado(sha) is False


def test_desligado_sem_arquivo(repo: Path) -> None:
    """Com config mas sem `.revisor/qa.md` no commit, QA desligado."""
    _git(repo, "config", "revisor.qa", "true")
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    assert mod.qa_ligado(sha) is False


def test_desligado_por_env(repo: Path) -> None:
    """`REVISOR_QA=0` desliga mesmo com config e roteiro."""
    sha = _liga_qa(repo)
    assert mod.qa_ligado(sha) is True
    os.environ["REVISOR_QA"] = "0"
    try:
        assert mod.qa_ligado(sha) is False
    finally:
        del os.environ["REVISOR_QA"]


# ------------------------------------------------- critério 3 (baixo risco)

def test_baixo_risco_nao_dispara(repo: Path) -> None:
    """Push só de docs não dispara QA e loga o pulo."""
    base = _liga_qa(repo)
    sha = _commit(repo, {"docs/guia.md": "# guia\n"}, "docs: guia")
    mod.disparar_qa([sha], sha, str(repo), base, "origin",
                    "refs/heads/main")
    assert not (_base_dir() / "qa-fundo.json").exists()
    assert "qa: pulado (baixo risco)" in _log()


# ------------------------------------------------- critério 4 (uma execução)

def test_mesmo_sha_vivo_nao_duplica(repo: Path,
                                    tmp_path: Path) -> None:
    """Mesmo sha com pid vivo não dispara de novo."""
    base = _liga_qa(repo)
    sha = _commit(repo, {"app.py": "print(1)\n"}, "feat: app")
    fundo = _base_dir() / "qa-fundo.json"
    fundo.parent.mkdir(parents=True, exist_ok=True)
    registro = {"sha": sha, "novos": [sha], "base": base,
                "remote": "origin", "remote_ref": "refs/heads/main",
                "rodada": 0, "token": "t", "pid": os.getpid()}
    fundo.write_text(json.dumps(registro) + "\n", encoding="utf-8")
    antes = fundo.read_text(encoding="utf-8")
    mod.disparar_qa([sha], sha, str(repo), base, "origin",
                    "refs/heads/main")
    assert fundo.read_text(encoding="utf-8") == antes
    assert "disparado" not in _log()


# --------------------------------- critério 5 (pronto falhando → PULADO)

def test_pronto_falhando_pulado_sem_chamar_stub(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """qaPronto falhando gera `QA: PULADO` sem chamar o comando do QA."""
    sha = _liga_qa(repo)
    _git(repo, "config", "revisor.qaPronto", "exit 1")
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\nprintf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not marcador.exists()
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: PULADO" in relatorio


# --------------------------- critério 6 (prompt, cwd fora do repositório)

def test_prompt_tem_partes_fixas(repo: Path) -> None:
    """Prompt monta proibições, roteiro, armadilhas e aceite."""
    prompt = mod.montar_prompt_qa("ROTEIRO-PROJETO", "ARMADILHA-X",
                                  "1. [abc1234] home abre", "deadbeef")
    for trecho in ("nunca pagar", "CAPTCHA", "localhost",
                   "375 px", "curl", "Playwright",
                   "ROTEIRO-PROJETO", "ARMADILHA-X",
                   "1. [abc1234] home abre", "deadbeef"):
        assert trecho in prompt


def test_qa_roda_em_diretorio_temporario(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """O comando do QA roda fora do repositório (cwd temporário vazio)."""
    sha = _liga_qa(repo)
    pwd_arq = tmp_path / "cwd.txt"
    qa = _stub(tmp_path / "qa.sh",
               f'[ "$1" = --cwd ] && echo "$2" > "{pwd_arq}"\n'
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    cwd_usado = pwd_arq.read_text(encoding="utf-8").strip()
    assert cwd_usado != str(repo)
    assert Path(cwd_usado).is_absolute()


# -------------------------------------------- critério 7 (parser do QA)

def test_achado_sem_prova_descartado() -> None:
    """Achado sem `PROVA:` não entra no veredito."""
    saida = ("- [ALTA] sem prova — problema — corrija\n"
             "- [BAIXA] com prova — problema — corrija\n"
             "PROVA: curl http://localhost:9/ => recusado\n"
             "QA: APROVADO\n")
    interp = mod.interpretar_qa(saida)
    assert interp["achados"] == [
        {"severidade": "BAIXA", "titulo": "com prova — problema — corrija",
         "prova": "curl http://localhost:9/ => recusado"}]


def test_sem_linha_qa_indisponivel() -> None:
    """Saída sem linha `QA:` é INDISPONIVEL, nunca aprovação."""
    interp = mod.interpretar_qa("- nenhum problema relevante\nACEITE: 1/1\n")
    assert interp["veredito"] == "INDISPONIVEL"
    assert interp["aceite"] == "1/1"


# --------------------------------- critério 8 (verificador independente)

def test_nao_confirmado_vai_para_armadilhas(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Achado não confirmado sai do veredito e entra nas armadilhas."""
    sha = _liga_qa(repo)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" '
               '"- [ALTA] falso alarme — nada — ignore" '
               '"PROVA: curl http://localhost:9/ => 200" '
               '"ACEITE: 1/1" "QA: REPROVADO"\n')
    ver = _stub(tmp_path / "ver.sh",
                'printf "%s\\n" "- [99] CONFIRMADO: outro" '
                '"- [1] NAO_REPRODUZIDO: não vi"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    monkeypatch.setenv("REVISOR_CMD_QA_VERIFICAR", ver)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: APROVADO" in relatorio
    armadilhas = (_base_dir() / "qa" / "armadilhas.md").read_text(
        encoding="utf-8")
    assert "falso alarme" in armadilhas


def test_verificador_indisponivel(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Verificador que falha deixa os achados NAO_VERIFICADO."""
    sha = _liga_qa(repo)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "- [ALTA] bug — ruim — corrija" '
               '"PROVA: curl http://localhost:9/ => 500" '
               '"QA: REPROVADO"\n')
    ver = _stub(tmp_path / "ver.sh", "exit 1\n")
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    monkeypatch.setenv("REVISOR_CMD_QA_VERIFICAR", ver)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: INDISPONIVEL" in relatorio
    assert "NAO_VERIFICADO" in relatorio


# --------------------------------- critério 9 (veredito, notificar, baixa)

def _cenarios_confirmados(tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs: QA com 1 MEDIA + 1 BAIXA, verificador confirma os dois."""
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "- [MEDIA] lento — demora — otimize" '
               '"PROVA: curl http://localhost:9/ => 9s" '
               '"- [BAIXA] texto — erro pequeno — ajuste" '
               '"PROVA: curl http://localhost:9/ => typo" '
               '"ACEITE: 1/1" "QA: REPROVADO"\n')
    ver = _stub(tmp_path / "ver.sh",
                'printf "%s\\n" "- [1] CONFIRMADO: lento mesmo" '
                '"- [2] CONFIRMADO: typo visível"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    monkeypatch.setenv("REVISOR_CMD_QA_VERIFICAR", ver)


def test_reprovado_notifica_e_baixa_vai_para_fim_de_dia(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Confirmado MEDIA reprova e notifica; BAIXA vai para baixa.md."""
    sha = _liga_qa(repo)
    _cenarios_confirmados(tmp_path, monkeypatch)
    antes = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert mod.cmd_qa(sha) == 0
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == antes
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: REPROVADO" in relatorio
    assert "CONFIRMADO" in relatorio
    assert "QA cliente:" in _log()
    baixa = (_base_dir() / "qa" / "baixa.md").read_text(encoding="utf-8")
    assert "texto" in baixa


def test_so_baixa_aprova_sem_notificar(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Só BAIXA confirmada aprova, grava baixa.md e não notifica."""
    sha = _liga_qa(repo)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "- [BAIXA] texto — erro pequeno — ajuste" '
               '"PROVA: curl http://localhost:9/ => typo" '
               '"QA: REPROVADO"\n')
    ver = _stub(tmp_path / "ver.sh",
                'printf "%s\\n" "- [1] CONFIRMADO: typo visível"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    monkeypatch.setenv("REVISOR_CMD_QA_VERIFICAR", ver)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: APROVADO" in relatorio
    baixa = (_base_dir() / "qa" / "baixa.md").read_text(encoding="utf-8")
    assert "texto" in baixa
    assert "QA cliente:" not in _log()


# ------------------------------------------------- critério 10 (status)

def test_status_mostra_qa_quando_ligado(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None, capsys: pytest.CaptureFixture) -> None:
    """Status mostra a linha do QA quando ele está ligado."""
    sha = _liga_qa(repo)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert mod.cmd_status(None) == 0
    saida = capsys.readouterr().out
    assert "QA cliente" in saida
    assert f"qa: APROVADO {sha[:7]}" in saida


def test_status_omite_qa_quando_desligado(
        repo: Path, capsys: pytest.CaptureFixture) -> None:
    """Sem QA ligado, o status não mostra seção de QA."""
    _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    assert mod.cmd_status(None) == 0
    assert "QA cliente" not in capsys.readouterr().out


# --------------------------------- critério 11 (correção automática opt-in)

def test_sem_opcao_nao_corrige(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Sem `revisor.qaCorrigir`, REPROVADO só relata (HEAD não anda)."""
    sha = _liga_qa(repo)
    _cenarios_confirmados(tmp_path, monkeypatch)
    antes = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert mod.cmd_qa(sha) == 0
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == antes
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: REPROVADO" in relatorio


def test_no_limite_de_rodadas_nao_corrige(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """No limite de rodadas, só notifica (HEAD não anda)."""
    sha = _liga_qa(repo)
    _git(repo, "config", "revisor.qaCorrigir", "true")
    _cenarios_confirmados(tmp_path, monkeypatch)
    monkeypatch.setenv("REVISOR_QA_RODADAS", "1")
    fundo = _base_dir() / "qa-fundo.json"
    fundo.parent.mkdir(parents=True, exist_ok=True)
    fundo.write_text(json.dumps({"sha": sha, "novos": [sha],
                                 "rodada": 1}) + "\n", encoding="utf-8")
    antes = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert mod.cmd_qa(sha) == 0
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == antes
    assert "limite de rodadas" in _log()
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: REPROVADO" in relatorio
