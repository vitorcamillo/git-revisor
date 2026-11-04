"""Testes do lock de checks ocupado (CHECK_OCUPADO)."""
from __future__ import annotations

import fcntl
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


def _proibido(nome: str):
    """Função que falha o teste se chamada."""
    def _chamada(*_args: object, **_kwargs: object):
        raise AssertionError(f"{nome} não deveria ser chamado")
    return _chamada


def _gravadora(chamadas: list[str], nome: str, resultado: object):
    """Mock que registra a chamada e devolve `resultado`."""
    def _chamada(*_args: object, **_kwargs: object):
        chamadas.append(nome)
        return resultado
    return _chamada


def _base(repo: Path, monkeypatch: pytest.MonkeyPatch, chamadas: list[str],
          logs: list[str]) -> None:
    """Mocks comuns: log/notificação capturados e IA sem autorrevisão."""
    monkeypatch.setattr(mod, "log", logs.append)
    monkeypatch.setattr(mod, "notificar", lambda *a, **k: None)
    monkeypatch.setattr(mod, "autorrevisao", lambda *a, **k: False)
    monkeypatch.setattr(mod, "gitleaks_intervalo", _gravadora(chamadas, "gitleaks", 0))


def _ocupado() -> tuple[bool, str]:
    """Resultado de `rodar_checks` com o lock ocupado."""
    return False, f"{mod.CHECK_OCUPADO} por mais de 0 min"


# ---------------------------------------------------------- rodar_checks


def test_rodar_checks_lock_ocupado_real(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock real ocupado: ok é False, relatório é ocupado, sem cache."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    monkeypatch.setattr(mod, "TEMPO_ESPERA_CHECK", 0.3)
    _git(repo, "config", "revisor.check", "true")
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    pasta_cache = mod.base_dir() / "checks-ok"
    antes = set(pasta_cache.glob("*")) if pasta_cache.exists() else set()
    caminho = mod.arquivo_trava_checks()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w") as segura:
        fcntl.flock(segura, fcntl.LOCK_EX)
        ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert mod.check_ocupado(rel)
    depois = set(pasta_cache.glob("*")) if pasta_cache.exists() else set()
    assert depois == antes


# ---------------------------------------------------------- check_ocupado


def test_check_ocupado_textos() -> None:
    """Só o prefixo CHECK_OCUPADO é ocupado; motivo tem texto próprio."""
    assert mod.check_ocupado(mod.CHECK_OCUPADO + " por mais de 30 min")
    assert not mod.check_ocupado("")
    assert not mod.check_ocupado("check quebrou")
    assert not mod.check_ocupado("CHECK_OCUPADO")
    assert (mod.motivo_veredito("CHECK_OCUPADO")
            == "lock de checks ocupado; check completo não rodou")


# ---------------------------------------------------------- revisar_intervalo


def _montar_intervalo(repo: Path, monkeypatch: pytest.MonkeyPatch,
                      chamadas: list[str], logs: list[str], *,
                      verifica: object, rapido: object, ia: object,
                      risco: object = ("normal", [])) -> list[tuple]:
    """Mocks comuns de revisar_intervalo; devolve a lista de correções."""
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", risco))
    monkeypatch.setattr(mod, "rodar_checks", verifica)
    monkeypatch.setattr(mod, "rodar_checks_rapidos", rapido)
    monkeypatch.setattr(mod, "revisar_diff", ia)
    caminhos: list[tuple] = []
    monkeypatch.setattr(mod, "caminho_correcao_push",
                        lambda *args, **kw: caminhos.append(args) or 1)
    return caminhos


def test_intervalo_risco_baixo_ocupado_bloqueia_sem_salvar(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture) -> None:
    """Risco baixo com lock ocupado: 1, sem aprovar, sem correção, sem relatório."""
    chamadas: list[str] = []
    logs: list[str] = []
    caminhos = _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", _ocupado()),
        rapido=_proibido("rodar_checks_rapidos"),
        ia=_proibido("revisar_diff"),
        risco=("baixo", []))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 1
    assert not mod.esta_aprovado(sha)
    assert caminhos == []
    assert mod.ler_relatorio(sha) is None
    saida = capsys.readouterr().out
    assert "revisor: " in saida and "REVISOR_PULAR=1 git push" in saida


def test_intervalo_pos_ia_ocupado_bloqueia_sem_salvar(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture) -> None:
    """IA APROVADO com completo ocupado: 1, sem aprovar nem corrigir."""
    chamadas: list[str] = []
    logs: list[str] = []
    caminhos = _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", _ocupado()),
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 1
    assert not mod.esta_aprovado(sha)
    assert caminhos == []
    assert mod.ler_relatorio(sha) is None
    saida = capsys.readouterr().out
    assert "revisor: " in saida and "REVISOR_PULAR=1 git push" in saida


