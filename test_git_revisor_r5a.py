"""Testes do check rápido (seleção e execução isolada)."""
from __future__ import annotations

import contextlib
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

_MVN = ["mvn", "-B", "-q", "test"]
_FLAGS_MVN = ["-Dsurefire.failIfNoSpecifiedTests=false",
              "-DfailIfNoTests=false"]


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
    _git(caminho, "init", "-q")
    _git(caminho, "config", "user.email", "teste@exemplo.invalid")
    _git(caminho, "config", "user.name", "Teste")
    monkeypatch.chdir(caminho)
    return caminho


def _pytest_cmd() -> list[str]:
    """Comando pytest detectado típico: `<python> -m pytest -q`."""
    return [sys.executable, "-m", "pytest", "-q"]


def test_maven_classe_principal_vira_dtest() -> None:
    """`src/main` alterado vira `-Dtest=` com os dois padrões."""
    assert mod.selecionar_teste_rapido(
        "", [*_MVN], ["src/main/java/a/Foo.java"]) == [
            "mvn", "-B", "-q", "test", "-Dtest=Foo*Test,Foo*Tests",
            *_FLAGS_MVN]


def test_maven_teste_alterado_vira_classe_exata() -> None:
    """Teste alterado vira `-Dtest=` com o nome exato."""
    assert mod.selecionar_teste_rapido(
        "", [*_MVN], ["src/test/java/a/BarTest.java"]) == [
            "mvn", "-B", "-q", "test", "-Dtest=BarTest", *_FLAGS_MVN]


def test_maven_pom_alterado_vira_test_compile() -> None:
    """`pom.xml` alterado desiste da seleção e só compila."""
    assert mod.selecionar_teste_rapido(
        "", [*_MVN],
        ["src/main/java/a/Foo.java", "pom.xml"]) == [
            "mvn", "-B", "-q", "test-compile"]


def test_maven_muitas_classes_viram_test_compile() -> None:
    """Mais de 30 padrões desiste da seleção e só compila."""
    arquivos = [f"src/main/java/a/C{i}.java" for i in range(31)]
    assert mod.selecionar_teste_rapido("", [*_MVN], arquivos) == [
        "mvn", "-B", "-q", "test-compile"]


def test_maven_prefixo_ignora_arquivos_de_fora() -> None:
    """Só contam os arquivos dentro da pasta coberta pelo check."""
    assert mod.selecionar_teste_rapido(
        "backend", [*_MVN],
        ["backend/src/main/java/a/Foo.java",
         "outro/src/main/java/b/Bar.java"]) == [
            "mvn", "-B", "-q", "test", "-Dtest=Foo*Test,Foo*Tests",
            *_FLAGS_MVN]


def test_pytest_arquivo_de_teste_direto() -> None:
    """Teste alterado entra direto no comando pytest."""
    assert mod.selecionar_teste_rapido(
        "", _pytest_cmd(), ["tests/test_x.py"]) == [
            *_pytest_cmd(), "tests/test_x.py"]


def test_pytest_modulo_acha_teste_pelo_nome(tmp_path: Path) -> None:
    """Módulo alterado puxa o teste cujo nome contém o módulo."""
    raiz = tmp_path / "wt"
    (raiz / "tests").mkdir(parents=True)
    (raiz / "tests" / "test_calc.py").write_text(
        "def test_soma():\n    assert True\n", encoding="utf-8")
    (raiz / "pkg").mkdir()
    (raiz / "pkg" / "calc.py").write_text("x = 1\n", encoding="utf-8")
    assert mod.selecionar_teste_rapido(
        "", _pytest_cmd(), ["pkg/calc.py"], raiz=str(raiz)) == [
            *_pytest_cmd(), "tests/test_calc.py"]


