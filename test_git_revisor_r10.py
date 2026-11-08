"""Testes R10: check final nunca pula; push segurado com retentativa.

O check FINAL (`rodar_checks`/`_rodar_checks_selecionados`) não pode
pular nada: falha de infraestrutura espera memória e repete uma vez,
ferramenta ausente segura o push sem reprovar, e o push retentado
(`retentar-push`) roda sozinho quando a máquina liberar. No check
RÁPIDO, falha de infra (incluindo a JVM morta do surefire) vira pulo,
nunca REPROVADO.
"""
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


# ---------------------------------------------------------------- _falha_infra

def test_falha_infra_codigos() -> None:
    """137, 143, negativo e 75 são infra; falha de assert não é."""
    for codigo in (137, 143, -9, 75):
        assert mod._falha_infra(codigo, "") is True
    assert mod._falha_infra(1, "") is False


def test_falha_infra_forked_vm_e_killed() -> None:
    """JVM do surefire morta e linha 'Killed' inteira são infra."""
    saida = ("Tests run: 5\n"
             "The forked VM terminated without properly saying goodbye\n")
    assert mod._falha_infra(1, saida) is True
    assert mod._falha_infra(1, "algo\nKilled\nbuild stop") is True
    # OutOfMemoryError de TESTE não é infra
    assert mod._falha_infra(1, "java.lang.OutOfMemoryError: Java heap") is False


# ------------------------------------------------------------ _executar_checks

def _checks_fake(monkeypatch: pytest.MonkeyPatch, codigos: list[tuple[int, str]],
                 tmp: Path) -> dict:
    """Troca subprocess.run por fake que devolve (código, saída) em sequência."""
    estado: dict = {"execucoes": 0, "fila": list(codigos)}
    logs: list[str] = []

    def fake_run(lista, **kwargs):
        estado["execucoes"] += 1
        codigo, saida = estado["fila"].pop(0)
        return _Proc(codigo, saida, "")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "log", logs.append)
    monkeypatch.setattr(mod, "esperar_memoria",
                        lambda limite_s=600, **_k: True)
    return estado


