"""Testes R11: suíte completa em segundo plano após push com check selecionado.

Com `revisor.checkFinal=selecionado`, o push roda só os testes ligados ao
diff; logo depois da aprovação a suíte COMPLETA roda em fundo
(`suite-completa`). Falha real grava `suite-falhou.json` e o PRÓXIMO push
roda o completo até ficar verde. A suíte em fundo cede a vez a um push
(`CHECK_CEDIDO`) e o `retentar-push` que for segurado DE NOVO volta a
esperar dentro do mesmo prazo (correção 4b da R10).
"""
from __future__ import annotations

import importlib.util
import json
import signal
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
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
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


class _Proc:
    """CompletedProcess mínimo para fakes de subprocess."""

    def __init__(self, returncode: int, stdout: str = "",
                 stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _escrever_json(caminho: Path, dados: dict) -> None:
    """Grava um JSON do revisor (1 linha)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False) + "\n",
                       encoding="utf-8")


def _ler_json(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def _interceptar_popen_suite(monkeypatch: pytest.MonkeyPatch,
                             popens: list[list[str]],
                             pid: int = 99_999) -> None:
    """Popen falso SÓ para o disparo `suite-completa`; git segue real."""

    class _FakePopen:
        pass

    _FakePopen.pid = pid

    real_popen = subprocess.Popen

    def _popen_falso(cmd, *args: object, **kw: object):
        if any("suite-completa" in str(parte) for parte in cmd):
            popens.append(list(cmd))
            return _FakePopen
        return real_popen(cmd, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(mod.subprocess, "Popen", _popen_falso)


def _condicoes_livres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Máquina liberada para suite-completa/retentar-push (sem espera)."""
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 99_999)
    monkeypatch.setattr(mod, "lock_checks_ocupado", lambda: False)
    monkeypatch.setattr(mod, "push_esperando", lambda: False)


def _maquina_ocupada(monkeypatch: pytest.MonkeyPatch) -> None:
    """Máquina nunca libera (memória sempre baixa)."""
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 0)
    monkeypatch.setattr(mod, "lock_checks_ocupado", lambda: False)
    monkeypatch.setattr(mod, "push_esperando", lambda: False)


# ---------------------------------------------------- suite_completa_em_cache

def test_suite_completa_em_cache(repo: Path) -> None:
    """Cache completo: manual cobrindo precisa de marcador; sem check, True."""
    sha = _commit(repo, {"NOTES.md": "mudou\n"}, "docs: notas")
    base = mod.pai1_ou_vazio(sha)
    # check manual que não cobre o diff: sem check aplicável → True
    _git(repo, "config", "revisor.check", "@app mvn -B test")
    assert mod.suite_completa_em_cache(sha, str(repo), base) is True
    # check manual cobrindo, sem marcador → False
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    assert mod.suite_completa_em_cache(sha, str(repo), base) is False
    # marcador gravado para a árvore → True
    arvore = mod.arvore_de(sha)
    comando = ["/usr/bin/python3", "-m", "pytest", "-q"]
    marcador = mod.caminho_cache_check(arvore,
                                       mod.chave_check("", comando, ""))
    _escrever_json(marcador, {"quando": mod.agora_iso()})
    assert mod.suite_completa_em_cache(sha, str(repo), base) is True
    # cacheChecks desligado: nunca há cache → False
    _git(repo, "config", "revisor.cacheChecks", "false")
    assert mod.suite_completa_em_cache(sha, str(repo), base) is False


# -------------------------------------------- disparo da suíte em segundo plano

