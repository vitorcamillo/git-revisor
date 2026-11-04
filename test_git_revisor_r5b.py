"""Testes da ordem ia-primeiro: check rápido/completo antes e depois da IA."""
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


# ---------------------------------------------------------- ordem_revisao

def test_ordem_revisao_padroes(repo: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Ausente vira ia-primeiro; check-primeiro exato é mantido."""
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    assert mod.ordem_revisao(str(repo)) == "ia-primeiro"
    assert logs == []
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    assert mod.ordem_revisao(str(repo)) == "check-primeiro"
    assert logs == []
    _git(repo, "config", "revisor.ordem", "xpto")
    assert mod.ordem_revisao(str(repo)) == "ia-primeiro"
    assert logs == ["revisor.ordem inválido: xpto; usando ia-primeiro"]


# ------------------------------------------------- revisar_com_trava

def test_com_trava_ia_reprovada_evita_check(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ia-primeiro com IA REPROVADO: completo não roda e o log aparece."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks",
                        _proibido("rodar_checks completo"))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _gravadora(chamadas, "rapido", (True, "")))
    monkeypatch.setattr(mod, "revisar_diff",
                        _gravadora(chamadas, "IA",
                                   ("REPROVADO", "achado grave", {"codex"})))
    monkeypatch.setattr(mod, "fluxo_correcao",
                        _gravadora(chamadas, "fluxo", 1))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 1
    assert "completo" not in chamadas
    assert "check completo evitado: IA REPROVADO antes do check" in logs
    assert mod.ler_relatorio(sha) == "achado grave"


def test_com_trava_ia_aprovada_completo_falha(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo falho: nada é aprovado; fluxo pega o relatório."""
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
                        _gravadora(chamadas, "completo", (False, "check quebrou")))
    recebidos: list[tuple] = []
    monkeypatch.setattr(mod, "fluxo_correcao",
                        lambda *args, **kw: recebidos.append(args) or 1)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 1
    assert not mod.esta_aprovado(sha)
    assert recebidos[0][1] == "check quebrou"
    assert "check completo evitado" not in logs


def test_com_trava_ia_aprovada_completo_verde_aprova(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo verde: aprova como hoje."""
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
                        _gravadora(chamadas, "completo", (True, "")))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 0
    assert mod.esta_aprovado(sha)
    assert chamadas.count("completo") == 1


def test_com_trava_rapido_falho_nao_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check rápido falho reprova com o relatório dele, sem IA nem completo."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _gravadora(chamadas, "rapido", (False, "teste falhou")))
    monkeypatch.setattr(mod, "revisar_diff", _proibido("revisar_diff"))
    monkeypatch.setattr(mod, "rodar_checks", _proibido("rodar_checks completo"))
    recebidos: list[tuple] = []
    monkeypatch.setattr(mod, "fluxo_correcao",
                        lambda *args, **kw: recebidos.append(args) or 1)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 1
    assert "IA" not in chamadas and "completo" not in chamadas
    assert recebidos[0][1] == "teste falhou"


def test_com_trava_risco_baixo_so_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Risco baixo: completo decide; IA e rápido não são chamados."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("baixo", [])))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _proibido("rodar_checks_rapidos"))
    monkeypatch.setattr(mod, "revisar_diff", _proibido("revisar_diff"))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", (True, "")))
    monkeypatch.setattr(mod, "fluxo_correcao",
                        _gravadora(chamadas, "fluxo", 1))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 0
    assert mod.esta_aprovado(sha)
    marcador = mod._marcador_aprovado(sha)
    assert marcador is not None and marcador.get("familia") == "regra"

    logs.clear()
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", (False, "check quebrou")))
    sha2 = _commit(repo, {"app.py": "x = 2\n"}, "feat: app 2")
    assert mod.revisar_com_trava(sha2) == 1
    assert not mod.esta_aprovado(sha2)


# ---------------------------------------------------------- revisar_intervalo

def _montar_intervalo(repo: Path, monkeypatch: pytest.MonkeyPatch,
                      chamadas: list[str], logs: list[str], *,
                      verifica: object, rapido: object, ia: object) -> list[tuple]:
    """Mocks comuns de revisar_intervalo; devolve a lista de fluxos acionados."""
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks", verifica)
    monkeypatch.setattr(mod, "rodar_checks_rapidos", rapido)
    monkeypatch.setattr(mod, "revisar_diff", ia)
    caminhos: list[tuple] = []
    monkeypatch.setattr(mod, "caminho_correcao_push",
                        lambda *args, **kw: caminhos.append(args) or 1)
    return caminhos


def test_intervalo_ia_reprovada_evita_check(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ia-primeiro com IA REPROVADO no push: completo não roda."""
    chamadas: list[str] = []
    logs: list[str] = []
    caminhos = _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_proibido("rodar_checks completo"),
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA",
                      ("REPROVADO", "achado grave", {"codex"})))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 1
    assert "completo" not in chamadas
    assert "check completo evitado: IA REPROVADO antes do check" in logs
    assert caminhos[0][2] == "achado grave"


