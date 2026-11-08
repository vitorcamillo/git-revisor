"""Testes do git_revisor: checks, triagem por risco, intenção e correção."""
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


# ---------------------------------------------------------------- casa_glob

@pytest.mark.parametrize("caminho,padrao,esperado", [
    ("docs/a/b.md", "docs/**", True),
    ("src/auth/x.py", "**/auth*", True),
    ("README.md", "*.md", True),
    ("src/README.md", "*.md", True),
    ("a/b.sql", "**/*.sql", True),
    ("src/x.py", "*.md", False),
])
def test_casa_glob(caminho: str, padrao: str, esperado: bool) -> None:
    """casa_glob entende `**`, `*` sem cruzar `/` e basename sem `/`."""
    assert mod.casa_glob(caminho, padrao) is esperado


# --------------------------------------------------------- classificar_risco

def test_classificar_risco_so_documentacao_eh_baixo(repo: Path) -> None:
    """Commit só com .md é de baixo risco."""
    sha = _commit(repo, {"README.md": "# oi\n"}, "docs: readme")
    assert mod.classificar_risco(mod.VAZIO_SHA, sha) == ("baixo", [])


def test_classificar_risco_md_mais_codigo_eh_normal(repo: Path) -> None:
    """Mistura de .md com .py não é baixo nem alto: normal."""
    sha = _commit(repo, {"README.md": "# oi\n", "app.py": "x = 1\n"},
                  "feat: app")
    assert mod.classificar_risco(mod.VAZIO_SHA, sha) == ("normal", [])


def test_classificar_risco_auth_eh_alto(repo: Path) -> None:
    """Arquivo sob src/auth/ é alto risco e vem listado."""
    sha = _commit(repo, {"src/auth/login.py": "x = 1\n"}, "feat: login")
    risco, caminhos = mod.classificar_risco(mod.VAZIO_SHA, sha)
    assert risco == "alto"
    assert caminhos == ["src/auth/login.py"]


def test_classificar_risco_config_alto_extra(repo: Path) -> None:
    """Glob extra de revisor.altoRisco na config também classifica alto."""
    _git(repo, "config", "--add", "revisor.altoRisco", "**/*.perigoso")
    sha = _commit(repo, {"dados.perigoso": "x\n"}, "feat: dados")
    risco, caminhos = mod.classificar_risco(mod.VAZIO_SHA, sha)
    assert risco == "alto"
    assert caminhos == ["dados.perigoso"]


# -------------------------------------------------------------- rodar_checks

def test_trava_aceita_arquivo_personalizado(tmp_path: Path) -> None:
    """O lock alternativo bloqueia concorrentes e libera ao sair."""
    arquivo = tmp_path / "locks" / "checks.lock"
    with mod.trava(0, arquivo=arquivo) as livre:
        assert livre is True
        with mod.trava(0, arquivo=arquivo) as segunda:
            assert segunda is False
    with mod.trava(0, arquivo=arquivo) as depois:
        assert depois is True


