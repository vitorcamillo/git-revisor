"""Testes da aprovação reaproveitada após rebase limpo (aprovação por patch).

Segue o estilo de `tests/test_git_revisor_r5b.py` (importar o módulo como
`mod`, fixture `repo` com git real e helper `_commit`).
"""
from __future__ import annotations

import importlib.util
import json
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


def _sha(repo: Path, ref: str = "HEAD") -> str:
    """Sha completo da ref dada."""
    return _git(repo, "rev-parse", ref).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repositório git temporário com o revisor desligado, como cwd."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q", "-b", "main")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _cenario_rebase(repo: Path) -> tuple[str, str, str, str]:
    """Base A; X sobre A; Z sobre A; X' = X em cima de Z. Devolve (A, X, Z, X')."""
    base = _commit(repo, {"a.txt": "base\n"}, "base A")
    original = _commit(repo, {"x.txt": "conteudo x\n"}, "feat: x")
    _git(repo, "reset", "-q", "--hard", base)
    _commit(repo, {"z.txt": "conteudo z\n"}, "feat: z")
    _git(repo, "cherry-pick", original)
    return base, original, _sha(repo, "HEAD~1"), _sha(repo, "HEAD")


def _proibido(nome: str):
    """Função que falha o teste se chamada."""
    def _chamada(*_args: object, **_kwargs: object):
        raise AssertionError(f"{nome} não deveria ser chamado")
    return _chamada


def test_impressao_igual_apos_rebase_limpo(repo: Path) -> None:
    """Mesmo conteúdo em base nova: sha difere, impressão igual, patch-id vem."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    assert original != rebaseado
    imp_original = mod.impressao_commit(original)
    imp_rebaseado = mod.impressao_commit(rebaseado)
    assert imp_original is not None and imp_rebaseado is not None
    assert imp_original[0] == imp_rebaseado[0]
    assert imp_original[1] and imp_rebaseado[1]


def test_impressao_muda_quando_conteudo_muda(repo: Path) -> None:
    """Linha alterada (conflito resolvido à mão) muda a impressão."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    imp_antes = mod.impressao_commit(rebaseado)
    assert imp_antes is not None
    (repo / "x.txt").write_text("conteudo x\nlinha extra do conflito\n",
                                encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "--no-edit")
    alterado = _sha(repo)
    assert alterado != rebaseado
    imp_depois = mod.impressao_commit(alterado)
    assert imp_depois is not None
    assert imp_depois[0] != imp_antes[0]
    assert mod.impressao_commit(original) is not None


def test_impressao_muda_com_diferenca_so_de_espacos(repo: Path) -> None:
    """Só indentação muda a impressão (mais estrita que `git patch-id`)."""
    _commit(repo, {"a.txt": "base\n"}, "base A")
    com_dois = _commit(repo, {"x.txt": "linha\n  indentada\n"}, "feat: x")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    com_quatro = _commit(repo, {"x.txt": "linha\n    indentada\n"}, "feat: x")
    imp_dois = mod.impressao_commit(com_dois)
    imp_quatro = mod.impressao_commit(com_quatro)
    assert imp_dois is not None and imp_quatro is not None
    assert imp_dois[0] != imp_quatro[0]


def test_impressao_muda_quando_so_mensagem_muda(repo: Path) -> None:
    """Mesmo diff com mensagem nova (amend) muda a impressão."""
    _commit(repo, {"base.txt": "base\n"}, "chore: base")  # raiz não tem impressão
    sha =_commit(repo, {"x.txt": "conteudo\n"}, "feat: x")
    imp_antes = mod.impressao_commit(sha)
    assert imp_antes is not None
    _git(repo, "commit", "-q", "--amend", "-m", "feat: x com outro texto")
    novo = _sha(repo)
    assert novo != sha
    imp_depois = mod.impressao_commit(novo)
    assert imp_depois is not None
    assert imp_depois[0] != imp_antes[0]