def test_intervalo_ia_aprovada_completo_falha(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo falho: push é tratado como REPROVADO."""
    chamadas: list[str] = []
    logs: list[str] = []
    caminhos = _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", (False, "check quebrou")),
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 1
    assert not mod.esta_aprovado(sha)
    assert caminhos[0][2] == "check quebrou"
    assert "check completo evitado" not in logs


def test_intervalo_ia_aprovada_completo_verde_aprova(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo verde: push aprovado como hoje."""
    chamadas: list[str] = []
    logs: list[str] = []
    _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", (True, "")),
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 0
    assert mod.esta_aprovado(sha)


def test_intervalo_rapido_falho_nao_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check rápido falho no push reprova sem IA nem completo."""
    chamadas: list[str] = []
    logs: list[str] = []
    caminhos = _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_proibido("rodar_checks completo"),
        rapido=_gravadora(chamadas, "rapido", (False, "teste falhou")),
        ia=_proibido("revisar_diff"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 1
    assert "IA" not in chamadas and "completo" not in chamadas
    assert mod.ler_relatorio(sha) == "teste falhou"
    assert caminhos == []


def test_intervalo_risco_baixo_so_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Risco baixo no push: completo roda, IA não; aprovação só com verde."""
    chamadas: list[str] = []
    logs: list[str] = []
    _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", (True, "")),
        rapido=_proibido("rodar_checks_rapidos"),
        ia=_proibido("revisar_diff"))
    monkeypatch.setattr(mod, "classificar_risco", lambda *a, **k: ("baixo", []))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 0
    assert mod.esta_aprovado(sha)

    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", (False, "check quebrou")))
    sha2 = _commit(repo, {"app.py": "x = 2\n"}, "feat: app 2")
    assert mod.revisar_intervalo([sha2], sha2, str(repo)) == 1
    assert not mod.esta_aprovado(sha2)