def test_disparo_aprovacao_com_selecionado_grava_json(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Push aprovado com `selecionado` e sem cache completo dispara a suíte."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _commit(repo, {"NOTES.md": "mudou\n"}, "docs: notas")
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    popens: list[list[str]] = []
    _interceptar_popen_suite(monkeypatch, popens)
    monkeypatch.setattr(mod, "gitleaks_intervalo", lambda *a, **k: 0)
    monkeypatch.setattr(mod, "rodar_checks",
                        lambda *a, **k: (True, ""))
    rc = mod.revisar_intervalo([sha], sha, str(repo), [], "origem",
                               "refs/heads/main", "refs/heads/main")
    assert rc == 0
    assert len(popens) == 1
    assert "suite-completa" in popens[0] and sha in popens[0]
    dados = _ler_json(mod.base_dir() / "suite-fundo.json")
    assert dados["sha"] == sha
    assert dados["base"] == mod.pai1_ou_vazio(sha)
    assert dados["pid"] == 99_999
    assert "quando" in dados


def test_disparo_com_cache_completo_ou_sem_selecionado_nao_dispara(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com a árvore inteira em cache (ou sem `selecionado`): nada dispara."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _commit(repo, {"NOTES.md": "mudou\n"}, "docs: notas")
    base = mod.pai1_ou_vazio(sha)
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _git(repo, "config", "revisor.check", "@ /usr/bin/python3 -m pytest -q")
    arvore = mod.arvore_de(sha)
    comando = ["/usr/bin/python3", "-m", "pytest", "-q"]
    marcador = mod.caminho_cache_check(arvore,
                                       mod.chave_check("", comando, ""))
    _escrever_json(marcador, {"quando": mod.agora_iso()})
    popens: list[list[str]] = []
    _interceptar_popen_suite(monkeypatch, popens)
    mod.disparar_suite_fundo_se_preciso([sha], sha, str(repo), base)
    assert popens == []
    assert not (mod.base_dir() / "suite-fundo.json").exists()
    # sem `revisor.checkFinal=selecionado`, também não dispara
    _git(repo, "config", "--unset", "revisor.checkFinal")
    _git(repo, "config", "--unset", "revisor.check")
    mod.disparar_suite_fundo_se_preciso([sha], sha, str(repo), base)
    assert popens == []
    assert not (mod.base_dir() / "suite-fundo.json").exists()


def test_disparo_substitui_antiga_de_outro_sha_e_pula_mesma(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Suíte viva de outro sha é morta (killpg) e substituída; mesma árvore
    com pid vivo não dispara de novo."""
    sha1 = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha2 = _commit(repo, {"a.txt": "2\n"}, "feat: b")
    real_kill = mod.os.kill
    killpgs: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        if pid in (5001, 5002):
            return  # finge que o pid da suíte (antiga e nova) está vivo
        real_kill(pid, sig)

    def fake_killpg(pid: int, sig: int) -> None:
        killpgs.append((pid, sig))

    monkeypatch.setattr(mod.os, "kill", fake_kill)
    monkeypatch.setattr(mod.os, "killpg", fake_killpg)
    popens: list[list[str]] = []
    _interceptar_popen_suite(monkeypatch, popens, pid=5002)
    # suíte antiga de outro sha, viva
    _escrever_json(mod.base_dir() / "suite-fundo.json",
                   {"sha": sha1, "base": "", "pid": 5001,
                    "quando": mod.agora_iso()})
    mod.disparar_suite_completa(sha2, mod.pai1_ou_vazio(sha2), str(repo))
    assert killpgs == [(5001, signal.SIGTERM)]
    assert len(popens) == 1
    dados = _ler_json(mod.base_dir() / "suite-fundo.json")
    assert dados["sha"] == sha2 and dados["pid"] == 5002
    # mesmo sha com pid vivo: não dispara de novo
    popens.clear()
    killpgs.clear()
    mod.disparar_suite_completa(sha2, mod.pai1_ou_vazio(sha2), str(repo))
    assert popens == [] and killpgs == []
    dados = _ler_json(mod.base_dir() / "suite-fundo.json")
    assert dados["pid"] == 5002


# --------------------------------------- rodar_checks com falha de suíte

def test_falha_de_commit_reescrito_e_apagada(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha de suíte de sha que sumiu de todas as branches (amend/rebase)
    é apagada no check final, em vez de ficar para sempre."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: b")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": "f" * 40, "base": "", "relatorio": "falhou",
                    "quando": mod.agora_iso()})
    monkeypatch.setattr(mod, "_rodar_checks_selecionados",
                        lambda *a, **k: (True, ""))
    ok, _rel = mod.rodar_checks(sha, str(repo))
    assert ok
    assert not (mod.base_dir() / "suite-falhou.json").exists()


def test_rodar_checks_falha_suite_forca_completo_e_apaga(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`suite-falhou.json` presente com `selecionado`: roda o completo e,
    passando, apaga a falha registrada."""
    sha = _commit(repo, {"NOTES.md": "mudou\n"}, "docs: notas")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": sha, "relatorio": "- [ALTA] x", "quando": ""})
    usados: dict[str, int] = {"sel": 0, "comp": 0}
    logs: list[str] = []

    def _selecionados(*a: object, **k: object) -> tuple[bool, str]:
        usados["sel"] += 1
        return True, ""

    def _completos(*a: object, **k: object) -> tuple[bool, str]:
        usados["comp"] += 1
        return True, ""

    monkeypatch.setattr(mod, "_rodar_checks_selecionados", _selecionados)
    monkeypatch.setattr(mod, "_rodar_checks_completos", _completos)
    monkeypatch.setattr(mod, "log", logs.append)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert usados == {"sel": 0, "comp": 1}
    assert any("suíte completa falhou em" in l for l in logs)
    assert not (mod.base_dir() / "suite-falhou.json").exists()


def test_falha_suite_roda_check_fora_do_push_e_apaga_verde(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha com base própria: o check de `src/` RODA mesmo com o push
    novo só tocando docs (união dos arquivos da falha) e, verde, apaga."""
    base_b = _commit(repo, {"src/App.java": "class A {}\n"}, "feat: app")
    sha_f = _commit(repo, {"src/Foo.java": "class Foo {}\n"}, "feat: foo")
    sha_n = _commit(repo, {"docs/x.md": "doc\n"}, "docs: x")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _git(repo, "config", "revisor.check", "@src /bin/true")
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": sha_f, "base": base_b,
                    "relatorio": "- [ALTA] x", "quando": mod.agora_iso()})
    rodadas: list[list[tuple[str, list[str], str, bool]]] = []

    def _fake_exec(checks, tmp, timeout, pulos=None, **k: object):
        rodadas.append(list(checks))
        return True, ""

    monkeypatch.setattr(mod, "_executar_checks", _fake_exec)
    ok, rel = mod.rodar_checks(sha_n, str(repo))
    assert ok is True and rel == ""
    assert len(rodadas) == 1 and rodadas[0]
    assert rodadas[0][0][0] == "src"  # não filtrado pelo diff do push novo
    assert not (mod.base_dir() / "suite-falhou.json").exists()


def test_falha_suite_roda_check_fora_do_push_e_falha_permanece(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesmo cenário com o check falhando: (False, ...) e a falha fica."""
    base_b = _commit(repo, {"src/App.java": "class A {}\n"}, "feat: app")
    sha_f = _commit(repo, {"src/Foo.java": "class Foo {}\n"}, "feat: foo")
    sha_n = _commit(repo, {"docs/x.md": "doc\n"}, "docs: x")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _git(repo, "config", "revisor.check", "@src /bin/true")
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": sha_f, "base": base_b,
                    "relatorio": "- [ALTA] x", "quando": mod.agora_iso()})

    def _fake_exec(checks, tmp, timeout, pulos=None, **k: object):
        return False, "- [ALTA] quebrou de novo"

    monkeypatch.setattr(mod, "_executar_checks", _fake_exec)
    ok, rel = mod.rodar_checks(sha_n, str(repo))
    assert ok is False and "quebrou de novo" in rel
    dados = _ler_json(mod.base_dir() / "suite-falhou.json")
    assert dados["sha"] == sha_f and dados["base"] == base_b


def test_falha_suite_nao_ancestral_nao_forca_completo_nem_apaga(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha registrada em sha de outra linha: o selecionado segue normal
    (não força o completo) e a falha permanece."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "-b", "main")
    _git(repo, "checkout", "-q", "-b", "outra")
    sha_outra = _commit(repo, {"c.txt": "1\n"}, "feat: c")
    _git(repo, "checkout", "-q", "main")
    sha_n = _commit(repo, {"NOTES.md": "mudou\n"}, "docs: notas")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": sha_outra, "base": "", "relatorio": "- [ALTA] x",
                    "quando": mod.agora_iso()})
    usados: dict[str, int] = {"sel": 0, "comp": 0}
    logs: list[str] = []

    def _selecionados(*a: object, **k: object) -> tuple[bool, str]:
        usados["sel"] += 1
        return True, ""

    def _completos(*a: object, **k: object) -> tuple[bool, str]:
        usados["comp"] += 1
        return True, ""

    monkeypatch.setattr(mod, "_rodar_checks_selecionados", _selecionados)
    monkeypatch.setattr(mod, "_rodar_checks_completos", _completos)
    monkeypatch.setattr(mod, "log", logs.append)
    ok, rel = mod.rodar_checks(sha_n, str(repo))
    assert ok is True and rel == ""
    assert usados == {"sel": 1, "comp": 0}
    assert any("suíte falhou em outra linha" in l for l in logs)
    assert (mod.base_dir() / "suite-falhou.json").exists()


def test_rodar_checks_sem_falha_usa_selecionado_e_forcar_completo_ignora(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `suite-falhou.json`: selecionado; `forcar_completo` ignora."""
    sha = _commit(repo, {"NOTES.md": "mudou\n"}, "docs: notas")
    _git(repo, "config", "revisor.checkFinal", "selecionado")
    usados: dict[str, int] = {"sel": 0, "comp": 0}

    def _selecionados(*a: object, **k: object) -> tuple[bool, str]:
        usados["sel"] += 1
        return True, ""

    def _completos(*a: object, **k: object) -> tuple[bool, str]:
        usados["comp"] += 1
        return True, ""

    monkeypatch.setattr(mod, "_rodar_checks_selecionados", _selecionados)
    monkeypatch.setattr(mod, "_rodar_checks_completos", _completos)
    monkeypatch.setattr(mod, "log", lambda *a: None)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""
    assert usados == {"sel": 1, "comp": 0}
    ok, rel = mod.rodar_checks(sha, str(repo), forcar_completo=True)
    assert ok is True and rel == ""
    assert usados == {"sel": 1, "comp": 1}


# ---------------------------------------------------- cmd_suite_completa

def test_suite_completa_falha_real_grava_e_notifica(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha real: `suite-falhou.json` gravado, notifica e apaga o fundo."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: b")
    _escrever_json(mod.base_dir() / "suite-fundo.json",
                   {"sha": sha, "base": mod.pai1_ou_vazio(sha),
                    "pid": 999_999, "quando": mod.agora_iso()})
    relatorio = "- [ALTA] testes quebrados em FooTest\n    at Foo.java:1\n"
    monkeypatch.setattr(mod, "rodar_checks",
                        lambda *a, **k: (False, relatorio))
    _condicoes_livres(monkeypatch)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    rc = mod.cmd_suite_completa(sha)
    assert rc == 1
    dados = _ler_json(mod.base_dir() / "suite-falhou.json")
    assert dados["sha"] == sha
    assert dados["relatorio"] == relatorio[:mod.MAX_RELATORIO]
    assert any(titulo == "Suíte completa falhou"
               and "testes quebrados em FooTest" in msg
               and "o próximo push roda a suíte completa" in msg
               for titulo, msg in avisos)
    assert not (mod.base_dir() / "suite-fundo.json").exists()


def test_suite_completa_verde_apaga_falha_de_ancestral(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verde: apaga falha de sha ancestral; falha de descendente fica."""
    pai = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    filho = _commit(repo, {"a.txt": "2\n"}, "feat: b")
    _escrever_json(mod.base_dir() / "suite-fundo.json",
                   {"sha": filho, "base": "", "pid": 999_999,
                    "quando": mod.agora_iso()})
    monkeypatch.setattr(mod, "rodar_checks",
                        lambda *a, **k: (True, ""))
    _condicoes_livres(monkeypatch)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    # falha no pai (ancestral do sha da suíte): verde apaga
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": pai, "relatorio": "- [ALTA] x", "quando": ""})
    assert mod.cmd_suite_completa(filho) == 0
    assert not (mod.base_dir() / "suite-falhou.json").exists()
    # falha no filho (NÃO ancestral do pai): verde de outro sha não apaga
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": filho, "relatorio": "- [ALTA] y", "quando": ""})
    assert mod.cmd_suite_completa(pai) == 0
    dados = _ler_json(mod.base_dir() / "suite-falhou.json")
    assert dados["sha"] == filho
    assert avisos == []


def test_suite_completa_infra_repete_ate_o_prazo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Infra (CHECK_SEM_RECURSO): não grava falha; repete até esgotar o prazo
    e então notifica 'não rodou'."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: b")
    relatorio = f"{mod.CHECK_SEM_RECURSO}: mvn test (exit 137)"
    execucoes: list[int] = []

    def _falha_infra_sempre(*a: object, **k: object) -> tuple[bool, str]:
        execucoes.append(1)
        return False, relatorio

    monkeypatch.setattr(mod, "rodar_checks", _falha_infra_sempre)
    _maquina_ocupada(monkeypatch)  # nunca libera: cada volta roda checks?
    # não: com memória baixa a suíte volta a esperar, sem rodar checks —
    # o prazo esgota na espera (o correto é não gravar falha nem rodar)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    relogio = {"v": 0.0}

    def fake_monotonic() -> float:
        relogio["v"] += 4_000.0
        return relogio["v"]

    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    rc = mod.cmd_suite_completa(sha)
    assert rc == 1
    assert not (mod.base_dir() / "suite-falhou.json").exists()
    assert any(titulo == "Suíte completa não rodou" for titulo, _ in avisos)

    # variante: máquina libera mas a infra falha de novo a cada rodada —
    # repete (cedido/ocupado) dentro do prazo, sem gravar falha
    relogio["v"] = 0.0
    execucoes.clear()
    _condicoes_livres(monkeypatch)
    rc = mod.cmd_suite_completa(sha)
    assert rc == 1
    assert len(execucoes) >= 2  # repetiu dentro do prazo
    assert not (mod.base_dir() / "suite-falhou.json").exists()


def test_suite_completa_sem_ferramenta_nao_marca_falha(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CHECK_SEM_FERRAMENTA: notifica 'não rodou' e encerra sem marcar falha."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: b")
    monkeypatch.setattr(
        mod, "rodar_checks",
        lambda *a, **k: (False,
                         f"{mod.CHECK_SEM_FERRAMENTA}: mvn em app: "
                         "comando não encontrado"))
    _condicoes_livres(monkeypatch)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    rc = mod.cmd_suite_completa(sha)
    assert rc == 0
    assert not (mod.base_dir() / "suite-falhou.json").exists()
    assert any(titulo == "Suíte completa não rodou" for titulo, _ in avisos)


# ------------------------------------- _executar_checks com abortar (ceder)

def test_executar_checks_abortar_cede_a_vez(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`abortar` True: mata o grupo (SIGTERM) e devolve CHECK_CEDIDO."""
    killpgs: list[tuple[int, int]] = []

    class _ProcVivo:
        pid = 4242

        def communicate(self, timeout: float | None = None):
            raise subprocess.TimeoutExpired(cmd=["/bin/sleep", "100"],
                                            timeout=timeout)

        def wait(self) -> int:
            return 0

    def fake_popen(cmd, *a: object, **k: object) -> _ProcVivo:
        return _ProcVivo()

    def fake_killpg(pid: int, sig: int) -> None:
        killpgs.append((pid, sig))

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mod.os, "killpg", fake_killpg)
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    checks = [("", ["/bin/sleep", "100"], "", False)]
    ok, rel = mod._executar_checks(checks, str(tmp_path), 30,
                                   estrito=True, abortar=lambda: True)
    assert ok is False
    assert rel.startswith(mod.CHECK_CEDIDO)
    assert (4242, signal.SIGTERM) in killpgs
    assert any("check interrompido" in l for l in logs)
    assert mod.check_retentavel(rel) is True
    assert mod.check_ocupado(rel) is True


def test_check_retentavel_aceita_cedido() -> None:
    """CHECK_CEDIDO entra no conjunto retentável (e, portanto, ocupado)."""
    assert mod.check_retentavel(f"{mod.CHECK_CEDIDO}: mvn test em .") is True
    assert mod.check_ocupado(f"{mod.CHECK_CEDIDO}: mvn test em .") is True
    assert mod.check_retentavel("- [ALTA] falha real") is False


# ------------------------------------------- 4b: retentar-push segurado de novo

def _gravar_git_push(monkeypatch: pytest.MonkeyPatch,
                     resultados: list[subprocess.CompletedProcess],
                     chamadas: list[list[str]]) -> None:
    """Envolve mod.git: 'push' devolve os resultados em sequência."""
    real = mod.git

    def fake_git(args, cwd=None, timeout=mod.TEMPO_GIT, env_extra=None):
        chamadas.append(list(args))
        if args and args[0] == "push":
            return resultados.pop(0) if resultados else _Proc(0, "", "")
        return real(args, cwd=cwd, timeout=timeout, env_extra=env_extra)

    monkeypatch.setattr(mod, "git", fake_git)


def test_retentar_push_segurado_de_novo_volta_a_esperar(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Push segurado DE NOVO: volta ao laço e, liberando, envia — um só
    'Push enviado', dois pushes, sem 'Push ainda bloqueado'."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "-b", "main")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: v1")
    pasta = mod.base_dir() / "retentar-push"
    pedido = pasta / f"{sha}.json"
    _escrever_json(pedido,
                   {"remote": "origem", "remote_ref": "refs/heads/main",
                    "local_ref": "refs/heads/main",
                    "quando": mod.agora_iso()})
    marcador = mod.MARCADOR_PUSH_SEGURADO
    resultados = [_Proc(1, "", f"remote: revisor: {marcador}; reintentando"),
                  _Proc(0, "", "")]
    chamadas: list[list[str]] = []
    _gravar_git_push(monkeypatch, resultados, chamadas)
    _condicoes_livres(monkeypatch)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    rc = mod.cmd_retentar_push(sha)
    assert rc == 0
    pushes = [c for c in chamadas if c and c[0] == "push"]
    assert len(pushes) == 2
    assert pushes[0] == ["push", "origem",
                         "refs/heads/main:refs/heads/main"]
    assert [t for t, _m in avisos].count("Push enviado") == 1
    assert all(t != "Push ainda bloqueado" for t, _m in avisos)
    assert any("segurado de novo" in l for l in logs)
    assert not pedido.exists()


def test_retentar_push_segurado_sempre_esgota_teto(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Segurado em toda tentativa: para no teto (sem laço infinito), com
    'Push segue segurado' e sem 'Push enviado'."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "-b", "main")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: v1")
    pedido = mod.base_dir() / "retentar-push" / f"{sha}.json"
    _escrever_json(pedido,
                   {"remote": "origem", "remote_ref": "refs/heads/main",
                    "local_ref": "refs/heads/main",
                    "quando": mod.agora_iso()})
    marcador = mod.MARCADOR_PUSH_SEGURADO
    resultados = [_Proc(1, "", f"remote: revisor: {marcador}")
                  for _ in range(mod.MAX_SEGURADAS_RETENTAR + 5)]
    chamadas: list[list[str]] = []
    _gravar_git_push(monkeypatch, resultados, chamadas)
    _condicoes_livres(monkeypatch)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    monkeypatch.setattr(mod, "log", lambda msg: None)
    rc = mod.cmd_retentar_push(sha)
    assert rc == 1
    pushes = [c for c in chamadas if c and c[0] == "push"]
    assert len(pushes) == mod.MAX_SEGURADAS_RETENTAR
    assert [t for t, _m in avisos] == ["Push segue segurado"]
    assert not pedido.exists()


def test_apagar_suite_fundo_por_token_sem_pid(repo: Path) -> None:
    """Filho identifica o próprio registro pelo token mesmo sem pid gravado."""
    arq = mod.base_dir() / "suite-fundo.json"
    _escrever_json(arq, {"sha": "x", "base": "", "token": "t1",
                         "quando": mod.agora_iso()})
    mod._apagar_suite_fundo_se_minha(None, "outro")
    assert arq.exists()
    mod._apagar_suite_fundo_se_minha(None, "t1")
    assert not arq.exists()


def test_retentar_push_falha_nao_segurada_notifica_bloqueio(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reprovação real (sem marcador): 'Push ainda bloqueado' e sem 2º push."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "-b", "main")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: v1")
    pedido = mod.base_dir() / "retentar-push" / f"{sha}.json"
    _escrever_json(pedido,
                   {"remote": "origem", "remote_ref": "refs/heads/main",
                    "local_ref": "refs/heads/main",
                    "quando": mod.agora_iso()})
    resultados = [_Proc(1, "", "! [remote rejected] main -> main (reprovado)")]
    chamadas: list[list[str]] = []
    _gravar_git_push(monkeypatch, resultados, chamadas)
    _condicoes_livres(monkeypatch)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    rc = mod.cmd_retentar_push(sha)
    assert rc == 1
    assert len([c for c in chamadas if c and c[0] == "push"]) == 1
    assert any(t == "Push ainda bloqueado" for t, _m in avisos)
    assert not pedido.exists()


# ------------------------------------------------------------- status (item 4)

def test_linhas_de_status_da_suite(repo: Path) -> None:
    """Status mostra a suíte em fundo (pid vivo/morto) e a falha pendente."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _escrever_json(mod.base_dir() / "suite-fundo.json",
                   {"sha": sha, "base": "", "pid": 999_999,
                    "quando": mod.agora_iso()})
    _escrever_json(mod.base_dir() / "suite-falhou.json",
                   {"sha": sha, "relatorio": "- [ALTA] quebrou\nat Foo:1\n",
                    "quando": mod.agora_iso()})
    fundo = mod.linhas_suite_fundo()
    assert len(fundo) == 1
    assert sha[:7] in fundo[0] and "pid 999999 morto" in fundo[0]
    falha = mod.linha_suite_falhou()
    assert falha is not None
    assert falha.startswith(f"suíte completa falhou em {sha[:7]}")
    assert "quebrou" in falha
    # sem arquivos: nada a mostrar
    (mod.base_dir() / "suite-fundo.json").unlink()
    (mod.base_dir() / "suite-falhou.json").unlink()
    assert mod.linhas_suite_fundo() == []
    assert mod.linha_suite_falhou() is None
