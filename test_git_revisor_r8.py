"""Testes R8: check final por seleção de testes (`revisor.checkFinal`)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
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
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    caminho = tmp_path / "repo"
    caminho.mkdir()
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    _git(caminho, "config", "revisor.enabled", "false")
    monkeypatch.chdir(caminho)
    return caminho


def _capturar_execucao(monkeypatch: pytest.MonkeyPatch,
                       resultado: tuple[bool, str] = (True, "")) -> dict:
    """Troca _executar_checks por fake que registra o que recebeu."""
    estado: dict = {"chamadas": 0}

    def _fake(checks, tmp, timeout_check, pulos,
              rotulo_log: str = "check", **kwargs):
        estado["chamadas"] += 1
        estado["checks"] = list(checks)
        estado["tmp"] = tmp
        estado["timeout"] = timeout_check
        estado["pulos"] = pulos
        estado["rotulo"] = rotulo_log
        estado["kwargs"] = dict(kwargs)
        return resultado

    monkeypatch.setattr(mod, "_executar_checks", _fake)
    return estado


_PYTEST = ("/usr/bin/python3", "-m", "pytest", "-q")


def test_padrao_sem_config_roda_comando_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `revisor.checkFinal`, rodar_checks executa o comando completo."""
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    _commit(repo, {"app.py": "x = 1\n",
                   "tests/test_x.py": "def test_a():\n    assert True\n"},
            "feat: base")
    sha = _commit(repo, {"app.py": "x = 2\n"}, "feat: app v2")
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert estado["chamadas"] == 1
    assert [c[1] for c in estado["checks"]] == [list(_PYTEST)]
    assert estado["rotulo"] == "check"


def test_selecionado_roda_teste_correspondente(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`selecionado`: pytest roda só o arquivo de teste alterado."""
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"app.py": "x = 1\n",
                   "tests/test_x.py": "def test_a():\n    assert True\n"},
            "feat: base")
    sha = _commit(repo, {"tests/test_x.py": "def test_b():\n    assert True\n"},
                  "feat: teste b")
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert estado["chamadas"] == 1
    assert [c[1] for c in estado["checks"]] == [
        ["/usr/bin/python3", "-m", "pytest", "-q", "tests/test_x.py"]]
    assert estado["rotulo"] == "check-selecionado"


def test_selecionado_pyproject_forca_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`selecionado` com pyproject.toml alterado: roda o comando completo."""
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"tests/test_x.py": "def test_a():\n    assert True\n",
                   "pyproject.toml": "[project]\nname = 'x'\n"},
            "feat: base")
    sha = _commit(repo, {"pyproject.toml": "[project]\nname = 'y'\n"},
                  "chore: pyproject")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert [c[1] for c in estado["checks"]] == [list(_PYTEST)]
    assert estado["rotulo"] == "check"
    assert any("arquivo de build/config alterado" in l for l in logs)


def test_selecionado_check_nao_selecionavel_roda_original(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`selecionado` com comando sem seleção: roda o comando original."""
    _git(repo, "config", "revisor.check", "@ ./run.sh")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"run.sh": "#!/bin/sh\n", "a.txt": "1\n"}, "feat: base")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: a")
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert [c[1] for c in estado["checks"]] == [["./run.sh"]]
    assert estado["rotulo"] == "check-selecionado"


def test_valor_invalido_usa_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`revisor.checkFinal` inválido: aviso no log e caminho completo."""
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    _git(repo, "config", "revisor.checkFinal", "imediato")
    _commit(repo, {"app.py": "x = 1\n"}, "feat: base")
    sha = _commit(repo, {"app.py": "x = 2\n"}, "feat: app v2")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert [c[1] for c in estado["checks"]] == [list(_PYTEST)]
    assert estado["rotulo"] == "check"
    assert any("revisor.checkFinal inválido: imediato; usando completo" in l
               for l in logs)


def test_falha_do_check_selecionado_reprova(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`selecionado` com teste quebrado: (False, relatório) do check real."""
    _git(repo, "config", "revisor.check",
         f"@ {sys.executable} -m pytest -q")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"app.py": "x = 1\n",
                   "tests/test_ok.py": "def test_ok():\n    assert True\n"},
            "feat: base")
    sha = _commit(repo, {"tests/test_ruim.py":
                         "def test_ruim():\n    assert False\n"},
                  "feat: teste ruim")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert "VEREDITO: REPROVADO" in rel
    assert "test_ruim" in rel
    assert any("check-selecionado" in l for l in logs)


def test_selecionado_pytest_com_teste_correspondente_nao_degrada(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`selecionado` com seleção útil e código alterado: mantém a seleção."""
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"x.py": "x = 1\n",
                   "tests/test_x.py": "def test_a():\n    assert True\n"},
            "feat: base")
    sha = _commit(repo, {"x.py": "x = 2\n",
                         "tests/test_x.py": "def test_b():\n    assert True\n"},
                  "feat: x e teste b")
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert [c[1] for c in estado["checks"]] == [
        ["/usr/bin/python3", "-m", "pytest", "-q", "tests/test_x.py"]]
    assert estado["rotulo"] == "check-selecionado"