def test_intervalo_grupo_aprovado_exige_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GRANDE_DEMAIS com grupos: um completo só, na ponta do push."""
    chamadas: list[str] = []
    logs: list[str] = []
    verificados: list[str] = []
    resultado_check = [True, ""]
    total_ia = [0]

    def _checks(sha_ver: str, _topo: str, **_kw: object) -> tuple[bool, str]:
        """Registra o sha verificado pelo completo e devolve o resultado."""
        verificados.append(sha_ver)
        return resultado_check[0], resultado_check[1]

    def _ia(*_args: object, **_kwargs: object) -> tuple:
        """1ª chamada (push inteiro) é GRANDE_DEMAIS; grupos são APROVADO."""
        total_ia[0] += 1
        chamadas.append("IA")
        if total_ia[0] == 1:
            return ("GRANDE_DEMAIS", "grande demais", set())
        return ("APROVADO", "tudo certo", {"codex"})

    _montar_intervalo(
        repo, monkeypatch, chamadas, logs,
        verifica=_checks,
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_ia)
    monkeypatch.setattr(mod, "grupos_do_push",
                        lambda novos: [[sha] for sha in novos])
    sha1 = _commit(repo, {"a.py": "x = 1\n"}, "feat: a")
    sha2 = _commit(repo, {"b.py": "x = 2\n"}, "feat: b")
    assert mod.revisar_intervalo([sha1, sha2], sha2, str(repo)) == 0
    assert verificados == [sha2]  # completo único, no local_sha do push
    assert mod.esta_aprovado(sha1) and mod.esta_aprovado(sha2)
    assert total_ia[0] == 3  # push inteiro + 2 grupos

    chamadas.clear()
    verificados.clear()
    total_ia[0] = 0
    resultado_check[0], resultado_check[1] = False, "check quebrou"
    sha3 = _commit(repo, {"c.py": "x = 3\n"}, "feat: c")
    sha4 = _commit(repo, {"d.py": "x = 4\n"}, "feat: d")
    assert mod.revisar_intervalo([sha3, sha4], sha4, str(repo)) == 1
    assert verificados == [sha4]
    assert total_ia[0] == 1  # completo falhou; a IA dos grupos não é chamada
    assert not mod.esta_aprovado(sha3) and not mod.esta_aprovado(sha4)
    assert mod.ler_relatorio(sha3) == "check quebrou"


# ---------------------------------------------------------- revisar_lote

def _montar_lote(repo: Path, monkeypatch: pytest.MonkeyPatch,
                 chamadas: list[str], logs: list[str], *,
                 verifica: object, rapido: object, ia: object) -> list[tuple]:
    """Mocks comuns de revisar_lote; devolve a lista de fluxos acionados."""
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks", verifica)
    monkeypatch.setattr(mod, "rodar_checks_rapidos", rapido)
    monkeypatch.setattr(mod, "revisar_diff", ia)
    fluxos: list[tuple] = []
    monkeypatch.setattr(mod, "fluxo_correcao",
                        lambda *args, **kw: fluxos.append(args) or 1)
    return fluxos


def test_lote_ia_reprovada_evita_check(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ia-primeiro com IA REPROVADO no lote: completo não roda."""
    chamadas: list[str] = []
    logs: list[str] = []
    fluxos = _montar_lote(
        repo, monkeypatch, chamadas, logs,
        verifica=_proibido("rodar_checks completo"),
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA",
                      ("REPROVADO", "achado grave", {"codex"})))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_lote([sha], str(repo)) == 1
    assert "completo" not in chamadas
    assert "check completo evitado: IA REPROVADO antes do check" in logs
    assert fluxos[0][1] == "achado grave"


def test_lote_ia_aprovada_completo_falha(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo falho no lote: fluxo recebe o relatório do check."""
    chamadas: list[str] = []
    logs: list[str] = []
    fluxos = _montar_lote(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", (False, "check quebrou")),
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_lote([sha], str(repo)) == 1
    assert not mod.esta_aprovado(sha)
    assert fluxos[0][1] == "check quebrou"


def test_lote_ia_aprovada_completo_verde_aprova(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo verde no lote: aprova como hoje."""
    chamadas: list[str] = []
    logs: list[str] = []
    _montar_lote(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", (True, "")),
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_lote([sha], str(repo)) == 0
    assert mod.esta_aprovado(sha)


def test_lote_rapido_falho_nao_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check rápido falho no lote reprova sem IA nem completo."""
    chamadas: list[str] = []
    logs: list[str] = []
    fluxos = _montar_lote(
        repo, monkeypatch, chamadas, logs,
        verifica=_proibido("rodar_checks completo"),
        rapido=_gravadora(chamadas, "rapido", (False, "teste falhou")),
        ia=_proibido("revisar_diff"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_lote([sha], str(repo)) == 1
    assert "IA" not in chamadas and "completo" not in chamadas
    assert fluxos[0][1] == "teste falhou"


def test_lote_risco_baixo_so_completo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Risco baixo no lote: completo roda, IA não; aprovação só com verde."""
    chamadas: list[str] = []
    logs: list[str] = []
    _montar_lote(
        repo, monkeypatch, chamadas, logs,
        verifica=_gravadora(chamadas, "completo", (True, "")),
        rapido=_proibido("rodar_checks_rapidos"),
        ia=_proibido("revisar_diff"))
    monkeypatch.setattr(mod, "classificar_risco", lambda *a, **k: ("baixo", []))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_lote([sha], str(repo)) == 0
    assert mod.esta_aprovado(sha)

    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", (False, "check quebrou")))
    sha2 = _commit(repo, {"app.py": "x = 2\n"}, "feat: app 2")
    assert mod.revisar_lote([sha2], str(repo)) == 1
    assert not mod.esta_aprovado(sha2)


# ---------------------------------------------------------- fluxo_correcao

def _montar_fluxo(repo: Path, monkeypatch: pytest.MonkeyPatch,
                  chamadas: list[str], logs: list[str], *,
                  rapido: object, ia: object,
                  verifica: object | None = None) -> list[str]:
    """Mocks comuns de fluxo_correcao; devolve os relatórios das tentativas."""
    _base(repo, monkeypatch, chamadas, logs)
    monkeypatch.setattr(mod, "rodadas_max", lambda: 2)
    monkeypatch.setattr(mod, "rodar_checks_rapidos", rapido)
    monkeypatch.setattr(mod, "revisar_por_partes", ia)
    if verifica is not None:
        monkeypatch.setattr(mod, "rodar_checks", verifica)
    recebidos: list[str] = []

    def _tentativa(_original: str, _atual: str, relatorio: str,
                   _topo: str) -> tuple[str, str]:
        recebidos.append(relatorio)
        numero = len(recebidos)
        novo = _commit(repo, {f"correcao{numero}.txt": f"{numero}\n"},
                       f"fix: correcao {numero}")
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    return recebidos


def test_fluxo_ia_reprovada_evita_check(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ia-primeiro no laço de correção: completo não roda quando a IA reprova."""
    chamadas: list[str] = []
    logs: list[str] = []
    _montar_fluxo(
        repo, monkeypatch, chamadas, logs,
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA",
                      ("REPROVADO", "achado grave", {"codex"})),
        verifica=_proibido("rodar_checks completo"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    assert "completo" not in chamadas
    assert "check completo evitado: IA REPROVADO antes do check" in logs


def test_fluxo_ia_aprovada_completo_falha_repassa_relatorio(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Completo falho após IA APROVADO: próxima tentativa recebe o relatório."""
    chamadas: list[str] = []
    logs: list[str] = []
    recebidos = _montar_fluxo(
        repo, monkeypatch, chamadas, logs,
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})),
        verifica=_gravadora(chamadas, "completo", (False, "check quebrou")))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    assert not mod.esta_aprovado(sha)
    assert recebidos == ["relatório inicial", "check quebrou"]
    assert "check completo evitado" not in logs