def test_arquivo_trava_checks_respeita_xdg_state_home(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O lock global fica sob XDG_STATE_HOME quando definido."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert mod.arquivo_trava_checks() == tmp_path / "git-revisor" / "checks.lock"


def test_rodar_checks_pula_quando_lock_global_ocupado(
        repo: Path, monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path) -> None:
    """Lock ocupado não aprova: devolve CHECK_OCUPADO, nem roda o check."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(mod, "TEMPO_ESPERA_CHECK", 0)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    _git(repo, "config", "--add", "revisor.check", "sh -c 'exit 1'")
    with mod.trava(None, arquivo=mod.arquivo_trava_checks()) as livre:
        assert livre is True
        ok, rel = mod.rodar_checks(sha, str(repo))
        assert ok is False and mod.check_ocupado(rel)


def test_executar_checks_define_preexec_prioridade(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """O subprocesso do check recebe o ajuste de prioridade do build."""
    chamadas: list[dict] = []

    class _ProcFalso:
        """Popen mínimo: sai 0 na primeira communicate."""

        pid = 1
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            """Simula fim imediato do check aprovado."""
            return "", ""

    def _popen(*args: object, _real=subprocess.Popen, **kwargs: object):
        """Captura argumentos e simula check aprovado."""
        if list(args[0])[:1] != ["check-falso"]:
            return _real(*args, **kwargs)
        chamadas.append(kwargs)
        return _ProcFalso()

    monkeypatch.setattr(mod.subprocess, "Popen", _popen)
    checks = [("", ["check-falso"], "", True)]
    assert mod._executar_checks(checks, str(tmp_path), 1) == (True, "")
    assert chamadas[0]["preexec_fn"] is mod._baixar_prioridade

def _sem_ferramentas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Esconde ferramentas do PATH (which → None) para os testes de check serem estáveis."""
    monkeypatch.setattr(mod.shutil, "which", lambda *_a, **_k: None)


def test_rodar_checks_sem_config(repo: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem revisor.check configurado, passa sem rodar nada."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.rodar_checks(sha, str(repo)) == (True, "")


def test_rodar_checks_comando_ok(repo: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Check com exit 0 passa."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    _git(repo, "config", "--add", "revisor.check", "sh -c 'exit 0'")
    assert mod.rodar_checks(sha, str(repo)) == (True, "")


def test_rodar_checks_comando_falha(repo: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Check com exit 3 reprova com relatório no formato do revisor."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    _git(repo, "config", "--add", "revisor.check",
         "sh -c 'echo falhou; exit 3'")
    ok, relatorio = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert "[ALTA]" in relatorio
    assert "exit 3" in relatorio
    assert "falhou" in relatorio
    assert relatorio.endswith("VEREDITO: REPROVADO")
    listagem = _git(repo, "worktree", "list").stdout.strip().splitlines()
    assert len(listagem) == 1


def test_rodar_checks_exit_75_segura(repo: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 75 no check final: espera memória, repete e segue SEM_RECURSO."""
    _sem_ferramentas(monkeypatch)
    monkeypatch.setattr(mod, "esperar_memoria", lambda *_a, **_k: True)
    monkeypatch.setattr(mod.time, "sleep", lambda *_a: None)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    _git(repo, "config", "--add", "revisor.check", "sh -c 'exit 75'")
    ok, relatorio = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert relatorio.startswith(mod.CHECK_SEM_RECURSO)
    assert "exit 75" in relatorio


def _subprocess_falso(monkeypatch: pytest.MonkeyPatch, retorno: int = 0,
                      stdout: str = "", stderr: str = "") -> list:
    """Troca subprocess.run/Popen registrando comandos que não são git."""
    registros: list = []
    real_run = subprocess.run
    real_popen = subprocess.Popen

    class _ProcFalso:
        """Popen mínimo: pid e returncode fixos, communicate imediato."""

        def __init__(self, lista: list[str]) -> None:
            self.pid = 1
            self.returncode = retorno

        def communicate(
                self, timeout: float | None = None) -> tuple[str, str]:
            """Devolve a saída fixada sem rodar nada."""
            return stdout, stderr

    def _lista_de(comando: object) -> list[str]:
        """Normaliza comando (str ou sequência) em lista de strings."""
        return (list(comando) if isinstance(comando, (list, tuple))
                else [str(comando)])

    def _falso_run(comando, *args: object, **kwargs: object):
        """Delega git ao subprocess real e finge o resto com `retorno`."""
        lista = _lista_de(comando)
        if lista and lista[0] == "git":
            return real_run(comando, *args, **kwargs)  # type: ignore[arg-type]
        registros.append(lista)
        return subprocess.CompletedProcess(lista, retorno, stdout, stderr)

    def _falso_popen(comando, *args: object, **kwargs: object):
        """Delega git ao Popen real e finge o resto sem rodar o comando."""
        lista = _lista_de(comando)
        if lista and lista[0] == "git":
            return real_popen(comando, *args, **kwargs)  # type: ignore[arg-type]
        registros.append(lista)
        return _ProcFalso(lista)

    monkeypatch.setattr(mod.subprocess, "run", _falso_run)
    monkeypatch.setattr(mod.subprocess, "Popen", _falso_popen)
    return registros


def test_detectar_checks_tres_linguagens(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Detecta maven, pytest e npm e cria o symlink de node_modules."""
    work = tmp_path / "worktree"
    topo = tmp_path / "topo"
    (work / "backend").mkdir(parents=True)
    (work / "backend" / "pom.xml").write_text("<project />\n",
                                              encoding="utf-8")
    (work / "pyproject.toml").write_text("[tool.pytest.ini_options]\n",
                                         encoding="utf-8")
    (work / "web").mkdir(parents=True)
    (work / "web" / "package.json").write_text(
        json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8")
    (topo / "web" / "node_modules").mkdir(parents=True)

    def _which(nome: str, *_a: object, **_k: object) -> str | None:
        """Só mvn e npm existem neste cenário."""
        return f"/fake/{nome}" if nome in {"mvn", "npm"} else None

    monkeypatch.setattr(mod.shutil, "which", _which)
    monkeypatch.setattr(mod, "_pytest_disponivel", lambda _py: True)
    checks = mod.detectar_checks(str(work), str(topo))
    assert [c[0] for c in checks] == ["", "backend", "web"]
    por_prefixo = {p: cmd for p, cmd, _cwd in checks}
    assert por_prefixo["backend"] == ["mvn", "-B", "-q", "test"]
    assert por_prefixo[""][1:] == ["-m", "pytest", "-q"]
    assert por_prefixo["web"] == ["npm", "test", "--silent"]
    assert all(cwd == prefixo for prefixo, _cmd, cwd in checks)
    assert (work / "web" / "node_modules").is_symlink()


def test_detectar_checks_no_test_specified(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """package.json com o teste padrão do npm não gera check."""
    work = tmp_path / "worktree"
    topo = tmp_path / "topo"
    work.mkdir()
    topo.mkdir()
    (work / "package.json").write_text(
        json.dumps({"scripts": {
            "test": 'echo "Error: no test specified" && exit 1'}}),
        encoding="utf-8")
    (topo / "node_modules").mkdir()
    monkeypatch.setattr(
        mod.shutil, "which",
        lambda nome, *_a, **_k: "/fake/npm" if nome == "npm" else None)
    monkeypatch.setattr(mod, "_pytest_disponivel", lambda _py: True)
    assert mod.detectar_checks(str(work), str(topo)) == []


def test_filtro_roda_so_check_da_pasta_tocada(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commit que só toca web/x.js roda só o check de web."""
    _sem_ferramentas(monkeypatch)
    _commit(repo, {"leia.md": "x\n"}, "chore: base")
    sha = _commit(repo, {"web/x.js": "console.log(1)\n"}, "feat: web")
    detectados = [("backend", ["cmd-backend"], "backend"),
                  ("web", ["cmd-web"], "web")]
    monkeypatch.setattr(mod, "detectar_checks",
                        lambda *_a, **_k: detectados)
    registros = _subprocess_falso(monkeypatch, retorno=0)
    assert mod.rodar_checks(sha, str(repo)) == (True, "")
    assert registros == [["cmd-web"]]


def test_manual_tem_prioridade_sobre_deteccao(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com revisor.check configurado, detectar_checks não é chamada."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    _git(repo, "config", "--add", "revisor.check", "sh -c 'exit 0'")

    def _proibido(*_a: object, **_k: object) -> None:
        """Falha se a detecção for chamada com check manual presente."""
        raise AssertionError("detectar_checks não deveria ser chamada")

    monkeypatch.setattr(mod, "detectar_checks", _proibido)
    assert mod.rodar_checks(sha, str(repo)) == (True, "")


def test_formato_arroba_roda_no_cwd_da_pasta(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`@backend sh -c ...` roda com cwd no backend do worktree."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"backend/marcador.txt": "x\n"}, "feat: backend")
    _git(repo, "config", "--add", "revisor.check",
         "@backend sh -c 'test -f marcador.txt'")
    assert mod.rodar_checks(sha, str(repo)) == (True, "")


def test_auto_check_desligado_sem_worktree(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """revisor.autoCheck false: sem manual, nenhum check nem worktree."""
    _sem_ferramentas(monkeypatch)
    _git(repo, "config", "revisor.autoCheck", "false")
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")

    def _proibido(*_a: object, **_k: object) -> None:
        """Falha se a detecção for chamada com autoCheck desligado."""
        raise AssertionError("detectar_checks não deveria ser chamada")

    monkeypatch.setattr(mod, "detectar_checks", _proibido)
    assert mod.rodar_checks(sha, str(repo)) == (True, "")
    listagem = _git(repo, "worktree", "list").stdout.strip().splitlines()
    assert len(listagem) == 1


def test_pulo_pytest_exit_5(repo: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Pytest exit 5 (nenhum teste coletado) pula sem reprovar."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    detectados = [("", [mod.sys.executable, "-m", "pytest", "-q"], "")]
    monkeypatch.setattr(mod, "detectar_checks",
                        lambda *_a, **_k: detectados)
    _subprocess_falso(monkeypatch, retorno=5)
    assert mod.rodar_checks(sha, str(repo)) == (True, "")


def test_segura_comando_inexistente(repo: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Comando não encontrado no check final: SEM_FERRAMENTA, sem pular."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    _git(repo, "config", "--add", "revisor.check",
         "comando-que-nao-existe-xyz --help")
    ok, relatorio = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert relatorio.startswith(mod.CHECK_SEM_FERRAMENTA)
    assert "comando-que-nao-existe-xyz" in relatorio


def test_segura_detectado_cannot_find_module(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check detectado com Cannot find module no final: SEM_FERRAMENTA."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"web/x.js": "console.log(1)\n"}, "feat: web")
    detectados = [("web", ["node", "app.js"], "web")]
    monkeypatch.setattr(mod, "detectar_checks",
                        lambda *_a, **_k: detectados)
    _subprocess_falso(monkeypatch, retorno=1,
                      stderr="Error: Cannot find module 'foo'\n")
    ok, relatorio = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert relatorio.startswith(mod.CHECK_SEM_FERRAMENTA)
    assert "Cannot find module" in relatorio


def test_manual_cannot_find_module_reprova(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check manual com Cannot find module reprova (o usuário pediu)."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    _git(repo, "config", "--add", "revisor.check",
         "sh -c 'echo \"Cannot find module\" >&2; exit 1'")
    ok, _relatorio = mod.rodar_checks(sha, str(repo))
    assert ok is False


def test_reprovacao_detectado_com_pasta(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha real de check detectado reprova com (pasta backend)."""
    _sem_ferramentas(monkeypatch)
    sha = _commit(repo, {"backend/app.py": "x = 1\n"}, "feat: backend")
    detectados = [("backend", ["sh", "-c", "echo quebrou; exit 2"],
                   "backend")]
    monkeypatch.setattr(mod, "detectar_checks",
                        lambda *_a, **_k: detectados)
    ok, relatorio = mod.rodar_checks(sha, str(repo))
    assert ok is False
    assert "exit 2" in relatorio
    assert "quebrou" in relatorio
    assert "(pasta backend)" in relatorio


# ----------------------------------------------------------- intenção/gate

def test_intencao_commits_traz_corpo(repo: Path) -> None:
    """intencao_commits inclui o corpo completo da mensagem."""
    sha = _commit(repo, {"app.py": "x = 1\n"},
                  "feat: app\n\nCorpo da mensagem.\nSegunda linha do corpo.")
    texto = mod.intencao_commits([sha])
    assert f"== {sha[:7]} ==" in texto
    assert "Corpo da mensagem." in texto
    assert "Segunda linha do corpo." in texto


def test_intencao_commits_respeita_cap(repo: Path) -> None:
    """intencao_commits corta em MAX_INTENCAO bytes com aviso."""
    sha = _commit(repo, {"app.py": "x = 1\n"},
                  "feat: app\n\n" + "x" * 5000)
    texto = mod.intencao_commits([sha])
    assert len(texto.encode("utf-8")) <= mod.MAX_INTENCAO
    assert texto.endswith("(…)")


def test_evidencia_gate_sem_arquivo(repo: Path) -> None:
    """Sem quality-gate-result.json no commit, devolve None."""
    sha = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    assert mod.evidencia_gate(sha) is None


def test_evidencia_gate_json_invalido(repo: Path) -> None:
    """JSON inválido no quality-gate-result.json devolve None."""
    sha = _commit(repo, {"quality-gate-result.json": "{nao-e-json"},
                  "feat: app")
    assert mod.evidencia_gate(sha) is None


def test_evidencia_gate_com_blocos(repo: Path) -> None:
    """JSON com blocos vira linhas `nome: STATUS (motivo)`."""
    dados = {"unit": {"status": "PASS", "motivo": "ok"},
             "cobertura": {"status": "FAIL", "motivo": "baixa"}}
    sha = _commit(repo, {"quality-gate-result.json": json.dumps(dados)},
                  "feat: app")
    texto = mod.evidencia_gate(sha)
    assert texto is not None
    assert "unit: PASS" in texto
    assert "cobertura: FAIL" in texto


# ------------------------------------------------------------ montar_prompt

def test_montar_prompt_risco_alto_antes_do_diff() -> None:
    """Texto de alto risco aparece no prompt antes do diff."""
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               risco_alto=["src/auth/x.py"])
    assert "MUDANÇA DE ALTO RISCO" in prompt
    assert "src/auth/x.py" in prompt
    assert prompt.index("MUDANÇA DE ALTO RISCO") < prompt.index("DIFFXYZ")


def test_montar_prompt_intencao_e_evidencia() -> None:
    """Intenção e evidência aparecem no prompt quando fornecidas."""
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao="== abc1234 ==\nfeat: faz algo",
                               evidencia="unit: PASS")
    assert "O que o commit diz que faz" in prompt
    assert "feat: faz algo" in prompt
    assert "Evidência de teste registrada" in prompt
    assert "unit: PASS" in prompt
    assert "INTENCAO:" in prompt


def test_montar_prompt_acima_do_teto_levanta() -> None:
    """Prompt maior que MAX_PROMPT_BYTES levanta RuntimeError."""
    with pytest.raises(RuntimeError):
        mod.montar_prompt("x" * (mod.MAX_PROMPT_BYTES + 1), "repo", "abc")


def test_montar_prompt_maximos_nao_estoura() -> None:
    """Diff+intenção+evidência+relatório nos tetos cabem no prompt."""
    prompt = mod.montar_prompt(
        "x" * mod.MAX_DIFF_BYTES, "repo", "abc1234",
        relatorio_anterior="r" * mod.MAX_RELATORIO,
        intencao="i" * mod.MAX_INTENCAO,
        evidencia="e" * mod.MAX_EVIDENCIA)
    assert len(prompt.encode("utf-8")) <= mod.MAX_PROMPT_BYTES


# ------------------------------------------------------------ parse_intencao

def test_parse_intencao_le_linha() -> None:
    """parse_intencao lê a linha INTENCAO da saída do revisor."""
    saida = "achados\nINTENCAO: PARCIAL\nVEREDITO: REPROVADO"
    assert mod.parse_intencao(saida) == "PARCIAL"


def test_parse_intencao_ausente() -> None:
    """Sem linha INTENCAO, parse_intencao devolve None."""
    assert mod.parse_intencao("VEREDITO: APROVADO") is None


# ------------------------------------------------- aprovação por regra (IA)

def test_revisar_com_trava_baixo_risco_sem_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commit só de .md é aprovado por regra, sem chamar a IA."""
    def _ia_proibida(*_args: object, **_kwargs: object) -> None:
        """Falha o teste se a IA for chamada para commit de baixo risco."""
        raise AssertionError("IA não deveria ser chamada")

    monkeypatch.setattr(mod, "revisar_com_ia", _ia_proibida)
    sha = _commit(repo, {"README.md": "# docs\n"}, "docs: readme")
    assert mod.revisar_com_trava(sha) == 0
    assert mod.esta_aprovado(sha)
    marcador = mod._marcador_aprovado(sha)
    assert marcador is not None
    assert marcador.get("familia") == "regra"


# ------------------------------------------------------- fluxo de correção

def test_fluxo_correcao_check_falha_antes_da_ia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check falho consome a 1ª rodada sem IA; a 2ª aprova e integra."""
    _git(repo, "config", "revisor.ordem", "check-primeiro")
    sha_original = _commit(repo, {"app.py": "x = 1\n"}, "feat: app")
    relatorio_check = ("- [ALTA] check — comando `pytest -q` falhou "
                       "(exit 1) — corrija até o comando passar\n"
                       "VEREDITO: REPROVADO")
    estado = {"rodada_check": 0, "chamadas_ia": 0, "novos": [],
              "relatorios_recebidos": []}

    def _tentativa_falsa(_original: str, _atual: str, relatorio: str,
                         _topo: str) -> tuple[str, str]:
        """Cria um commit novo de verdade no repo e devolve (sha, família)."""
        estado["relatorios_recebidos"].append(relatorio)
        numero = len(estado["novos"]) + 1
        novo = _commit(repo, {f"correcao{numero}.txt": f"{numero}\n"},
                       f"fix: correcao {numero}")
        estado["novos"].append(novo)
        return novo, "codex"

    def _checks_falso(_sha: str, _topo: str, **_kw: object) -> tuple[bool, str]:
        """Falha na primeira rodada e passa da segunda em diante."""
        estado["rodada_check"] += 1
        if estado["rodada_check"] == 1:
            return False, relatorio_check
        return True, ""

    def _revisao_falsa(*_args: object, **_kwargs: object) -> tuple:
        """Aprova sempre, contando quantas vezes a IA foi chamada."""
        estado["chamadas_ia"] += 1
        return "APROVADO", "aprovado sem problemas", {"codex"}

    monkeypatch.setattr(mod, "tentativa_correcao", _tentativa_falsa)
    monkeypatch.setattr(mod, "rodar_checks", _checks_falso)
    monkeypatch.setattr(mod, "revisar_por_partes", _revisao_falsa)

    assert mod.fluxo_correcao(sha_original, "relatório inicial",
                              str(repo)) == 0
    assert estado["chamadas_ia"] == 1
    assert estado["relatorios_recebidos"][1] == relatorio_check
    assert mod.ler_relatorio(estado["novos"][0]) == relatorio_check