def test_pytest_sem_teste_vira_compileall(tmp_path: Path) -> None:
    """`.py` sem teste correspondente vira `compileall`."""
    raiz = tmp_path / "wt"
    (raiz / "tests").mkdir(parents=True)
    (raiz / "tests" / "test_outro.py").write_text(
        "def test_nada():\n    assert True\n", encoding="utf-8")
    assert mod.selecionar_teste_rapido(
        "", _pytest_cmd(), ["pkg/sem_teste.py"],
        raiz=str(raiz)) == [
            sys.executable, "-m", "compileall", "-q", "pkg/sem_teste.py"]


def test_comando_desconhecido_nao_tem_selecao() -> None:
    """npm (e afins) devolve None: sem seleção rápida."""
    assert mod.selecionar_teste_rapido(
        "", ["npm", "test"], ["src/app.js"]) is None


def test_rapido_desligado_nao_executa(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`revisor.checkRapido=false` devolve ok sem chamar nada."""
    _git(repo, "config", "revisor.checkRapido", "false")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)

    def _boom(*args: object, **kw: object) -> tuple[bool, str]:
        raise AssertionError("não deveria executar checks")

    monkeypatch.setattr(mod, "_executar_checks", _boom)
    assert mod.rodar_checks_rapidos("abc", str(repo), "def") == (True, "")
    assert logs == []


def test_rapido_lock_ocupado_pula_com_log(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock ocupado devolve ok com o log de pulo."""
    base = _commit(repo, {"pkg/calc.py": "x = 1\n"}, "base")
    sha = _commit(repo, {"pkg/calc.py": "x = 2\n"}, "altera")
    _git(repo, "config", "revisor.checkRapidoCmd",
         f"@ {sys.executable} -m pytest -q")

    @contextlib.contextmanager
    def _wt(_sha: str, _topo: str) -> object:
        yield str(repo)

    @contextlib.contextmanager
    def _travada(*args: object, **kw: object) -> object:
        yield False

    monkeypatch.setattr(mod, "worktree_checks", _wt)
    monkeypatch.setattr(mod, "trava", _travada)
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)

    def _boom(*args: object, **kw: object) -> tuple[bool, str]:
        raise AssertionError("não deveria executar checks")

    monkeypatch.setattr(mod, "_executar_checks", _boom)
    assert mod.rodar_checks_rapidos(sha, str(repo), base) == (True, "")
    assert "check rápido pulado: lock ocupado" in logs


def test_rapido_cmd_configurado_vai_pronto(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path) -> None:
    """`checkRapidoCmd` chega ao executor sem passar pela seleção."""
    base = _commit(repo, {"src/main/java/a/Foo.java": "class Foo {}\n"},
                   "base")
    sha = _commit(repo, {"src/main/java/a/Foo.java": "class Foo { int x; }\n"},
                  "altera")
    _git(repo, "config", "revisor.checkRapidoCmd", "@ mvn -B -q test-compile")

    @contextlib.contextmanager
    def _wt(_sha: str, _topo: str) -> object:
        yield str(tmp_path)

    monkeypatch.setattr(mod, "worktree_checks", _wt)
    recebidos: dict[str, object] = {}

    def _falso(checks: object, tmp: str, timeout: int,
              pulos: object = None, **kw: object) -> tuple[bool, str]:
        recebidos["checks"] = checks
        recebidos["rotulo"] = kw.get("rotulo_log")
        return True, ""

    monkeypatch.setattr(mod, "_executar_checks", _falso)
    assert mod.rodar_checks_rapidos(sha, str(repo), base) == (True, "")
    assert recebidos["checks"] == [
        ("", ["mvn", "-B", "-q", "test-compile"], "", True)]
    assert recebidos["rotulo"] == "check-rapido"


def test_executar_checks_rotulo_log(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O prefixo do log de execução segue `rotulo_log`."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    checks = [("", ["true"], "", True)]
    assert mod._executar_checks(checks, str(tmp_path), 60, []) == (True, "")
    assert any(m.startswith("check exit=") for m in logs)
    logs.clear()
    assert mod._executar_checks(
        checks, str(tmp_path), 60, [],
        rotulo_log="check-rapido") == (True, "")
    assert any(m.startswith("check-rapido exit=") for m in logs)
    assert not any(m.startswith("check exit=") for m in logs)