def test_fluxo_ia_aprovada_completo_verde_integra(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """IA APROVADO com completo verde: integra como hoje."""
    chamadas: list[str] = []
    logs: list[str] = []
    _montar_fluxo(
        repo, monkeypatch, chamadas, logs,
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})),
        verifica=_gravadora(chamadas, "completo", (True, "")))
    integra: list[tuple] = []
    monkeypatch.setattr(mod, "integrar_correcao",
                        lambda *args, **kw: integra.append(args) or 0)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 0
    assert integra[0][0] == sha
    assert integra[0][1] != sha  # correção nova foi a aprovada


def test_fluxo_rapido_falho_nao_chama_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check rápido falho no laço: IA não é chamada; relatório vira da próxima."""
    chamadas: list[str] = []
    logs: list[str] = []
    recebidos = _montar_fluxo(
        repo, monkeypatch, chamadas, logs,
        rapido=_gravadora(chamadas, "rapido", (False, "teste falhou")),
        ia=_proibido("revisar_por_partes"),
        verifica=_proibido("rodar_checks completo"))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 1
    assert "IA" not in chamadas and "completo" not in chamadas
    assert recebidos == ["relatório inicial", "teste falhou"]


def test_fluxo_retomada_completo_antes_de_integrar(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retomada APROVADO: completo roda antes; falho, cai em tentativa_correcao."""
    chamadas: list[str] = []
    logs: list[str] = []
    _montar_fluxo(
        repo, monkeypatch, chamadas, logs,
        rapido=_gravadora(chamadas, "rapido", (True, "")),
        ia=_gravadora(chamadas, "IA", ("APROVADO", "tudo certo", {"codex"})))
    verificados: list[str] = []

    def _checks(sha_ver: str, _topo: str, **_kw: object) -> tuple[bool, str]:
        """Registra o sha verificado pelo completo e o aprova."""
        verificados.append(sha_ver)
        return True, ""

    monkeypatch.setattr(mod, "rodar_checks", _checks)
    integra: list[tuple] = []
    monkeypatch.setattr(mod, "integrar_correcao",
                        lambda *args, **kw: integra.append(args) or 0)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    anterior = _commit(repo, {"app.py": "x = 2\n"}, "fix: correcao guardada")
    assert mod.guardar_tentativa(sha, anterior, str(repo))
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 0
    assert verificados == [anterior]
    assert len(integra) == 1 and integra[0][0] == sha

    chamadas.clear()
    verificados.clear()
    recebidos: list[str] = []

    def _tentativa(_original: str, _atual: str, relatorio: str,
                   _topo: str) -> tuple[str, str]:
        recebidos.append(relatorio)
        novo = _commit(repo, {"correcao2.txt": "1\n"}, "fix: correcao 2")
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    verificados.clear()
    restam_falhas = [1]

    def _checks_falho(sha_ver: str, _topo: str, **_kw: object) -> tuple[bool, str]:
        """Falha só no primeiro completo (retomada); depois aprova."""
        verificados.append(sha_ver)
        if restam_falhas[0]:
            restam_falhas[0] = 0
            return False, "check quebrou"
        return True, ""

    monkeypatch.setattr(mod, "rodar_checks", _checks_falho)
    sha2 = _commit(repo, {"app.py": "y = 1\n"}, "feat: app 2")
    anterior2 = _commit(repo, {"app.py": "y = 2\n"}, "fix: correcao guardada 2")
    assert mod.guardar_tentativa(sha2, anterior2, str(repo))
    assert mod.fluxo_correcao(sha2, "relatório inicial", str(repo)) == 0
    assert len(verificados) == 2 and verificados[0] == anterior2 and verificados[1] != anterior2
    assert recebidos == ["check quebrou"]