def test_estrito_infra_repete_e_devolve_sem_recurso(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 137 duas vezes no check final: CHECK_SEM_RECURSO, 2 execuções."""
    checks = [("", ["/usr/bin/ferramenta", "test"], "", False)]
    estado = _checks_fake(monkeypatch, [(137, ""), (137, "")], tmp_path)
    ok, rel = mod._executar_checks(checks, str(tmp_path), 10, estrito=True)
    assert ok is False
    assert rel.startswith(mod.CHECK_SEM_RECURSO)
    assert "(exit 137)" in rel
    assert estado["execucoes"] == 2


def test_estrito_infra_repeticao_passa(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 137 e depois 0 no check final: aprovado na repetição."""
    checks = [("", ["/usr/bin/ferramenta", "test"], "", False)]
    estado = _checks_fake(monkeypatch, [(137, ""), (0, "")], tmp_path)
    ok, rel = mod._executar_checks(checks, str(tmp_path), 10, estrito=True)
    assert ok is True and rel == ""
    assert estado["execucoes"] == 2


def test_estrito_infra_sem_abortar_espera_memoria_curta(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `abortar` (check final do push com o lock de checks preso), a
    espera por memória da repetição estrita usa o limite curto, não 600 s."""
    checks = [("", ["/usr/bin/ferramenta", "test"], "", False)]
    limites: list[int] = []

    def _espera(limite_s: int = 600, **_k: object) -> bool:
        limites.append(limite_s)
        return True

    estado = _checks_fake(monkeypatch, [(137, ""), (137, "")], tmp_path)
    monkeypatch.setattr(mod, "esperar_memoria", _espera)
    ok, _rel = mod._executar_checks(checks, str(tmp_path), 10, estrito=True)
    assert ok is False
    assert limites == [mod.ESPERA_MEMORIA_SEM_ABORTAR_S]
    assert estado["execucoes"] == 2


def test_nao_estrito_forked_vm_vira_pulo(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check rápido com JVM morta: pulo (True), nunca REPROVADO."""
    checks = [("", ["mvn", "test"], "", False)]
    saida_vm = ("Tests run: 3\n"
                "The forked VM terminated without properly saying goodbye\n")
    estado = _checks_fake(monkeypatch, [(1, saida_vm)], tmp_path)
    pulos: list[str] = []
    ok, rel = mod._executar_checks(checks, str(tmp_path), 10, pulos=pulos)
    assert ok is True and rel == ""
    assert estado["execucoes"] == 1
    assert pulos and "forked VM" not in rel


def test_estrito_exit_127_vira_sem_ferramenta(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 127 em check detectado no check final: CHECK_SEM_FERRAMENTA."""
    checks = [("", ["/usr/bin/nao-existe", "test"], "", False)]
    estado = _checks_fake(monkeypatch, [(127, "")], tmp_path)
    ok, rel = mod._executar_checks(checks, str(tmp_path), 10, estrito=True)
    assert ok is False
    assert rel.startswith(mod.CHECK_SEM_FERRAMENTA)
    assert "exit 127" in rel
    assert estado["execucoes"] == 1


def test_predicados_de_pulo() -> None:
    """check_ocupado cobre os três prefixos; retentável exclui sem ferramenta."""
    for prefixo in (mod.CHECK_OCUPADO, mod.CHECK_SEM_RECURSO,
                    mod.CHECK_SEM_FERRAMENTA):
        assert mod.check_ocupado(prefixo + ": detalhe") is True
        assert mod.check_ocupado("- [ALTA] check") is False
    assert mod.check_retentavel(mod.CHECK_OCUPADO + " x") is True
    assert mod.check_retentavel(mod.CHECK_SEM_RECURSO + " x") is True
    assert mod.check_retentavel(mod.CHECK_SEM_FERRAMENTA + " x") is False
    assert mod.check_sem_ferramenta(mod.CHECK_SEM_FERRAMENTA + " x") is True
    assert mod.check_sem_ferramenta(mod.CHECK_SEM_RECURSO + " x") is False


# ------------------------------------------------- detectar_checks (ausentes)

@pytest.fixture()
def npm_sem_node(monkeypatch: pytest.MonkeyPatch):
    """npm 'instalado' e pytest indisponível, para forçar o caminho ausente."""
    monkeypatch.setattr(mod.shutil, "which",
                        lambda nome: f"/usr/bin/{nome}")
    monkeypatch.setattr(mod, "_pytest_disponivel", lambda python: False)


def test_detectar_checks_registra_ausentes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        npm_sem_node) -> None:
    """package.json com script test e sem node_modules entra em `ausentes`."""
    raiz = tmp_path / "wt"
    (raiz / "app").mkdir(parents=True)
    (raiz / "app" / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
    ausentes: list[tuple[str, str]] = []
    detectados = mod.detectar_checks(str(raiz), str(tmp_path / "topo"),
                                     ausentes=ausentes)
    assert detectados == []
    assert len(ausentes) == 1
    prefixo, motivo = ausentes[0]
    assert prefixo == "app"
    assert "node_modules" in motivo


def test_check_final_bloqueia_ferramenta_ausente_na_pasta(
        repo: Path, monkeypatch: pytest.MonkeyPatch, npm_sem_node) -> None:
    """Check final com alterado na pasta sem node_modules: SEM_FERRAMENTA."""
    _commit(repo, {"app/package.json": json.dumps({"scripts": {"test": "jest"}}),
                   "app/app.js": "console.log(1)\n"}, "feat: app npm")
    sha = _commit(repo, {"app/app.js": "console.log(2)\n"}, "feat: app v2")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert rel.startswith(mod.CHECK_SEM_FERRAMENTA)
    assert "node_modules" in rel


def test_check_final_ausente_fora_da_pasta_nao_bloqueia(
        repo: Path, monkeypatch: pytest.MonkeyPatch, npm_sem_node) -> None:
    """Ferramenta ausente cuja pasta não cobre o alterado não bloqueia."""
    _commit(repo, {"app/package.json": json.dumps({"scripts": {"test": "jest"}}),
                   "app/app.js": "console.log(1)\n"}, "feat: app npm")
    sha = _commit(repo, {"raiz.txt": "mudou\n"}, "feat: raiz")
    logs: list[str] = []
    monkeypatch.setattr(mod, "log", logs.append)
    ok, rel = mod.rodar_checks(sha, str(repo))
    assert ok is True and rel == ""


# --------------------------------------------------------- _bloquear_ocupado

def _pedido_retentar(repo: Path) -> Path:
    """Caminho do pedido de retentativa do sha na ponta."""
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return mod.base_dir() / "retentar-push" / f"{sha}.json"


def test_push_segurado_retentavel_dispara_segundo_plano(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relatório retentável com remote/local_ref: grava json e dispara Popen."""
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    popens: list[list[str]] = []

    class _FakePopen:
        pid = 99_999

    real_popen = subprocess.Popen

    def _popen_falso(cmd, *args: object, **kw: object):
        """Intercepta só o disparo retentar-push; git e resto usam Popen real."""
        if any("retentar-push" in str(parte) for parte in cmd):
            popens.append(list(cmd))
            return _FakePopen
        return real_popen(cmd, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(mod.subprocess, "Popen", _popen_falso)
    monkeypatch.setattr(mod, "gitleaks_intervalo", lambda *a, **k: 0)
    monkeypatch.setattr(mod, "rodar_checks",
                        lambda *a, **k: (False,
                                         f"{mod.CHECK_SEM_RECURSO}: "
                                         "mvn test (exit 137)"))
    rc = mod.revisar_intervalo([sha], sha, str(repo), [], "origem",
                               "refs/heads/main", "refs/heads/main")
    assert rc == 1
    assert len(popens) == 1
    assert "retentar-push" in popens[0]
    pedido = _pedido_retentar(repo)
    assert pedido.exists()
    dados = json.loads(pedido.read_text(encoding="utf-8"))
    assert dados["remote"] == "origem"
    assert dados["remote_ref"] == "refs/heads/main"
    assert dados["local_ref"] == "refs/heads/main"
    assert dados["pid"] == 99_999


def test_push_segurado_sem_ferramenta_nao_dispara(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ferramenta ausente: notifica e não grava pedido de retentativa."""
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: a")
    popens: list[list[str]] = []
    avisos: list[tuple[str, str]] = []

    class _FakePopen:
        pid = 99_999

    real_popen = subprocess.Popen

    def _popen_falso(cmd, *args: object, **kw: object):
        """Intercepta só o disparo retentar-push; git e resto usam Popen real."""
        if any("retentar-push" in str(parte) for parte in cmd):
            popens.append(list(cmd))
            return _FakePopen
        return real_popen(cmd, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(mod.subprocess, "Popen", _popen_falso)
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    monkeypatch.setattr(mod, "gitleaks_intervalo", lambda *a, **k: 0)
    monkeypatch.setattr(mod, "rodar_checks",
                        lambda *a, **k: (False,
                                         f"{mod.CHECK_SEM_FERRAMENTA}: "
                                         "npm test em app: command not found"))
    rc = mod.revisar_intervalo([sha], sha, str(repo), [], "origem",
                               "refs/heads/main", "refs/heads/main")
    assert rc == 1
    assert popens == []
    pedido = _pedido_retentar(repo)
    assert not pedido.exists()
    assert any("falta ferramenta" in msg for _t, msg in avisos)


# ------------------------------------------------------------- retentar-push

def _gravar_git(monkeypatch: pytest.MonkeyPatch):
    """Envolve mod.git gravando chamadas; 'push' finge sucesso."""
    chamadas: list[list[str]] = []
    real = mod.git

    def fake_git(args, cwd=None, timeout=mod.TEMPO_GIT, env_extra=None):
        chamadas.append(list(args))
        if args and args[0] == "push":
            return _Proc(0, "", "")
        return real(args, cwd=cwd, timeout=timeout, env_extra=env_extra)

    monkeypatch.setattr(mod, "git", fake_git)
    return chamadas


def _escrever_pedido(repo: Path, sha: str) -> Path:
    """Grava o pedido de retentativa do sha."""
    pasta = mod.base_dir() / "retentar-push"
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{sha}.json"
    caminho.write_text(json.dumps(
        {"remote": "origem", "remote_ref": "refs/heads/main",
         "local_ref": "refs/heads/main", "quando": mod.agora_iso()}),
        encoding="utf-8")
    return caminho


def test_retentar_push_branch_andou_desiste(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Branch que andou: desiste sem push e apaga o pedido."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "-b", "main")
    velho = _commit(repo, {"a.txt": "2\n"}, "feat: v1")
    _commit(repo, {"a.txt": "3\n"}, "feat: v2")  # main andou
    pedido = _escrever_pedido(repo, velho)
    chamadas = _gravar_git(monkeypatch)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    rc = mod.cmd_retentar_push(velho)
    assert rc == 0
    assert not any(c and c[0] == "push" for c in chamadas)
    assert not pedido.exists()


def test_retentar_push_condicoes_ok_envia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Condições livres: roda git push <remote> <local_ref>:<remote_ref>."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "-b", "main")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: v1")
    pedido = _escrever_pedido(repo, sha)
    chamadas = _gravar_git(monkeypatch)
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 99_999)
    monkeypatch.setattr(mod, "lock_checks_ocupado", lambda: False)
    monkeypatch.setattr(mod, "push_esperando", lambda: False)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    rc = mod.cmd_retentar_push(sha)
    assert rc == 0
    assert ["push", "origem", "refs/heads/main:refs/heads/main"] in chamadas
    assert any(titulo == "Push enviado" for titulo, _msg in avisos)
    assert not pedido.exists()


def test_retentar_push_prazo_esgotado_segura(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Máquina nunca libera no prazo: notifica 'Push segue segurado'."""
    _commit(repo, {"a.txt": "1\n"}, "feat: a")
    _git(repo, "checkout", "-q", "-b", "main")
    sha = _commit(repo, {"a.txt": "2\n"}, "feat: v1")
    pedido = _escrever_pedido(repo, sha)
    chamadas = _gravar_git(monkeypatch)
    monkeypatch.setattr(mod, "mem_disponivel_mb", lambda: 0)
    monkeypatch.setattr(mod, "lock_checks_ocupado", lambda: False)
    monkeypatch.setattr(mod, "push_esperando", lambda: False)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    relogio = {"v": 0.0}

    def fake_monotonic() -> float:
        relogio["v"] += 4_000.0
        return relogio["v"]

    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)
    avisos: list[tuple[str, str]] = []
    monkeypatch.setattr(mod, "notificar",
                        lambda titulo, msg: avisos.append((titulo, msg)))
    rc = mod.cmd_retentar_push(sha)
    assert rc == 1
    assert not any(c and c[0] == "push" for c in chamadas)
    assert any("Push segue segurado" in titulo for titulo, _msg in avisos)
    assert not pedido.exists()


# ------------------------------------------------------ errata sobrevive R10

def test_errata_sobrevive_ao_corte_da_intencao(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mensagens enormes (> MAX_INTENCAO) não cortam mais a errata."""
    mensagem = "feat: msg enorme\n\n" + "detalhe " * 900
    _git(repo, "commit", "-q", "--allow-empty", "-m", mensagem)
    sha1 = _git(repo, "rev-parse", "HEAD").stdout.strip()
    errata = (f"- {sha1[:7]}: o commit realmente faz outra coisa "
              "MARCADOR-ERRATA-12345\n")
    pasta = repo / ".revisor"
    pasta.mkdir(exist_ok=True)
    (pasta / "errata.md").write_text(errata, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "docs: errata")
    sha2 = _git(repo, "rev-parse", "HEAD").stdout.strip()

    texto = mod.intencao_commits([sha1], ponta=sha2)
    assert "MARCADOR-ERRATA-12345" in texto
    assert "(…)" in texto  # blocos de mensagens foram cortados
    limite = mod.MAX_INTENCAO + mod.MAX_ERRATA + 64
    assert len(texto.encode("utf-8")) <= limite

    prompt = mod.montar_prompt("DIFF", "repo", "abc1234", intencao=texto)
    assert "MARCADOR-ERRATA-12345" in prompt