# ---------------------------------------------------------- revisar_com_trava


def test_com_trava_ocupado_marca_inconclusivo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo ocupado: inconclusivo, sem correção."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _gravadora(chamadas, "rapido", (True, "")))
    monkeypatch.setattr(mod, "revisar_diff",
                        _gravadora(chamadas, "IA",
                                   ("APROVADO", "tudo certo", {"codex"})))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    monkeypatch.setattr(mod, "fluxo_correcao", _proibido("fluxo_correcao"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 0
    assert mod.esta_inconclusivo(sha)
    assert not mod.esta_aprovado(sha)
    assert "fluxo" not in chamadas


# ---------------------------------------------------------- revisar_lote


def test_lote_ocupado_marca_todos_inconclusivos(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo ocupado no lote: todos inconclusivos."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _gravadora(chamadas, "rapido", (True, "")))
    monkeypatch.setattr(mod, "revisar_diff",
                        _gravadora(chamadas, "IA",
                                   ("APROVADO", "tudo certo", {"codex"})))
    monkeypatch.setattr(mod, "fluxo_correcao", _proibido("fluxo_correcao"))
    sha1 = _commit(repo, {"a.py": "x = 1\n"}, "feat: a")
    sha2 = _commit(repo, {"b.py": "x = 2\n"}, "feat: b")
    assert mod.revisar_lote([sha1, sha2], str(repo)) == 0
    assert mod.esta_inconclusivo(sha1) and mod.esta_inconclusivo(sha2)
    assert not mod.esta_aprovado(sha1) and not mod.esta_aprovado(sha2)
    assert "fluxo" not in chamadas


# ---------------------------------------------------------- fluxo_correcao


def test_fluxo_ocupado_guarda_tentativa_sem_nova_rodada(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Completo ocupado após IA aprovar a correção: guarda, devolve 1."""
    chamadas: list[str] = []
    logs: list[str] = []
    avisos: list[tuple] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "notificar",
                        lambda *args, **kw: avisos.append(args))
    monkeypatch.setattr(mod, "rodadas_max", lambda: 2)
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _gravadora(chamadas, "rapido", (True, "")))
    monkeypatch.setattr(mod, "revisar_por_partes",
                        _gravadora(chamadas, "IA",
                                   ("APROVADO", "tudo certo", {"codex"})))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    tentativas = [0]

    def _tentativa(_original: str, _atual: str, _relatorio: str,
                   _topo: str) -> tuple[str, str]:
        tentativas[0] += 1
        novo = _commit(repo, {"correcao.txt": "1\n"}, "fix: correcao")
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    assert tentativas[0] == 1
    novo = mod.retomar_tentativa(sha, str(repo))
    assert novo is not None and mod.ler_relatorio(novo) is None
    assert not mod.esta_aprovado(sha)
    assert avisos and avisos[0][0] == "Check adiado"


# ---------------------------------------------------------- fatia C3


def test_c3_rodar_checks_auto_lock_ocupado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detecção automática com lock real ocupado: ok False e relatório ocupado."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    monkeypatch.setattr(mod, "TEMPO_ESPERA_CHECK", 0)
    monkeypatch.setattr(
        mod, "detectar_checks",
        lambda *a, **k: [("", ["sh", "-c", "exit 1"], "")])
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    with mod.trava(None, arquivo=mod.arquivo_trava_checks()) as livre:
        assert livre
        ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert mod.check_ocupado(rel)


def test_c3_com_trava_risco_baixo_ocupado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Risco baixo com completo ocupado: 0, inconclusivo, sem correção."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("baixo", [])))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _proibido("rodar_checks_rapidos"))
    monkeypatch.setattr(mod, "revisar_diff", _proibido("revisar_diff"))
    monkeypatch.setattr(mod, "fluxo_correcao", _proibido("fluxo_correcao"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 0
    assert mod.esta_inconclusivo(sha)
    assert not mod.esta_aprovado(sha)
    assert "fluxo" not in chamadas


def test_c3_com_trava_check_primeiro_ocupado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check-primeiro com risco normal e completo ocupado: 0, inconclusivo."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _proibido("rodar_checks_rapidos"))
    monkeypatch.setattr(mod, "revisar_diff", _proibido("revisar_diff"))
    monkeypatch.setattr(mod, "fluxo_correcao", _proibido("fluxo_correcao"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 0
    assert mod.esta_inconclusivo(sha)
    assert not mod.esta_aprovado(sha)
    assert "fluxo" not in chamadas


def test_c3_intervalo_check_primeiro_ocupado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check-primeiro no push com completo ocupado: 1, sem aprovar nem salvar."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _proibido("rodar_checks_rapidos"))
    monkeypatch.setattr(mod, "revisar_diff", _proibido("revisar_diff"))
    caminhos: list[tuple] = []
    monkeypatch.setattr(mod, "caminho_correcao_push",
                        lambda *args, **kw: caminhos.append(args) or 1)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 1
    assert not mod.esta_aprovado(sha)
    assert mod.ler_relatorio(sha) is None
    assert caminhos == []


def test_c3_fluxo_retomada_ocupado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retomada com IA APROVADO e completo ocupado: 1, mantém ref, sem worker."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "revisar_por_partes",
                        _gravadora(chamadas, "IA",
                                   ("APROVADO", "tudo certo", {"codex"})))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    monkeypatch.setattr(mod, "tentativa_correcao",
                        _proibido("tentativa_correcao"))
    sha_original = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    anterior = _commit(repo, {"app.py": "x = 2\n"}, "fix: correcao guardada")
    assert mod.guardar_tentativa(sha_original, anterior, str(repo))
    assert mod.fluxo_correcao(sha_original, "relatório inicial",
                              str(repo)) == 1
    assert mod.retomar_tentativa(sha_original, str(repo)) == anterior
    assert "tentativa_correcao" not in chamadas
    assert mod.CHECK_OCUPADO not in (mod.ler_relatorio(anterior) or "")


def test_c3_fluxo_check_primeiro_ocupado_guarda(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check-primeiro: worker gera novo, completo ocupado guarda e não chama IA."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodadas_max", lambda: 2)
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", _ocupado()))
    monkeypatch.setattr(mod, "revisar_por_partes",
                        _proibido("revisar_por_partes"))

    def _tentativa(_original: str, _atual: str, _relatorio: str,
                   _topo: str) -> tuple[str, str]:
        chamadas.append("tentativa")
        novo = _commit(repo, {"correcao.txt": "1\n"}, "fix: correcao")
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    novo = mod.retomar_tentativa(sha, str(repo))
    assert novo is not None and novo != sha
    assert "IA" not in chamadas


def test_c3_selecionar_rapido_teste_direto() -> None:
    """Arquivo *_test.py alterado entra direto no comando pytest."""
    comando = mod.selecionar_teste_rapido(
        "", ["python3", "-m", "pytest", "-q"], ["pkg/foo_test.py"])
    assert comando is not None
    assert comando[2] == "pytest" and "pkg/foo_test.py" in comando


def test_c3_selecionar_rapido_mapeia_modulo(tmp_path: Path) -> None:
    """Módulo alterado mapeia para o teste; __init__ sozinho não mapeia."""
    pasta = tmp_path / "proj"
    (pasta / "tests").mkdir(parents=True)
    (pasta / "tests" / "test_modulo_x.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8")
    (pasta / "src").mkdir(parents=True)
    (pasta / "src" / "modulo_x.py").write_text("x = 1\n", encoding="utf-8")
    comando = mod.selecionar_teste_rapido(
        "", ["python3", "-m", "pytest", "-q"], ["src/modulo_x.py"],
        raiz=str(pasta))
    assert comando is not None
    assert "tests/test_modulo_x.py" in comando
    so_init = mod.selecionar_teste_rapido(
        "", ["python3", "-m", "pytest", "-q"], ["src/__init__.py"],
        raiz=str(pasta))
    assert so_init is not None
    assert "tests/test_modulo_x.py" not in so_init


def test_c3_rapidos_filtro_cobertura(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check fora da cobertura dos alterados: (True, '') sem executar."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    monkeypatch.setattr(
        mod, "detectar_checks",
        lambda *a, **k: [("sub", ["mvn", "-B", "-q", "test"], "sub")])  # mvn sem filtro viraria test-compile
    monkeypatch.setattr(mod, "_executar_checks",
                        _proibido("_executar_checks"))
    _commit(repo, {"base.txt": "0\n"}, "feat: base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    sha = _commit(repo, {"outro/a.py": "x = 1\n"}, "feat: outro")
    assert mod.rodar_checks_rapidos(sha, str(repo), base) == (True, "")


def test_c3_rapidos_mvn_selecao(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual mvn com Foo.java alterado seleciona -Dtest=Foo*Test,Foo*Tests."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    _git(repo, "config", "--add", "revisor.check", "mvn -B -q test")
    capturados: list[list] = []
    chamadas: list[str] = []

    def _exec(checks: object, *args: object, **kw: object) -> tuple[bool, str]:
        chamadas.append("exec")
        capturados.append(checks)  # type: ignore[arg-type]
        return True, ""

    monkeypatch.setattr(mod, "_executar_checks", _exec)
    _commit(repo, {"base.txt": "0\n"}, "feat: base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    sha = _commit(repo, {"src/main/java/a/Foo.java": "class Foo {}\n"},
                  "feat: foo")
    assert mod.rodar_checks_rapidos(sha, str(repo), base) == (True, "")
    assert chamadas == ["exec"]
    assert capturados and "-Dtest=Foo*Test,Foo*Tests" in capturados[0][0][1]


def test_c3_rapidos_lock_ocupado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock ocupado no rápido: (True, '') sem executar."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    monkeypatch.setattr(mod, "TEMPO_ESPERA_CHECK_RAPIDO", 0)
    _git(repo, "config", "revisor.checkRapidoCmd", "true")
    monkeypatch.setattr(mod, "_executar_checks",
                        _proibido("_executar_checks"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    base = mod.pai1_ou_vazio(sha)
    with mod.trava(None, arquivo=mod.arquivo_trava_checks()) as livre:
        assert livre
        assert mod.rodar_checks_rapidos(sha, str(repo), base) == (True, "")


# ---------------------------------------------------------- fatia D


def test_d_fluxo_checks_usam_base_original(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Correção integrada: todo rodar_checks recebe a base do lote inteiro."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "rodadas_max", lambda: 2)
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _gravadora(chamadas, "rapido", (True, "")))
    monkeypatch.setattr(mod, "revisar_por_partes",
                        _gravadora(chamadas, "IA",
                                   ("APROVADO", "tudo certo", {"codex"})))
    bases: list[object] = []

    def _verifica(sha: str, topo: str, **kw: object) -> tuple[bool, str]:
        chamadas.append("completo")
        bases.append(kw.get("base"))
        return True, ""

    monkeypatch.setattr(mod, "rodar_checks", _verifica)
    monkeypatch.setattr(mod, "aprovar_correcao",
                        lambda *a, **k: chamadas.append("aprovar"))
    monkeypatch.setattr(mod, "integrar_correcao",
                        lambda *a, **k: chamadas.append("integrar") or 0)

    def _tentativa(_original: str, _atual: str, _relatorio: str,
                   _topo: str) -> tuple[str, str]:
        novo = _commit(repo, {"correcao.txt": "1\n"}, "fix: correcao")
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 0
    esperada = mod.pai1_ou_vazio(sha)
    assert "completo" in chamadas
    assert bases and all(b == esperada for b in bases)


def test_d_rapidos_ignora_arquivo_apagado(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check rápido não passa arquivo apagado ao pytest (exit 4)."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    _git(repo, "config", "--add", "revisor.check", "python3 -m pytest -q")
    _commit(repo, {"tests/test_velho.py": "def test_v():\n    assert True\n",
                   "src/m.py": "x = 1\n"}, "feat: base")
    (repo / "tests" / "test_velho.py").unlink()
    (repo / "src" / "m.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: apaga teste velho")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    base = mod.pai1_ou_vazio(sha)
    capturados: list[list] = []

    def _exec(checks: object, *args: object, **kw: object) -> tuple[bool, str]:
        capturados.append(checks)  # type: ignore[arg-type]
        return True, ""

    monkeypatch.setattr(mod, "_executar_checks", _exec)
    assert mod.rodar_checks_rapidos(sha, str(repo), base) == (True, "")
    assert capturados
    for lote in capturados:
        for _prefixo, comando, _cwd, _manual in lote:
            assert not any("test_velho" in parte for parte in comando)


# ---------------------------------------------------------- fatia E


def test_e_mvn_com_arg_extra_nao_seleciona() -> None:
    """mvn com argumento fora do conjunto simples: sem seleção (None)."""
    assert mod.selecionar_teste_rapido(
        "", ["mvn", "-f", "backend/pom.xml", "test"],
        ["backend/src/main/java/a/Foo.java"]) is None


def test_e_mvn_simples_seleciona_dtest() -> None:
    """mvn simples continua selecionando -Dtest com os padrões."""
    comando = mod.selecionar_teste_rapido(
        "", ["mvn", "-B", "-q", "test"], ["src/main/java/a/Foo.java"])
    assert comando is not None
    assert "-Dtest=Foo*Test,Foo*Tests" in comando


def test_e_pytest_com_plugin_nao_seleciona() -> None:
    """pytest com argumento extra (ex.: -p) não é simplificado: None."""
    assert mod.selecionar_teste_rapido(
        "", ["python3", "-m", "pytest", "-q", "-p", "no:x"],
        ["test_a.py"]) is None


def test_e_pytest_simples_inclui_teste() -> None:
    """pytest simples continua incluindo o arquivo de teste."""
    comando = mod.selecionar_teste_rapido(
        "", ["python3", "-m", "pytest", "-q"], ["test_a.py"])
    assert comando is not None
    assert "test_a.py" in comando