# ------------------------------------------- seleção degradada no final


def test_selecionado_maven_mais_de_30_padroes_roda_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Maven com >30 padrões degrada; com código alterado, roda completo."""
    _git(repo, "config", "revisor.check", "@ mvn -B -q test")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"docs/a.md": "base\n"}, "docs: base")
    classes = {f"src/main/java/app/C{i}.java": f"class C{i} {{}}\n"
               for i in range(31)}
    sha = _commit(repo, classes, "feat: 31 classes")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert [c[1] for c in estado["checks"]] == [["mvn", "-B", "-q", "test"]]
    assert estado["rotulo"] == "check-selecionado"
    assert any("seleção sem testes para .; rodando completo" in l
               for l in logs)


def test_selecionado_maven_so_docs_mantem_degradado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Maven com só docs alterado: mantém o `test-compile` degradado."""
    _git(repo, "config", "revisor.check", "@ mvn -B -q test")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"docs/x.md": "v1\n"}, "docs: v1")
    sha = _commit(repo, {"docs/x.md": "v2\n"}, "docs: v2")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert [c[1] for c in estado["checks"]] == [
        ["mvn", "-B", "-q", "test-compile"]]
    assert estado["rotulo"] == "check-selecionado"
    assert not any("seleção sem testes" in l for l in logs)


def test_selecionado_pytest_modulo_sem_teste_roda_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pytest com módulo sem teste correspondente degrada; roda completo."""
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _commit(repo, {"pkg/__init__.py": ""}, "feat: base")
    sha = _commit(repo, {"pkg/modulo.py": "y = 2\n"}, "feat: modulo")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    estado = _capturar_execucao(monkeypatch)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert [c[1] for c in estado["checks"]] == [list(_PYTEST)]
    assert estado["rotulo"] == "check-selecionado"
    assert any("seleção sem testes para .; rodando completo" in l
               for l in logs)


def test_selecao_degradada_e_toca_codigo() -> None:
    """Helpers `_selecao_degradada` e `_toca_codigo` cobrem os limites."""
    assert mod._selecao_degradada(["mvn", "-B", "-q", "test-compile"])
    assert mod._selecao_degradada(
        ["/usr/bin/python3", "-m", "compileall", "-q", "a.py"])
    assert not mod._selecao_degradada(
        ["/usr/bin/python3", "-m", "pytest", "-q", "tests/test_x.py"])
    assert not mod._selecao_degradada(["mvn", "-B", "-q", "test"])
    assert mod._selecao_degradada(["python3", "-m", "compileall"])
    # toca código: extensões, src no início e no meio, prefixo e apagados
    assert not mod._toca_codigo("", ["docs/a.md", "leiame.txt"])
    assert mod._toca_codigo("", ["docs/a.md", "app.py"])
    assert mod._toca_codigo("", ["src/main/resources/a.yml"])
    assert mod._toca_codigo("", ["web/lib/src/A.kt"])
    assert not mod._toca_codigo("web", ["web/lib/README.md"])
    assert mod._toca_codigo("web", ["web/lib/src/A.kt"])
    assert mod._toca_codigo("", ["pkg/apagado.py"])  # lista completa



# ------------------------------------------------------- config e globs


def test_check_final_selecionado_valores(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check_final_selecionado: só `selecionado` (strip/lower) é True."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    assert mod.check_final_selecionado(str(repo)) is False  # ausente
    _git(repo, "config", "revisor.checkFinal", "completo")
    assert mod.check_final_selecionado(str(repo)) is False
    assert not any("inválido" in l for l in logs)
    _git(repo, "config", "revisor.checkFinal", "  SELECIONADO  ")
    assert mod.check_final_selecionado(str(repo)) is True
    _git(repo, "config", "revisor.checkFinal", "rapido")
    assert mod.check_final_selecionado(str(repo)) is False
    assert any("revisor.checkFinal inválido: rapido; usando completo" in l
               for l in logs)


def test_forca_completo_globs() -> None:
    """Globs de ARQUIVOS_FORCAM_COMPLETO casam caminho relativo e basename."""
    forcam = ("pom.xml", "build.gradle", "build.gradle.kts", "package.json",
              "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
              "requirements.txt", "requirements-dev.txt", "pyproject.toml",
              "setup.cfg", "setup.py", "conftest.py", "pytest.ini", "tox.ini",
              "app/src/test/resources/log4j.xml",
              "src/test/resources/application.yml",
              "web/pom.xml")
    for arquivo in forcam:
        assert mod._forca_completo(arquivo), arquivo
    passam = ("a.txt", "src/main/java/App.java", "tests/test_x.py",
              "docs/pom.xml.bak", "requirements.rst")
    for arquivo in passam:
        assert not mod._forca_completo(arquivo), arquivo