def test_impressao_merge_e_raiz_devolvem_none(repo: Path) -> None:
    """Merge (vários pais) e commit raiz (sem pai) não têm impressão."""
    raiz = _commit(repo, {"a.txt": "base\n"}, "base A")
    assert mod.impressao_commit(raiz) is None
    _git(repo, "checkout", "-q", "-b", "ramo")
    _commit(repo, {"b.txt": "ramo\n"}, "feat: ramo")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, {"c.txt": "main\n"}, "feat: main")
    _git(repo, "merge", "-q", "--no-ff", "ramo", "-m", "merge ramo")
    assert mod.impressao_commit(_sha(repo)) is None


def test_marcar_aprovado_grava_patch_e_herda(repo: Path) -> None:
    """Aprovação codex grava a impressão; X' herda, X não, glm/nada não grava."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok", familia="codex")
    impressao = mod.impressao_commit(original)
    assert impressao is not None
    pasta = mod.base_dir() / "aprovados-patch"
    assert (pasta / impressao[0]).exists()
    herdado = mod.aprovacao_herdavel(rebaseado)
    assert herdado is not None and herdado["sha"] == original
    assert mod.aprovacao_herdavel(original) is None  # mesmo sha não herda
    antes = set(pasta.iterdir())
    outro1 = _commit(repo, {"o1.txt": "1\n"}, "feat: o1")
    mod.marcar_aprovado(outro1, "ok", familia="glm")
    outro2 = _commit(repo, {"o2.txt": "2\n"}, "feat: o2")
    mod.marcar_aprovado(outro2, "ok", familia=None)
    outro3 = _commit(repo, {"o3.txt": "3\n"}, "feat: o3")
    mod.marcar_aprovado(outro3, "ok", familia="codex", requer=original)
    assert set(pasta.iterdir()) == antes


def test_aprovacao_original_removida_nao_herda(repo: Path) -> None:
    """Sem o marcador incondicional da origem, X' não herda nada."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok", familia="codex")
    assert mod.aprovacao_herdavel(rebaseado) is not None
    (mod.base_dir() / "aprovados" / original).unlink()
    assert mod.aprovacao_herdavel(rebaseado) is None


def test_config_desligada_prefixo_vazio(repo: Path) -> None:
    """`revisor.aprovacaoPorPatch=false`: nada é herdado."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok", familia="codex")
    assert mod.aprovacao_herdavel(rebaseado) is not None
    _git(repo, "config", "revisor.aprovacaoPorPatch", "false")
    assert mod.prefixo_herdado([rebaseado], str(repo)) == []


def test_prefixo_para_no_primeiro_sem_heranca(repo: Path) -> None:
    """`prefixo_herdado([X', Y])` com Y novo devolve só X'."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok", familia="codex")
    novo = _commit(repo, {"y.txt": "novo\n"}, "feat: y")
    herdados = mod.prefixo_herdado([rebaseado, novo], str(repo))
    assert [sha for sha, _dados in herdados] == [rebaseado]


def _mocks_intervalo(monkeypatch: pytest.MonkeyPatch, *,
                     ia: object) -> None:
    """Mocks comuns do push herdado: gitleaks/retomada/checks/IA/suíte/QA."""
    monkeypatch.setattr(mod, "log", lambda *a, **k: None)
    monkeypatch.setattr(mod, "notificar", lambda *a, **k: None)
    monkeypatch.setattr(mod, "autorrevisao", lambda *a, **k: False)
    monkeypatch.setattr(mod, "gitleaks_intervalo", lambda *a, **k: 0)
    monkeypatch.setattr(mod, "retomar_tentativa", lambda *a, **k: None)
    monkeypatch.setattr(mod, "classificar_risco", lambda *a, **k: ("alto", []))
    monkeypatch.setattr(mod, "rodar_checks_rapidos", lambda *a, **k: (True, ""))
    monkeypatch.setattr(mod, "rodar_checks", lambda *a, **k: (True, ""))
    monkeypatch.setattr(mod, "revisar_diff", ia)
    monkeypatch.setattr(mod, "disparar_suite_fundo_se_preciso",
                        lambda *a, **k: None)
    monkeypatch.setattr(mod, "disparar_qa", lambda *a, **k: None)