# ---------------------------------------------------------- ordem check-primeiro

def test_ordem_check_primeiro_mantem_ordem_antiga(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check-primeiro: [completo, IA], sem rápido, nos 4 pontos."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks",
                        _gravadora(chamadas, "completo", (True, "")))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _proibido("rodar_checks_rapidos"))
    monkeypatch.setattr(mod, "revisar_diff",
                        _gravadora(chamadas, "IA",
                                   ("APROVADO", "tudo certo", {"codex"})))
    monkeypatch.setattr(mod, "revisar_por_partes",
                        _gravadora(chamadas, "IA",
                                   ("APROVADO", "tudo certo", {"codex"})))
    monkeypatch.setattr(mod, "integrar_correcao", lambda *a, **kw: 0)
    monkeypatch.setattr(mod, "rodadas_max", lambda: 1)

    def _tentativa(_original: str, _atual: str, _relatorio: str,
                   _topo: str) -> tuple[str, str]:
        novo = _commit(repo, {"correcao.txt": "1\n"}, "fix: correcao")
        return novo, "codex"

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")

    assert mod.revisar_com_trava(sha) == 0
    assert mod.revisar_intervalo([sha], sha, str(repo)) == 0
    assert mod.revisar_lote([sha], str(repo)) == 0
    assert mod.fluxo_correcao(sha, "relatório inicial", str(repo)) == 0
    assert "rapido" not in chamadas
    # nos 4 pontos o completo antecede a IA
    primeiro_completo = chamadas.index("completo")
    primeira_ia = chamadas.index("IA")
    assert primeiro_completo < primeira_ia
    assert chamadas.count("completo") == chamadas.count("IA") == 4


# ---------------------------------------------------------- ordem inválida

def test_ordem_invalida_vale_ia_primeiro(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """revisor.ordem=xpto vira ia-primeiro, com log de aviso."""
    chamadas: list[str] = []
    logs: list[str] = []
    _base(repo, monkeypatch, chamadas, logs)
    _git(repo, "config", "revisor.ordem", "xpto")
    monkeypatch.setattr(mod, "classificar_risco",
                        _gravadora(chamadas, "risco", ("normal", [])))
    monkeypatch.setattr(mod, "rodar_checks", _proibido("rodar_checks completo"))
    monkeypatch.setattr(mod, "rodar_checks_rapidos",
                        _gravadora(chamadas, "rapido", (True, "")))
    monkeypatch.setattr(mod, "revisar_diff",
                        _gravadora(chamadas, "IA",
                                   ("REPROVADO", "achado grave", {"codex"})))
    monkeypatch.setattr(mod, "fluxo_correcao",
                        _gravadora(chamadas, "fluxo", 1))
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.revisar_com_trava(sha) == 1
    assert "completo" not in chamadas
    assert "revisor.ordem inválido: xpto; usando ia-primeiro" in logs
    assert "check completo evitado: IA REPROVADO antes do check" in logs
