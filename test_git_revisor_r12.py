"""Testes R12: errata de mensagem de commit (`.revisor/errata.md`).

Divergência mensagem↔diff volta a ser MEDIA (a R9 a tinha rebaixado para
BAIXA) com caminho de resolução versionado: a errata registrada na ponta
revisada corrige só a descrição do commit, e o worker pode escrever nela
(mas não nos demais arquivos de `.revisor/`).
"""
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


# ---------------------------------------------------------------- ler_errata

def test_ler_errata_le_da_arvore_da_ponta(repo: Path) -> None:
    """Errata commitada na ponta é lida da árvore; antes dela, None."""
    sha_antes = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    assert mod.ler_errata(sha_antes) is None

    errata = f"- {sha_antes[:7]}: na verdade só renomeou o arquivo\n"
    sha_com = _commit(repo, {".revisor/errata.md": errata}, "docs: errata")
    assert mod.ler_errata(sha_antes) is None  # ponta anterior à errata
    assert mod.ler_errata(sha_com) == errata
    # errata anterior continua visível em ponta posterior (herda da árvore)
    sha_depois = _commit(repo, {"b.txt": "2\n"}, "feat: b")
    assert mod.ler_errata(sha_depois) == errata


def test_ler_errata_ausente_devolve_none(repo: Path) -> None:
    """Sem `.revisor/errata.md` na árvore da ponta, devolve None."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    assert mod.ler_errata(sha) is None


def test_ler_errata_corta_no_teto(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Conteúdo acima de MAX_ERRATA é cortado em bytes UTF-8."""
    sha = _commit(repo, {".revisor/errata.md": "x" * 100}, "docs: errata")
    monkeypatch.setattr(mod, "MAX_ERRATA", 10)
    lida = mod.ler_errata(sha)
    assert lida == "x" * 10
    assert len(lida.encode("utf-8")) <= mod.MAX_ERRATA


