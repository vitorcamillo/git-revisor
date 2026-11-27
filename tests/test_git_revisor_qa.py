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
import time
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


@pytest.fixture(autouse=True)
def qa_liberado(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suíte de QA remove o `REVISOR_QA=0` global do conftest."""
    monkeypatch.delenv("REVISOR_QA", raising=False)


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

def test_ligado_por_padrao_sem_config(repo: Path) -> None:
    """Sem `revisor.qa`, o QA já vem ligado (só `false`/env desligam)."""
    sha = _commit(repo, {".revisor/qa.md": "# Roteiro\n"}, "feat: x")
    assert mod.qa_ligado(sha) is True


def test_sem_arquivo_ainda_ligado(repo: Path) -> None:
    """Com config mas sem `.revisor/qa.md`, o QA segue ligado."""
    _git(repo, "config", "revisor.qa", "true")
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    assert mod.qa_ligado(sha) is True


def test_desligado_por_config_false(repo: Path) -> None:
    """`revisor.qa false` desliga mesmo com roteiro versionado."""
    sha = _commit(repo, {".revisor/qa.md": "# Roteiro\n"}, "feat: x")
    _git(repo, "config", "revisor.qa", "false")
    assert mod.qa_ligado(sha) is False


def test_desligado_por_env(repo: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    """`REVISOR_QA=0` desliga mesmo com config e roteiro."""
    sha = _liga_qa(repo)
    assert mod.qa_ligado(sha) is True
    monkeypatch.setenv("REVISOR_QA", "0")
    assert mod.qa_ligado(sha) is False


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
               '"COBERTO: página inicial" '
               '"QA: REPROVADO"\n')
    ver = _stub(tmp_path / "ver.sh",
                'printf "%s\\n" "- [1] CONFIRMADO: typo visível"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    monkeypatch.setenv("REVISOR_CMD_QA_VERIFICAR", ver)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: APROVADO" in relatorio
    assert "COBERTO: página inicial" in relatorio
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
    """Com `revisor.qa false`, o status não mostra seção de QA."""
    _git(repo, "config", "revisor.qa", "false")
    _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    assert mod.cmd_status(None) == 0
    assert "QA cliente" not in capsys.readouterr().out


def test_status_mostra_qa_por_padrao(
        repo: Path, capsys: pytest.CaptureFixture) -> None:
    """Sem config, o status já mostra a seção de QA (ligado por padrão)."""
    _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    assert mod.cmd_status(None) == 0
    saida = capsys.readouterr().out
    assert "QA cliente" in saida
    assert "qa: sem veredito ainda" in saida


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


# ------------------------- critério 1 (subcomando qa com QA desligado)

def test_cmd_qa_desligado_por_config_pula_sem_chamar_stub(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Subcomando `qa` com `revisor.qa false`: PULADO sem chamar o comando."""
    sha = _commit(repo, {".revisor/qa.md": "# Roteiro\n"}, "feat: x")
    _git(repo, "config", "revisor.qa", "false")
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\nprintf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not marcador.exists()
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: PULADO" in relatorio


def test_cmd_qa_desligado_por_env_pula_sem_chamar_stub(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Subcomando `qa` com `REVISOR_QA=0`: PULADO sem chamar o comando."""
    sha = _liga_qa(repo)
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\nprintf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    monkeypatch.setenv("REVISOR_QA", "0")
    assert mod.cmd_qa(sha) == 0
    assert not marcador.exists()
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: PULADO" in relatorio


# --------------------------- critério 2 (push previamente aprovado)

def test_push_previamente_aprovado_agenda_qa(
        repo: Path, sem_espera: None) -> None:
    """Push sem pendências (tudo aprovado) agenda o QA e devolve 0."""
    base = _liga_qa(repo)
    sha = _commit(repo, {"app.py": "print(1)\n"}, "feat: app")
    mod.marcar_aprovado(base, "teste")
    mod.marcar_aprovado(sha, "teste")
    refs = [(sha, base, "refs/heads/main", "refs/heads/main")]
    assert mod.revisar_refs(refs, "origin") == 0
    assert "qa cliente disparado" in _log()


# ---------------- critério 4 (substituição encerra os descendentes)

def test_disparo_substitui_execucao_em_outra_sessao(
        repo: Path, tmp_path: Path, sem_espera: None) -> None:
    """Sha novo encerra supervisor anterior e descendentes dele."""
    base = _liga_qa(repo)
    antigo = _commit(repo, {"app.py": "print(1)\n"}, "feat: antigo")
    novo = _commit(repo, {"app.py": "print(2)\n"}, "feat: novo")
    arq_filho = tmp_path / "filho.pid"
    vitima = subprocess.Popen(
        ["sh", "-c",
         f'sleep 30 & echo $! > "{arq_filho}"; wait'],
        start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        filho: int | None = None
        for _ in range(100):
            if arq_filho.exists():
                texto = arq_filho.read_text(encoding="utf-8").strip()
                if texto:
                    filho = int(texto)
                    break
            time.sleep(0.05)
        assert filho is not None
        fundo = _base_dir() / "qa-fundo.json"
        fundo.parent.mkdir(parents=True, exist_ok=True)
        fundo.write_text(json.dumps(
            {"sha": antigo, "novos": [antigo], "base": base,
             "remote": "origin", "remote_ref": "refs/heads/main",
             "rodada": 0, "token": "t", "pid": vitima.pid}) + "\n",
            encoding="utf-8")
        mod.disparar_qa([novo], novo, str(repo), base, "origin",
                        "refs/heads/main")
        assert not mod._processo_vivo(vitima.pid)
        assert not mod._processo_vivo(filho)
        assert "qa cliente disparado" in _log()
    finally:
        try:
            vitima.kill()
        except OSError:
            pass
        try:
            vitima.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


# --------------------------------- critério 5 (prazo da espera)

def test_espera_zero_nao_aceita_pronto_que_so_libera_depois(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com espera 0, `qaPronto` pronto não libera: o prazo vale já."""
    sha = _liga_qa(repo)
    _git(repo, "config", "revisor.qaPronto", "exit 0")
    monkeypatch.setenv("REVISOR_QA_ESPERA", "0")
    ok, motivo = mod._esperar_qa_liberar(sha, str(repo), None, None)
    assert ok is False
    assert "após 0s" in motivo


def test_pronto_com_limite_zero_nao_roda_comando(
        repo: Path, tmp_path: Path) -> None:
    """`_qa_pronto_ok` com limite 0 nem executa o comando de prontidão."""
    sha = _liga_qa(repo)
    marcador = tmp_path / "pronto-chamado"
    _git(repo, "config", "revisor.qaPronto", f'touch "{marcador}"')
    assert mod._qa_pronto_ok(sha, str(repo), limite=0) is False
    assert not marcador.exists()
    assert mod._remoto_contem("origin", "refs/heads/main", sha,
                              str(repo), limite=0) is False


def test_espera_zero_sem_remoto_nem_pronto_libera(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem remoto nem `qaPronto`, espera 0 libera (nada a esperar)."""
    sha = _liga_qa(repo)
    monkeypatch.setenv("REVISOR_QA_ESPERA", "0")
    ok, _motivo = mod._esperar_qa_liberar(sha, str(repo), None, None)
    assert ok is True


# ------------- critério 11 (cadeia positiva da correção automática)

def test_correcao_positiva_agenda_revalidacao_sem_matar_supervisor(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Correção agenda revalidação (rodada+1) sem encerrar quem agenda."""
    sha = _liga_qa(repo)
    _git(repo, "config", "revisor.qaCorrigir", "true")
    branch = _git(repo, "branch", "--show-current").stdout.strip()
    _git(repo, "config", f"branch.{branch}.remote", "origin")
    _git(repo, "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
    monkeypatch.setenv("REVISOR_QA_RODADA", "0")
    fundo = _base_dir() / "qa-fundo.json"
    fundo.parent.mkdir(parents=True, exist_ok=True)
    fundo.write_text(json.dumps(
        {"sha": sha, "novos": [sha], "rodada": 0, "token": "sup",
         "pid": os.getpid()}) + "\n", encoding="utf-8")
    encerramentos: list[int] = []
    monkeypatch.setattr(
        mod, "_encerrar_qa_anterior",
        lambda pid: encerramentos.append(pid) or True)
    novo: dict[str, str] = {}

    def fluxo_falso(original: str, relatorio: str, topo: str,
                    lote: list[str] | None = None) -> int:
        novo["sha"] = _commit(repo, {"app.py": "print('ok')\n"},
                              "fix: correcao do QA")
        return 0

    monkeypatch.setattr(mod, "fluxo_correcao", fluxo_falso)
    pushes: list[str] = []
    monkeypatch.setattr(mod, "autopush",
                        lambda s, t: pushes.append(s))
    comandos: list[tuple] = []

    class _ProcFalso:
        pid = 424242

    popen_real = subprocess.Popen

    def popen_falso(cmd: list[str], **kwargs) -> _ProcFalso | subprocess.Popen:
        if cmd and cmd[0] == "git":
            return popen_real(cmd, **kwargs)
        comandos.append((cmd, kwargs))
        return _ProcFalso()

    monkeypatch.setattr(mod.subprocess, "Popen", popen_falso)
    mod._qa_corrigir(sha, str(repo), "relatorio do QA", 0)
    assert pushes == [novo["sha"]]
    assert os.getpid() not in encerramentos
    assert len(comandos) == 1
    cmd, kwargs = comandos[0]
    assert cmd[-2:] == ["qa", novo["sha"]]
    assert kwargs["env"]["REVISOR_QA_RODADA"] == "1"
    dados = json.loads(fundo.read_text(encoding="utf-8"))
    assert dados["sha"] == novo["sha"]
    assert dados["rodada"] == 1
    assert dados["pid"] == 424242
    assert "qa cliente disparado" in _log()


def test_sem_cota_usa_agente_reserva(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Exit 75 no agente principal cai no reserva (REVISOR_CMD_QA2)."""
    sha = _liga_qa(repo)
    monkeypatch.setenv("REVISOR_CMD_QA", _stub(tmp_path / "q1.sh", "exit 75\n"))
    monkeypatch.setenv("REVISOR_CMD_QA2", _stub(
        tmp_path / "q2.sh", 'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n'))
    assert mod.cmd_qa(sha) == 0
    relatorio = (repo / ".git" / "revisor" / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: APROVADO" in relatorio


# --------------------------------- critério 10 (REVISOR_QA global)

def test_env_qa_zero_bloqueia_disparo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com `REVISOR_QA=0` (conftest global), nada é disparado."""
    monkeypatch.setenv("REVISOR_QA", "0")
    base = _commit(repo, {"app.py": "print(1)\n"}, "feat: base")
    sha = _commit(repo, {"app.py": "print(2)\n"}, "feat: app")
    mod.disparar_qa([sha], sha, str(repo), base, "origin",
                    "refs/heads/main")
    assert not (_base_dir() / "qa-fundo.json").exists()
    assert "disparado" not in _log()


# --------------------------------- critérios 2 e 4 (origem e gravação)

def _roteiro_valido() -> str:
    """Roteiro válido: alvos http(s) + linha ESPERA."""
    return ("alvos: http://localhost:3000/\n"
            "- jornada 1: abrir /\n"
            "proibidas: pagar\n"
            "ESPERA: 0\n")


def test_versionado_vence_local_e_ignora_gerador(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Roteiro versionado vence o local e não chama o gerador."""
    sha = _liga_qa(repo)
    (mod.base_dir() / "qa").mkdir(parents=True, exist_ok=True)
    (mod.base_dir() / "qa" / "roteiro.md").write_text(
        "LOCAL\nESPERA: 0\nhttp://localhost:1/\n", encoding="utf-8")
    marcador = tmp_path / "gerador-chamado"
    gen = _stub(tmp_path / "gen.sh",
                f'touch "{marcador}"\nprintf "%s\\n" "SEM_ALVO: x"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    prompt_arq = tmp_path / "prompt.txt"
    qa = _stub(tmp_path / "qa.sh",
               f'printf "%s" "$3" > "{prompt_arq}"\n'
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not marcador.exists()
    prompt = prompt_arq.read_text(encoding="utf-8")
    assert "Roteiro do projeto" in prompt
    assert "LOCAL" not in prompt
    assert "gerado automaticamente" not in prompt
    assert "sem alvo" not in (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8").lower()


def test_local_usado_sem_versionado_sem_gerador(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Sem versionado, o roteiro local é usado e o gerador nem é chamado."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    (mod.base_dir() / "qa").mkdir(parents=True, exist_ok=True)
    (mod.base_dir() / "qa" / "roteiro.md").write_text(
        _roteiro_valido(), encoding="utf-8")
    marcador = tmp_path / "gerador-chamado"
    gen = _stub(tmp_path / "gen.sh",
                f'touch "{marcador}"\nprintf "%s\\n" "SEM_ALVO: x"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    prompt_arq = tmp_path / "prompt.txt"
    qa = _stub(tmp_path / "qa.sh",
               f'printf "%s" "$3" > "{prompt_arq}"\n'
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not marcador.exists()
    assert "Roteiro local" in prompt_arq.read_text(encoding="utf-8")


def test_geracao_gravada_com_cabecalho_e_reusada(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Roteiro gerado válido é gravado com cabeçalho e reusado depois."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    contador = tmp_path / "gen-vezes.txt"
    gen = _stub(tmp_path / "gen.sh",
                f'echo x >> "{contador}"\n'
                f'printf "%s\\n" "alvos: http://localhost:3000/" '
                '"- jornada 1: abrir /" "ESPERA: 0"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    gravado = (mod.base_dir() / "qa" / "roteiro.md").read_text(
        encoding="utf-8")
    assert "gerado automaticamente" in gravado
    assert ".revisor/qa.md" in gravado
    assert "http://localhost:3000/" in gravado
    assert mod.cmd_qa(sha) == 0
    assert len(contador.read_text(encoding="utf-8").splitlines()) == 1


def test_gravar_roteiro_local_atomico(repo: Path) -> None:
    """A gravação do roteiro local não deixa `.tmp` para trás."""
    mod.gravar_roteiro_qa_local(_roteiro_valido())
    caminho = mod.caminho_roteiro_qa_local()
    assert "http://localhost:3000/" in caminho.read_text(encoding="utf-8")
    assert not (caminho.parent / (caminho.name + ".tmp")).exists()


def test_roteiro_invalido_indisponivel_sem_gravar(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Geração sem ESPERA/URL nem SEM_ALVO: INDISPONIVEL, nada gravado."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    gen = _stub(tmp_path / "gen.sh",
                'printf "%s\\n" "um texto sem espera nem alvo"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\nprintf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: INDISPONIVEL" in relatorio
    assert not marcador.exists()
    assert not (mod.base_dir() / "qa" / "roteiro.md").exists()
    assert not (mod.base_dir() / "qa" / "sem-alvo").exists()


def test_gerador_ausente_indisponivel_sem_gravar(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Gerador indisponível: INDISPONIVEL, nada gravado (tenta de novo)."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO",
                       "/nao/existe/gerador-qa-teste")
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\nprintf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: INDISPONIVEL" in relatorio
    assert not marcador.exists()
    assert not (mod.base_dir() / "qa" / "roteiro.md").exists()


def test_roteiro_gerado_nao_executa_shell(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Conteúdo shell no roteiro gerado nunca é executado."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    alvo = tmp_path / "PWN"
    gen = _stub(tmp_path / "gen.sh",
                f'printf "%s\\n" "alvos: http://localhost:9/" '
                f'"- \\$(touch \\"{alvo}\\") -- ignorar" '
                f'"- \\`touch \\"{alvo}\\"\\` -- ignorar" "ESPERA: 0"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not alvo.exists()


# ------------------------------------------------- critério 3 (geração)

def test_interpretar_roteiro_qa() -> None:
    """Parser da saída do gerador: sem-alvo, roteiro ou inválido."""
    assert mod.interpretar_roteiro_qa("SEM_ALVO: só lib\n") == (
        "sem-alvo", "só lib", None)
    tipo, _corpo, espera = mod.interpretar_roteiro_qa(
        "alvos: https://exemplo.invalid/\n- j1\nESPERA: 30\n")
    assert (tipo, espera) == ("roteiro", 30)
    assert mod.interpretar_roteiro_qa("texto sem nada")[0] == "invalido"
    assert mod.interpretar_roteiro_qa("ESPERA: 10\nsem url")[0] == "invalido"
    assert mod.interpretar_roteiro_qa(
        "http://x sem espera")[0] == "invalido"


def test_interpretar_roteiro_host_externo_avisa(repo: Path) -> None:
    """Host fora de localhost no roteiro gerado avisa no log (não recusa)."""
    tipo, _corpo, _espera = mod.interpretar_roteiro_qa(
        "alvos: https://exemplo.invalid/\n- j1\nESPERA: 0\n")
    assert tipo == "roteiro"
    assert "host externo" in _log()
    assert "exemplo.invalid" in _log()


def test_interpretar_roteiro_localhost_nao_avisa(repo: Path) -> None:
    """Roteiro só com localhost não gera aviso de host externo."""
    tipo, _corpo, _espera = mod.interpretar_roteiro_qa(
        "alvos: http://localhost:3000/\n- j1\nESPERA: 0\n")
    assert tipo == "roteiro"
    assert "host externo" not in _log()


def test_geracao_usa_cwd_do_topo_e_prompt_fixo(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Gerador roda com --cwd no topo e recebe o prompt fixo."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    cwd_arq = tmp_path / "cwd.txt"
    prompt_arq = tmp_path / "prompt-gen.txt"
    gen = _stub(tmp_path / "gen.sh",
                f'[ "$1" = --cwd ] && echo "$2" > "{cwd_arq}"\n'
                f'printf "%s" "$3" > "{prompt_arq}"\n'
                f'printf "%s\\n" "alvos: http://localhost:3000/" '
                '"- jornada 1: abrir /" "ESPERA: 0"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert cwd_arq.read_text(encoding="utf-8").strip() == str(repo)
    prompt = prompt_arq.read_text(encoding="utf-8")
    for trecho in ("PRÓPRIO produto", "sem login", "proibidas",
                   "ESPERA:", "SEM_ALVO"):
        assert trecho in prompt


def test_geracao_reserva_no_exit_75(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Exit 75 no gerador principal cai no reserva (QA_ROTEIRO2)."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO",
                       _stub(tmp_path / "g1.sh", "exit 75\n"))
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO2", _stub(
        tmp_path / "g2.sh",
        'printf "%s\\n" "alvos: http://localhost:3000/" '
        '"- jornada 1: abrir /" "ESPERA: 0"\n'))
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: APROVADO" in relatorio
    assert "http://localhost:3000/" in (
        mod.base_dir() / "qa" / "roteiro.md").read_text(encoding="utf-8")


# ------------------------------------------------- critério 5 (sem alvo)

def test_sem_alvo_grava_marcador_e_pula(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """`SEM_ALVO:` grava o marcador e o relatório sai PULADO."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    gen = _stub(tmp_path / "gen.sh",
                'printf "%s\\n" "SEM_ALVO: só biblioteca"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\nprintf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not marcador.exists()
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: PULADO" in relatorio
    assert "sem alvo" in relatorio.lower()
    marcador_sem_alvo = mod.base_dir() / "qa" / "sem-alvo"
    assert marcador_sem_alvo.exists()
    assert "só biblioteca" in marcador_sem_alvo.read_text(encoding="utf-8")


def test_marcador_vivo_impede_disparo_e_loga_uma_vez(
        repo: Path) -> None:
    """Marcador vivo: `disparar_qa` nem dispara e loga 1x/dia."""
    base = _commit(repo, {"app.py": "print(1)\n"}, "feat: base")
    sha = _commit(repo, {"app.py": "print(2)\n"}, "feat: app")
    mod.gravar_sem_alvo_qa("só biblioteca")
    mod.disparar_qa([sha], sha, str(repo), base, "origin",
                    "refs/heads/main")
    assert not (_base_dir() / "qa-fundo.json").exists()
    assert _log().count("qa: pulado (sem alvo)") == 1
    mod.disparar_qa([sha], sha, str(repo), base, "origin",
                    "refs/heads/main")
    assert _log().count("qa: pulado (sem alvo)") == 1


def test_marcador_vivo_com_roteiro_local_dispara(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marcador vivo não segura o disparo quando há roteiro local."""
    base = _commit(repo, {"app.py": "print(1)\n"}, "feat: base")
    sha = _commit(repo, {"app.py": "print(2)\n"}, "feat: app")
    mod.gravar_sem_alvo_qa("só biblioteca")
    (mod.base_dir() / "qa" / "roteiro.md").write_text(
        _roteiro_valido(), encoding="utf-8")
    comandos: list[list[str]] = []

    class _ProcFalso:
        pid = 424243

    popen_real = subprocess.Popen

    def popen_falso(cmd: list[str],
                    **kwargs) -> _ProcFalso | subprocess.Popen:
        if cmd and cmd[0] == "git":
            return popen_real(cmd, **kwargs)
        comandos.append(cmd)
        return _ProcFalso()

    monkeypatch.setattr(mod.subprocess, "Popen", popen_falso)
    mod.disparar_qa([sha], sha, str(repo), base, "origin",
                    "refs/heads/main")
    assert (_base_dir() / "qa-fundo.json").exists()
    assert comandos and comandos[0][-2:] == ["qa", sha]
    assert "qa cliente disparado" in _log()
    assert "qa: pulado (sem alvo)" not in _log()


def test_marcador_vencido_apagado_e_gera_de_novo(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Marcador vencido é apagado e a geração roda de novo."""
    import datetime as _dt
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    velho = (_dt.datetime.now().astimezone()
             - _dt.timedelta(days=31)).isoformat()
    pasta = mod.base_dir() / "qa"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "sem-alvo").write_text(
        json.dumps({"quando": velho, "motivo": "antigo"}) + "\n",
        encoding="utf-8")
    gen = _stub(tmp_path / "gen.sh",
                'printf "%s\\n" "alvos: http://localhost:3000/" '
                '"- jornada 1: abrir /" "ESPERA: 0"\n')
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO", gen)
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not (pasta / "sem-alvo").exists()
    assert "QA: APROVADO" in (pasta / f"{sha}.md").read_text(
        encoding="utf-8")


def test_versionado_ignora_marcador(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        sem_espera: None) -> None:
    """Roteiro versionado presente ignora o marcador sem-alvo."""
    sha = _liga_qa(repo)
    mod.gravar_sem_alvo_qa("marcador velho")
    monkeypatch.setenv("REVISOR_CMD_QA_ROTEIRO",
                       "/nao/existe/gerador-qa-teste")
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: APROVADO" in relatorio


# ------------------------------------------------- critério 6 (espera)

def test_espera_do_roteiro_antes_do_qa(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `qaPronto`, o `ESPERA:` do roteiro vira espera (com teto)."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    (mod.base_dir() / "qa").mkdir(parents=True, exist_ok=True)
    (mod.base_dir() / "qa" / "roteiro.md").write_text(
        "alvos: http://localhost:3000/\nESPERA: 42\n", encoding="utf-8")
    dormiu: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: dormiu.append(s))
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert len(dormiu) == 1
    assert 40.0 <= dormiu[0] <= 42.0


def test_espera_respeita_teto(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`REVISOR_QA_ESPERA` limita a espera do `ESPERA:` do roteiro."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    (mod.base_dir() / "qa").mkdir(parents=True, exist_ok=True)
    (mod.base_dir() / "qa" / "roteiro.md").write_text(
        "alvos: http://localhost:3000/\nESPERA: 5000\n", encoding="utf-8")
    monkeypatch.setenv("REVISOR_QA_ESPERA", "100")
    dormiu: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: dormiu.append(s))
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert len(dormiu) == 1
    assert 98.0 <= dormiu[0] <= 100.0


def test_qaPronto_ignora_espera_do_roteiro(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com `qaPronto`, vale a espera atual (o `ESPERA:` não dorme)."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    monkeypatch.setenv("REVISOR_QA_ESPERA", "5")
    _git(repo, "config", "revisor.qaPronto", "exit 0")
    (mod.base_dir() / "qa").mkdir(parents=True, exist_ok=True)
    (mod.base_dir() / "qa" / "roteiro.md").write_text(
        "alvos: http://localhost:3000/\nESPERA: 9999\n", encoding="utf-8")
    dormiu: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: dormiu.append(s))
    qa = _stub(tmp_path / "qa.sh",
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert dormiu == []
    assert "QA: APROVADO" in (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")


def _remoto_local(tmp_path: Path, repo: Path) -> str:
    """Cria o remoto local `origin` (bare) e devolve a ref da branch."""
    bare = tmp_path / "origem.git"
    _git(tmp_path, "init", "--bare", "-q", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    ramo = _git(repo, "branch", "--show-current").stdout.strip()
    return f"refs/heads/{ramo}"


def _fundo_com_remoto(sha: str, ref: str) -> None:
    """Registra o fundo do QA apontando para o remoto `origin`."""
    fundo = _base_dir() / "qa-fundo.json"
    fundo.parent.mkdir(parents=True, exist_ok=True)
    fundo.write_text(json.dumps({"sha": sha, "novos": [sha],
                                 "remote": "origin",
                                 "remote_ref": ref}) + "\n",
                     encoding="utf-8")


def test_sem_qaPronto_espera_remoto_conter_sha(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `qaPronto`, o QA não roda antes de o remoto conter o sha."""
    _commit(repo, {"app.py": "x = 1\n"}, "feat: base")
    ref = _remoto_local(tmp_path, repo)
    _git(repo, "push", "-q", "origin", "HEAD")  # remoto só tem a base
    sha = _liga_qa(repo)  # sha novo, ainda fora do remoto
    _fundo_com_remoto(sha, ref)
    monkeypatch.setenv("REVISOR_QA_ESPERA", "2")
    monkeypatch.setenv("REVISOR_QA_INTERVALO", "0.1")
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\nprintf "%s\\n" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert not marcador.exists()
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: PULADO" in relatorio
    assert "remoto sem" in relatorio


def test_sem_qaPronto_com_remoto_atualizado_roda_qa(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `qaPronto`, com o sha já no remoto, o QA roda normalmente."""
    sha = _liga_qa(repo)
    ref = _remoto_local(tmp_path, repo)
    _git(repo, "push", "-q", "origin", "HEAD")
    _fundo_com_remoto(sha, ref)
    monkeypatch.setenv("REVISOR_QA_ESPERA", "5")
    monkeypatch.setenv("REVISOR_QA_INTERVALO", "0.1")
    marcador = tmp_path / "qa-chamado"
    qa = _stub(tmp_path / "qa.sh",
               f'touch "{marcador}"\n'
               'printf "%s\\n" "ACEITE: 1/1" "QA: APROVADO"\n')
    monkeypatch.setenv("REVISOR_CMD_QA", qa)
    assert mod.cmd_qa(sha) == 0
    assert marcador.exists()
    relatorio = (_base_dir() / "qa" / f"{sha}.md").read_text(
        encoding="utf-8")
    assert "QA: APROVADO" in relatorio


# ------------------------------------------------- critérios 7 e 8

def test_prompt_diz_origem() -> None:
    """O prompt diz a origem; gerado acrescenta o aviso de alvo."""
    gerado = mod.montar_prompt_qa("R", None, None, "abc", "gerado")
    assert ".git/revisor/qa/roteiro.md" in gerado
    assert ("roteiro gerado automaticamente: se um alvo não responder ou "
            "não for do próprio projeto, não teste e diga isso") in gerado
    local = mod.montar_prompt_qa("R", None, None, "abc", "local")
    assert "Roteiro local" in local
    versionado = mod.montar_prompt_qa("R", None, None, "abc", "versionado")
    assert ".revisor/qa.md" in versionado
    assert "não teste e diga isso" not in versionado


def test_status_mostra_sem_alvo(
        repo: Path, capsys: pytest.CaptureFixture) -> None:
    """Com marcador vivo, o status mostra `qa: sem alvo`."""
    _commit(repo, {"app.py": "x = 1\n"}, "feat: x")
    mod.gravar_sem_alvo_qa("só biblioteca")
    assert mod.cmd_status(None) == 0
    saida = capsys.readouterr().out
    assert "QA cliente" in saida
    assert "qa: sem alvo" in saida


# ---------------- QA sobrevive à worktree temporária apagada

def test_qa_roda_na_arvore_principal_quando_push_sai_de_worktree(
        repo: Path, tmp_path: Path) -> None:
    """Push de worktree temporária: o QA em fundo roda na árvore principal."""
    _liga_qa(repo)
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt))
    assert mod._pasta_estavel_qa(str(wt)) == str(repo)
    assert mod._pasta_estavel_qa(str(repo)) == str(repo)


def test_pasta_estavel_em_repositorio_bare_fica_no_topo(
        repo: Path, tmp_path: Path) -> None:
    """Sem árvore principal (bare), mantém a pasta de onde o push saiu."""
    _liga_qa(repo)
    bare = tmp_path / "bare.git"
    _git(repo, "clone", "-q", "--bare", str(repo), str(bare))
    wt = tmp_path / "wt-bare"
    _git(bare, "worktree", "add", "-q", "--detach", str(wt))
    assert mod._pasta_estavel_qa(str(wt)) == str(wt)


# ---------------- remoto que já andou por cima do sha

def test_remoto_contem_sha_ancestral_da_ponta(
        repo: Path, tmp_path: Path) -> None:
    """Outro push entrou por cima: o sha antigo ainda conta como no remoto."""
    _liga_qa(repo)
    remoto = tmp_path / "remoto.git"
    _git(repo, "init", "-q", "--bare", str(remoto))
    _git(repo, "remote", "add", "origin", str(remoto))
    ramo = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    ref = f"refs/heads/{ramo}"
    antigo = _commit(repo, {"a.py": "1\n"}, "feat: a")
    _git(repo, "push", "-q", "--no-verify", "origin", f"HEAD:{ref}")
    # um segundo clone empurra por cima (objeto ausente no repo local)
    outro = tmp_path / "outro"
    _git(repo, "clone", "-q", str(remoto), str(outro))
    _git(outro, "config", "user.email", "t@exemplo.invalid")
    _git(outro, "config", "user.name", "T")
    _git(outro, "config", "revisor.enabled", "false")
    (outro / "b.py").write_text("2\n", encoding="utf-8")
    _git(outro, "add", "b.py")
    _git(outro, "commit", "-q", "-m", "feat: b")
    _git(outro, "push", "-q", "--no-verify", "origin", f"HEAD:{ref}")
    assert mod._remoto_contem("origin", ref, antigo, str(repo))
    fora = _commit(repo, {"c.py": "3\n"}, "feat: c (nunca empurrado)")
    assert not mod._remoto_contem("origin", ref, fora, str(repo))


def test_disparo_de_worktree_usa_arvore_principal_como_cwd(
        repo: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """O processo do QA nasce na árvore principal, não na worktree do push."""
    base = _liga_qa(repo)
    sha = _commit(repo, {"app.py": "print(1)\n"}, "feat: app")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt))
    chamadas: list[dict] = []

    class _ProcFalso:
        pid = 424243

    popen_real = subprocess.Popen

    def popen_falso(cmd: list[str], **kwargs):
        if cmd and cmd[0] == "git":
            return popen_real(cmd, **kwargs)
        chamadas.append(kwargs)
        return _ProcFalso()

    monkeypatch.setattr(mod.subprocess, "Popen", popen_falso)
    mod.disparar_qa([sha], sha, str(wt), base, "origin", "refs/heads/main")
    assert len(chamadas) == 1
    assert chamadas[0]["cwd"] == str(repo)


def test_coberto_e_lido_do_contrato() -> None:
    """A linha COBERTO do agente é extraída (vai para o relatório)."""
    interp = mod.interpretar_qa(
        "COBERTO: home em 375 px, busca vazia e longa\n"
        "ACEITE: 2/2\nQA: APROVADO\n")
    assert interp["coberto"] == "home em 375 px, busca vazia e longa"
    assert mod.interpretar_qa("QA: APROVADO\n")["coberto"] is None
    assert "COBERTO:" in mod.QA_CONTRATO_SAIDA