def test_intervalo_herdado_nao_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """X' aprovado antes via X: sem IA, marca herdado, relatório e log."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok revisado", familia="codex")
    impressao, patch_id = mod.impressao_commit(rebaseado) or ("", "")
    assert impressao and patch_id
    _mocks_intervalo(monkeypatch, ia=_proibido("revisar_diff"))
    assert mod.revisar_intervalo([rebaseado], rebaseado, str(repo), []) == 0
    assert mod.esta_aprovado(rebaseado)
    marcador = mod._marcador_aprovado(rebaseado)
    assert marcador is not None and marcador.get("herdado_de") == original
    relatorio = mod.ler_relatorio(rebaseado)
    assert relatorio is not None
    assert original in relatorio and rebaseado in relatorio
    assert patch_id in relatorio
    dados = json.loads(
        (mod.base_dir() / "aprovados-patch" / impressao).read_text(
            encoding="utf-8"))
    assert dados["sha"] == original  # herdado não toma a origem


def test_intervalo_conteudo_diferente_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Conflito resolvido à mão: a IA é chamada com base no pai de X'."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok", familia="codex")
    (repo / "x.txt").write_text("conteudo x\nlinha do conflito\n",
                                encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "--no-edit")
    alterado = _sha(repo)
    chamadas: list[tuple] = []

    def _ia(base: str, *args: object, **_kw: object) -> tuple:
        """Registra a base do diff e aprova."""
        chamadas.append((base, args))
        return ("APROVADO", "tudo certo\n--- codex-ask: x", {"codex"})

    _mocks_intervalo(monkeypatch, ia=_ia)
    assert mod.revisar_intervalo([alterado], alterado, str(repo), []) == 0
    assert chamadas and chamadas[0][0] == _sha(repo, f"{alterado}~1")
    assert mod.esta_aprovado(alterado)
    marcador = mod._marcador_aprovado(alterado)
    assert marcador is not None and marcador.get("herdado_de") is None


def test_intervalo_lote_herdado_mais_novo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`[X', Y]`: IA vê o diff desde X' (pai de Y); X' herdado, Y normal."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok", familia="codex")
    novo = _commit(repo, {"y.txt": "novo\n"}, "feat: y")
    chamadas: list[str] = []

    def _ia(base: str, *args: object, **_kw: object) -> tuple:
        """Registra a base do diff e aprova."""
        chamadas.append(base)
        return ("APROVADO", "tudo certo\n--- codex-ask: x", {"codex"})

    _mocks_intervalo(monkeypatch, ia=_ia)
    assert mod.revisar_intervalo([rebaseado, novo], novo, str(repo), []) == 0
    assert chamadas == [rebaseado]
    marcador_rebaseado = mod._marcador_aprovado(rebaseado)
    assert (marcador_rebaseado is not None
            and marcador_rebaseado.get("herdado_de") == original)
    assert mod.esta_aprovado(novo)
    marcador_novo = mod._marcador_aprovado(novo)
    assert marcador_novo is not None and marcador_novo.get("herdado_de") is None


def test_intervalo_herdado_com_check_falho_bloqueia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Herdado com checks vermelhos: bloqueia e X' não fica aprovado."""
    _base, original, _z, rebaseado = _cenario_rebase(repo)
    mod.marcar_aprovado(original, "ok", familia="codex")
    _mocks_intervalo(monkeypatch, ia=_proibido("revisar_diff"))
    monkeypatch.setattr(mod, "rodar_checks", lambda *a, **k: (False, "falhou"))
    monkeypatch.setattr(mod, "caminho_correcao_push",
                        lambda *args, **kw: 1)
    assert mod.revisar_intervalo([rebaseado], rebaseado, str(repo), []) != 0
    assert not mod.esta_aprovado(rebaseado)