def test_ler_errata_acima_do_teto_mantem_entrada_nova(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entradas são acrescentadas no fim: acima do teto, a cauda (com a
    entrada nova) é que entra, sem linha parcial no começo."""
    antigo = "".join(f"- {i:07d}: entrada antiga {i}\n" for i in range(50))
    novo = "- abc1234: entrada nova\n"
    sha = _commit(repo, {".revisor/errata.md": antigo + novo}, "docs: errata")
    monkeypatch.setattr(mod, "MAX_ERRATA", 200)
    lida = mod.ler_errata(sha)
    assert lida.endswith(novo)
    assert len(lida.encode("utf-8")) <= mod.MAX_ERRATA
    assert all(linha.startswith("- ") for linha in lida.splitlines())


# ----------------------------------------------------------- intencao_commits

def test_intencao_commits_com_ponta_inclui_errata(repo: Path) -> None:
    """Com ponta e errata registrada, o bloco de errata entra na intenção."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    sem = mod.intencao_commits([sha])
    assert "errata" not in sem  # sem ponta, não inclui

    errata = f"- {sha[:7]}: na verdade só adicionou o arquivo\n"
    _commit(repo, {".revisor/errata.md": errata}, "docs: errata")
    com = mod.intencao_commits([sha], ponta="HEAD")
    assert f"== {sha[:7]} ==" in com
    assert "== errata (.revisor/errata.md) ==" in com
    assert f"- {sha[:7]}" in com
    assert "na verdade só adicionou o arquivo" in com


def test_intencao_commits_sem_ponta_nao_inclui_errata(repo: Path) -> None:
    """Sem `ponta`, a intenção fica igual mesmo com errata no worktree."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    _commit(repo, {".revisor/errata.md": "- xyz: nada\n"}, "docs: errata")
    assert "errata" not in mod.intencao_commits([sha])


def test_intencao_commits_errata_depois_do_corte(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mensagens cortadas em MAX_INTENCAO com "(…)"; errata INTEIRA depois."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: " + "x" * 300)
    _commit(repo, {".revisor/errata.md": "- " + "e" * 100 + "\n"},
            "docs: errata")
    monkeypatch.setattr(mod, "MAX_INTENCAO", 100)
    texto = mod.intencao_commits([sha], ponta="HEAD")
    # a parte de mensagens sai cortada no teto e a errata entra inteira,
    # DEPOIS do corte (não é cortada junto)
    marcador = "== errata (.revisor/errata.md) =="
    assert marcador in texto
    cortadas, errata = texto.split(marcador, 1)
    assert cortadas.rstrip("\n").endswith("(…)")
    assert len(cortadas.rstrip("\n").encode("utf-8")) <= mod.MAX_INTENCAO
    assert f"- {'e' * 100}" in errata


# -------------------------------------------------------------- montar_prompt

def test_montar_prompt_regra_media_com_errata() -> None:
    """Regra de divergência: MEDIA, errata cobre descrição, nunca bug."""
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao="feat: faz algo")
    assert "MEDIA" in prompt
    assert "é problema MEDIA" in prompt
    assert "errata" in prompt
    assert "nunca justifica bug" in prompt
    assert "não reprove por isso" not in prompt


# ------------------------------------------------------------- tarefa_worker

def test_tarefa_worker_instrui_errata() -> None:
    """Tarefa do worker ensina a registrar divergência descritiva na errata."""
    tarefa = mod.tarefa_worker("x")
    assert ".revisor/errata.md" in tarefa
    assert "Nunca use a errata para justificar bug" in tarefa
    # o relatório continua emoldurado como DADO
    assert "<<<RELATORIO\nx\nRELATORIO>>>" in tarefa


# -------------------------------------------------------- caminhos_proibidos

def test_caminhos_proibidos_revisor_menos_errata(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker não pode tocar `.revisor/`, exceto `errata.md`."""
    sha = _commit(repo, {".revisor/contexto.md": "dono\n"}, "docs: contexto")
    wt = repo.with_name("wt")
    _git(repo, "worktree", "add", "--detach", str(wt), sha)

    # contexto.md alterado no worktree: proibido
    (wt / ".revisor" / "contexto.md").write_text("alterado pelo worker\n",
                                                 encoding="utf-8")
    proibidos = mod.caminhos_proibidos(str(wt))
    assert ".revisor/contexto.md" in proibidos

    # só errata.md criada/alterada: não é proibido
    _git(wt, "checkout", "--", ".revisor/contexto.md")
    (wt / ".revisor" / "errata.md").write_text("- abc1234: faz outra coisa\n",
                                               encoding="utf-8")
    assert mod.caminhos_proibidos(str(wt)) == []


def test_caminhos_proibidos_errata_como_unico_arquivo_novo(
        repo: Path) -> None:
    """`.revisor/errata.md` não rastreada e nova não é proibida."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    wt = repo.with_name("wt2")
    _git(repo, "worktree", "add", "--detach", str(wt), sha)
    (wt / ".revisor").mkdir()
    (wt / ".revisor" / "errata.md").write_text("- abc1234: faz outra coisa\n",
                                               encoding="utf-8")
    assert mod.caminhos_proibidos(str(wt)) == []
    # mas outro arquivo novo sob .revisor/ é proibido
    (wt / ".revisor" / "contexto.md").write_text("worker\n", encoding="utf-8")
    assert ".revisor/contexto.md" in mod.caminhos_proibidos(str(wt))


def test_caminhos_proibidos_rename_do_dono_e_barrado(repo: Path) -> None:
    """`git mv` de `.revisor/contexto.md` (dono) para errata.md é barrado:
    a origem também é verificada."""
    sha = _commit(repo, {".revisor/contexto.md": "dono\n"}, "docs: contexto")
    wt = repo.with_name("wt4")
    _git(repo, "worktree", "add", "--detach", str(wt), sha)
    _git(wt, "mv", ".revisor/contexto.md", ".revisor/errata.md")
    assert ".revisor/contexto.md" in mod.caminhos_proibidos(str(wt))
