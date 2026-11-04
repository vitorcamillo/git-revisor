"""Testes R3a: cache de veredito APROVADO por par de árvores."""
from __future__ import annotations

import importlib.util
import json
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


def _ia_contada(monkeypatch: pytest.MonkeyPatch,
                veredito: str = "APROVADO") -> dict:
    """Troca revisar_com_ia por fake que conta chamadas na chave 'chamadas'."""
    estado = {"chamadas": 0}

    def _fake(_prompt: str, _cwd: str) -> tuple[str, str]:
        """Conta a chamada e devolve o veredito com rodapé codex."""
        estado["chamadas"] += 1
        rodape = "--- codex-ask: modo review ---"
        if veredito == "APROVADO":
            return "APROVADO", f"- nenhum problema\nVEREDITO: APROVADO\n{rodape}"
        achado = "- [MEDIA] a.txt:1 — ruim — corrija"
        return "REPROVADO", f"{achado}\nVEREDITO: REPROVADO\n{rodape}"

    monkeypatch.setattr(mod, "revisar_com_ia", _fake)
    return estado


def _arquivo_cache(base: str, ponta: str) -> Path:
    """Caminho do arquivo de cache do par de árvores base→ponta."""
    return (mod.base_dir() / "arvores"
            / f"{mod.arvore_de(base)}-{mod.arvore_de(ponta)}")


def test_revisar_diff_amend_reaproveita_cache(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesmo par de árvores após `commit --amend` chama a IA uma só vez."""
    estado = _ia_contada(monkeypatch)
    sha_base = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    sha1 = _commit(repo, {"b.txt": "2\n"}, "feat: b")
    base = mod.pai1_ou_vazio(sha_base)
    veredito1, saida1, familias1 = mod.revisar_diff(
        base, sha1, str(repo), f"{sha1[:7]} feat: b")
    assert veredito1 == "APROVADO"
    assert saida1 == "aprovado em 1 parte(s)"
    assert familias1 == {"codex"}
    assert estado["chamadas"] == 1

    _git(repo, "commit", "-q", "--amend", "-m", "outra")
    sha2 = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert sha2 != sha1  # commit novo, mesma árvore
    veredito2, saida2, familias2 = mod.revisar_diff(
        base, sha2, str(repo), f"{sha2[:7]} outra")
    assert estado["chamadas"] == 1  # IA não foi chamada de novo
    assert veredito2 == "APROVADO"
    assert saida2 == saida1 + " (cache de árvore)"
    assert familias2 == familias1

    arquivo = _arquivo_cache(base, sha1)
    assert arquivo.exists()
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1
    dados = json.loads(linhas[0])
    assert set(dados) == {"quando", "resumo", "familias"}
    assert dados["resumo"] == saida1
    assert dados["familias"] == ["codex"]


def test_reprovido_nao_entra_no_cache(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REPROVADO não grava cache: mesmo par de árvores chama a IA 2 vezes."""
    estado = _ia_contada(monkeypatch, veredito="REPROVADO")
    sha_base = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    sha = _commit(repo, {"b.txt": "2\n"}, "feat: b")
    base = mod.pai1_ou_vazio(sha_base)
    for _ in range(2):
        veredito, _saida, familias = mod.revisar_diff(
            base, sha, str(repo), f"{sha[:7]} feat: b")
        assert veredito == "REPROVADO"
        assert familias == {"codex"}
    assert estado["chamadas"] == 2
    assert not _arquivo_cache(base, sha).exists()


def test_cache_desligado_chama_ia_de_novo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """revisor.cacheVeredito false: mesmo par de árvores chama a IA 2 vezes."""
    _git(repo, "config", "revisor.cacheVeredito", "false")
    estado = _ia_contada(monkeypatch)
    sha_base = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    sha = _commit(repo, {"b.txt": "2\n"}, "feat: b")
    base = mod.pai1_ou_vazio(sha_base)
    for _ in range(2):
        veredito, _saida, _familias = mod.revisar_diff(
            base, sha, str(repo), f"{sha[:7]} feat: b")
        assert veredito == "APROVADO"
    assert estado["chamadas"] == 2


def test_relatorio_anterior_ignora_leitura_do_cache(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rodada de correção não lê o cache, mas grava para rodadas futuras."""
    estado = _ia_contada(monkeypatch)
    sha_base = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    sha = _commit(repo, {"b.txt": "2\n"}, "feat: b")
    base = mod.pai1_ou_vazio(sha_base)
    assert mod.revisar_diff(base, sha, str(repo), "commits")[0] == "APROVADO"
    assert estado["chamadas"] == 1

    # relatorio_anterior não nulo: mesmo com cache, a IA é chamada de novo
    resultado = mod.revisar_diff(base, sha, str(repo), "commits",
                                 relatorio_anterior="relatório anterior")
    assert resultado[0] == "APROVADO"
    assert estado["chamadas"] == 2

    # rodada normal seguinte volta a aproveitar o (re)gravado
    _veredito, saida, _familias = mod.revisar_diff(
        base, sha, str(repo), "commits")
    assert estado["chamadas"] == 2
    assert saida == "aprovado em 1 parte(s) (cache de árvore)"


def test_aprovacao_glm_nao_entra_no_cache(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GLM não grava cache; aprovação Codex posterior grava para o par."""
    estado = {"chamadas": 0, "rodape": (
        "--- zcode-worker: modo=review GLM-5.3 ---")}

    def _fake(_prompt: str, _cwd: str) -> tuple[str, str]:
        """Conta chamadas e assina a saída com a família configurada."""
        estado["chamadas"] += 1
        return "APROVADO", f"VEREDITO: APROVADO\n{estado['rodape']}"

    monkeypatch.setattr(mod, "revisar_com_ia", _fake)
    sha_base = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    sha = _commit(repo, {"b.txt": "2\n"}, "feat: b")
    base = mod.pai1_ou_vazio(sha_base)

    veredito, _saida, familias = mod.revisar_diff(
        base, sha, str(repo), f"{sha[:7]} feat: b")
    assert veredito == "APROVADO"
    assert mod.familia_revisor("--- zcode-worker: modo=review GLM-5.3 ---") == "glm"
    assert familias == {"glm"}
    arvores = mod.base_dir() / "arvores"
    assert not arvores.exists() or not list(arvores.iterdir())

    estado["rodape"] = "--- codex-ask: modo=review gpt-6-astra ---"
    veredito, _saida, familias = mod.revisar_diff(
        base, sha, str(repo), f"{sha[:7]} feat: b")
    assert veredito == "APROVADO"
    assert estado["chamadas"] == 2
    assert mod.familia_revisor(estado["rodape"]) == "codex"
    assert familias == {"codex"}
    assert len(list(arvores.iterdir())) == 1
