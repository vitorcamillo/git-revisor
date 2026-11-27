"""Testes de aceite: critérios `Aceite:` na mensagem do commit.

Seguem o estilo de `test_git_revisor_r12.py` (importar o módulo como
`mod`, fixture `repo` e helper `_commit`).
"""
from __future__ import annotations

import hashlib
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


# ---------------------------------------------------------- extrair_aceite

def test_extrair_numerados_ponto_e_parentese() -> None:
    """Itens `1.` e `1)` são reconhecidos como critérios."""
    msg = "feat: x\n\nAceite:\n1. primeiro\n2) segundo\n"
    assert mod.extrair_aceite(msg) == ["primeiro", "segundo"]


def test_extrair_traco_e_asterisco() -> None:
    """Itens `-` e `*` são reconhecidos como critérios."""
    msg = "feat: x\n\nAceite:\n- primeiro\n* segundo\n"
    assert mod.extrair_aceite(msg) == ["primeiro", "segundo"]


def test_extrair_continuacao_indentada_junta() -> None:
    """Linha indentada continua o item anterior com um espaço."""
    msg = ("feat: locação de filmes\n\nAceite:\n"
           "1. filme alugado não pode ser alugado de novo\n"
           "2. devolução libera o filme\n"
           "   para nova locação\n"
           "- cliente sem cadastro é recusado\n")
    assert mod.extrair_aceite(msg) == [
        "filme alugado não pode ser alugado de novo",
        "devolução libera o filme para nova locação",
        "cliente sem cadastro é recusado",
    ]


def test_extrair_termina_em_linha_em_branco() -> None:
    """Linha em branco encerra a seção."""
    msg = "feat: x\n\nAceite:\n1. primeiro\n\n2. depois do branco\n"
    assert mod.extrair_aceite(msg) == ["primeiro"]


def test_extrair_termina_em_linha_nao_indentada() -> None:
    """Trailer não indentado (ex.: Co-Authored-By) encerra a seção."""
    msg = ("feat: x\n\nAceite:\n1. primeiro\n"
           "Co-Authored-By: alguém\n2. fora da seção\n")
    assert mod.extrair_aceite(msg) == ["primeiro"]


def test_extrair_cabecalho_criterios_de_aceite() -> None:
    """Cabeçalho `Critérios de aceite:` (com e sem acento) vale."""
    assert mod.extrair_aceite(
        "feat: x\n\nCritérios de aceite:\n1. um\n") == ["um"]
    assert mod.extrair_aceite(
        "feat: x\n\nCriterios de aceite:\n1. um\n") == ["um"]
    assert mod.extrair_aceite("feat: x\n\nACEITE:\n1. um\n") == ["um"]


def test_extrair_cabecalho_sem_itens_vazio() -> None:
    """Cabeçalho sem nenhum item = sem critérios."""
    assert mod.extrair_aceite("feat: x\n\nAceite:\n") == []
    assert mod.extrair_aceite("feat: x\n\nAceite:\n\n1. fora\n") == []


def test_extrair_sem_secao_vazio() -> None:
    """Mensagem sem seção devolve lista vazia."""
    assert mod.extrair_aceite("feat: x\n\ncorpo sem critérios\n") == []


def test_extrair_aceite_no_meio_de_frase_nao_e_cabecalho() -> None:
    """`aceite:` no meio de uma frase não abre seção."""
    msg = "feat: x\n\nisto tem aceite: no meio da frase\n1. nada\n"
    assert mod.extrair_aceite(msg) == []


def test_extrair_so_primeira_secao_conta() -> None:
    """Só a primeira seção de cada mensagem conta."""
    msg = ("feat: x\n\nAceite:\n1. primeiro\n\ntexto\n\n"
           "Aceite:\n1. segundo\n")
    assert mod.extrair_aceite(msg) == ["primeiro"]


# --------------------------------------------------------- aceite_commits

def test_aceite_commits_numeracao_sequencial(repo: Path) -> None:
    """Numeração sequencial entre dois commits, com `[sha7]`."""
    sha1 = _commit(repo, {"a.txt": "1\n"},
                   "feat: um\n\nAceite:\n1. primeiro\n2. segundo\n")
    sha2 = _commit(repo, {"b.txt": "2\n"},
                   "feat: dois\n\nAceite:\n- terceiro\n")
    texto = mod.aceite_commits([sha1, sha2])
    assert texto is not None
    linhas = texto.splitlines()
    assert linhas[0] == f"1. [{sha1[:7]}] primeiro"
    assert linhas[1] == f"2. [{sha1[:7]}] segundo"
    assert linhas[2] == f"3. [{sha2[:7]}] terceiro"


def test_aceite_commits_sem_criterios_none(repo: Path) -> None:
    """Sem critério em nenhum commit, devolve None."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: sem seção")
    assert mod.aceite_commits([sha]) is None


def test_aceite_commits_teto_criterios(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acima de MAX_CRITERIOS, termina com `(+K ...)` e K certo."""
    sha = _commit(repo, {"a.txt": "1\n"},
                  "feat: x\n\nAceite:\n1. um\n2. dois\n3. três\n")
    monkeypatch.setattr(mod, "MAX_CRITERIOS", 2)
    texto = mod.aceite_commits([sha])
    assert texto is not None
    linhas = texto.splitlines()
    assert len(linhas) == 3
    assert linhas[2] == "(+1 critérios omitidos por tamanho)"


def test_aceite_commits_teto_bytes(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acima de MAX_ACEITE em bytes, termina com `(+K ...)` e K certo."""
    sha = _commit(repo, {"a.txt": "1\n"},
                  "feat: x\n\nAceite:\n1. primeiro critério\n"
                  "2. segundo critério\n")
    monkeypatch.setattr(mod, "MAX_ACEITE", 30)
    texto = mod.aceite_commits([sha])
    assert texto is not None
    linhas = texto.splitlines()
    assert linhas[-1].startswith("(+")
    assert linhas[-1] == "(+2 critérios omitidos por tamanho)"


def test_aceite_commits_criterio_longo_cortado(repo: Path) -> None:
    """Critério individual é cortado em 300 caracteres."""
    longo = "c" * 400
    sha = _commit(repo, {"a.txt": "1\n"}, f"feat: x\n\nAceite:\n1. {longo}\n")
    texto = mod.aceite_commits([sha])
    assert texto == f"1. [{sha[:7]}] {'c' * 300}"


# -------------------------------------------------------- intencao_commits

def test_intencao_commits_com_aceite_traz_marcador(repo: Path) -> None:
    """Com seção, o bloco do marcador entra na intenção."""
    sha = _commit(repo, {"a.txt": "1\n"},
                  "feat: x\n\nAceite:\n1. primeiro\n")
    texto = mod.intencao_commits([sha])
    assert mod.MARCADOR_ACEITE in texto
    assert f"1. [{sha[:7]}] primeiro" in texto


def test_intencao_commits_sem_secao_sem_marcador(repo: Path) -> None:
    """Sem seção, nenhum bloco de aceite entra."""
    sha = _commit(repo, {"a.txt": "1\n"}, "feat: sem seção")
    assert mod.MARCADOR_ACEITE not in mod.intencao_commits([sha])


def test_intencao_commits_aceite_sobrevive_ao_corte(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Critério extraído da mensagem COMPLETA sobrevive ao corte."""
    corpo = "y" * 300
    sha = _commit(repo, {"a.txt": "1\n"},
                  f"feat: x\n\n{corpo}\n\nAceite:\n1. criterio vivo\n")
    monkeypatch.setattr(mod, "MAX_INTENCAO", 100)
    texto = mod.intencao_commits([sha])
    assert mod.MARCADOR_ACEITE in texto
    assert "criterio vivo" in texto


def test_intencao_commits_ordem_mensagens_aceite_errata(repo: Path) -> None:
    """Ordem dos blocos: mensagens, aceite, errata."""
    sha = _commit(repo, {"a.txt": "1\n"},
                  "feat: x\n\nAceite:\n1. primeiro\n")
    _commit(repo, {".revisor/errata.md": f"- {sha[:7]}: corrige\n"},
            "docs: errata")
    texto = mod.intencao_commits([sha], ponta="HEAD")
    pos_msg = texto.index(f"== {sha[:7]} ==")
    pos_aceite = texto.index(mod.MARCADOR_ACEITE)
    pos_errata = texto.index("== errata (.revisor/errata.md) ==")
    assert pos_msg < pos_aceite < pos_errata


def test_intencao_commits_aceite_de_outro_commit(repo: Path) -> None:
    """`aceite_de` usa os critérios de outro commit."""
    com = _commit(repo, {"a.txt": "1\n"},
                  "feat: com\n\nAceite:\n1. criterio alheio\n")
    sem = _commit(repo, {"b.txt": "2\n"}, "feat: sem seção")
    assert "criterio alheio" not in mod.intencao_commits([sem])
    com_aceite = mod.intencao_commits([sem], aceite_de=[com])
    assert "criterio alheio" in com_aceite


# ------------------------------------------------------------ montar_prompt

def test_montar_prompt_com_marcador_traz_regra_media() -> None:
    """Com marcador, o prompt reprova critério não atendido (MEDIA)."""
    intencao = (f"== abc1234 ==\nfeat: x\n\n{mod.MARCADOR_ACEITE}\n"
                "1. [abc1234] primeiro\n")
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao=intencao)
    assert "é problema MEDIA (reprova)" in prompt
    assert "ACEITE: <atendidos>/<total>" in prompt


def test_montar_prompt_sem_marcador_sem_regra_aceite() -> None:
    """Sem marcador, nada muda no prompt."""
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao="feat: faz algo")
    assert "Critérios de aceite" not in prompt


def test_montar_prompt_multipartes_aceite_nao_verificavel() -> None:
    """Com multipartes, o prompt admite `ACEITE: NAO_VERIFICAVEL`."""
    intencao = (f"== abc1234 ==\nfeat: x\n\n{mod.MARCADOR_ACEITE}\n"
                "1. [abc1234] primeiro\n")
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao=intencao, multipartes=True)
    assert "ACEITE: NAO_VERIFICAVEL" in prompt


def test_montar_prompt_regra_antiga_continua() -> None:
    """A regra antiga (divergência BAIXA) continua presente."""
    intencao = (f"== abc1234 ==\nfeat: x\n\n{mod.MARCADOR_ACEITE}\n"
                "1. [abc1234] primeiro\n")
    prompt = mod.montar_prompt("DIFFXYZ", "repo", "abc1234",
                               intencao=intencao)
    assert "é problema BAIXA (não reprova)" in prompt


# ------------------------------------------------------------- parse_aceite

def test_parse_aceite_fracao() -> None:
    """`ACEITE: 4/5` devolve a fração."""
    assert mod.parse_aceite("linhas\nACEITE: 4/5\nVEREDITO: APROVADO") == "4/5"


def test_parse_aceite_nao_verificavel() -> None:
    """`ACEITE: NAO_VERIFICAVEL` é valor válido."""
    assert mod.parse_aceite("ACEITE: NAO_VERIFICAVEL") == "NAO_VERIFICAVEL"


def test_parse_aceite_ultima_vence() -> None:
    """Última linha ACEITE válida vence."""
    saida = "ACEITE: 1/2\ntexto\nACEITE: 2/2\n"
    assert mod.parse_aceite(saida) == "2/2"


def test_parse_aceite_ausente_none() -> None:
    """Sem linha ACEITE, devolve None."""
    assert mod.parse_aceite("INTENCAO: CUMPRIDA\nVEREDITO: APROVADO") is None


def test_parse_aceite_malformada_none() -> None:
    """Linha malformada (`ACEITE: quase`) não casa."""
    assert mod.parse_aceite("ACEITE: quase") is None


def test_montar_prompt_teto_cabe_mensagens_aceite_e_errata() -> None:
    """Com os três blocos no teto, o fim da errata ainda entra no prompt."""
    errata = "e" * (mod.MAX_ERRATA - 9) + "FIMERRATA"
    intencao = ("m" * mod.MAX_INTENCAO + f"\n{mod.MARCADOR_ACEITE}\n"
                + "c" * mod.MAX_ACEITE
                + "\n== errata (.revisor/errata.md) ==\n" + errata)
    prompt = mod.montar_prompt("DIFF", "repo", "abc1234", intencao=intencao)
    assert "FIMERRATA" in prompt


# ------------------------------------------------------- caches e duplicados

def test_aceite_commits_nao_duplica_criterio_repetido(repo: Path) -> None:
    """Correção que repete a mensagem do original não dobra os critérios."""
    msg = "feat: x\n\nAceite:\n1. regra a\n"
    a = _commit(repo, {"a.txt": "1\n"}, msg)
    b = _commit(repo, {"a.txt": "2\n"}, msg)
    assert mod.aceite_commits([a, b]) == f"1. [{a[:7]}] regra a"


def test_aceite_da_intencao_ignora_sha_e_errata(repo: Path) -> None:
    """Chave de cache: só o texto dos critérios, sem sha7 e sem errata."""
    a = _commit(repo, {"a.txt": "1\n"}, "feat: x\n\nAceite:\n1. regra a\n")
    _commit(repo, {".revisor/errata.md": "- abc: nada\n"}, "docs: errata")
    intencao = mod.intencao_commits([a], ponta="HEAD")
    assert mod.aceite_da_intencao(intencao) == "1. regra a"
    assert mod.aceite_da_intencao("feat: sem seção") == ""
    assert mod.aceite_da_intencao(None) == ""


def test_chave_parte_muda_com_aceite_e_preserva_sem() -> None:
    """Critério novo invalida o cache da parte; sem critério a chave não muda."""
    sem = mod.chave_parte("DIFF", None, None)
    assert mod.chave_parte("DIFF", None, None, "") == sem
    com = mod.chave_parte("DIFF", None, None, "1. regra a")
    assert com != sem
    assert mod.chave_parte("DIFF", None, None, "1. regra b") != com


def test_aceite_da_intencao_preserva_referencia_dentro_do_criterio() -> None:
    """Só o `[sha7]` após a numeração sai; referência no texto fica."""
    base = f"feat: x\n{mod.MARCADOR_ACEITE}\n"
    a = mod.aceite_da_intencao(base + "1. [abc1234] regra [1111111] vale\n")
    b = mod.aceite_da_intencao(base + "1. [abc1234] regra [2222222] vale\n")
    assert a == "1. regra [1111111] vale"
    assert b == "1. regra [2222222] vale"
    assert a != b
    assert mod.chave_parte("DIFF", None, None, a) != mod.chave_parte(
        "DIFF", None, None, b)


def test_chave_parte_multipartes_so_com_aceite() -> None:
    """Com critérios, parte e diff completo têm chaves distintas; sem, não."""
    sem = mod.chave_parte("DIFF", None, None)
    assert mod.chave_parte("DIFF", None, None, "", multipartes=True) == sem
    completo = mod.chave_parte("DIFF", None, None, "1. regra a")
    assert mod.chave_parte(
        "DIFF", None, None, "1. regra a", multipartes=False) == completo
    assert mod.chave_parte(
        "DIFF", None, None, "1. regra a", multipartes=True) != completo


def _chave_legada_sem_multipartes(parte: str, aceite: str) -> str:
    """Chave gravada antes do campo `multipartes` (sem ele no payload)."""
    dados = {"parte": parte, "risco": [], "contexto_repo": "",
             "versao": mod.VERSAO_CACHE_PARTE, "aceite": aceite}
    return hashlib.sha256(json.dumps(
        dados, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def test_chave_completa_com_aceite_invalida_cache_antigo() -> None:
    """Migração: chave completa nova difere da legada sem `multipartes`."""
    legada = _chave_legada_sem_multipartes("DIFF", "1. regra a")
    assert mod.chave_parte(
        "DIFF", None, None, "1. regra a", multipartes=False) != legada
    assert mod.chave_parte(
        "DIFF", None, None, "1. regra a", multipartes=True) != legada


def test_cache_antigo_multipartes_nao_aprova_diff_completo(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Aprovação legada (sem `multipartes`) não é reusada como completa."""
    intencao = f"feat: x\n{mod.MARCADOR_ACEITE}\n1. [abc1234] regra a"
    aceite = mod.aceite_da_intencao(intencao)
    texto = "diff pequeno"
    cache: dict[str, str] = {
        _chave_legada_sem_multipartes(texto, aceite): "codex"}
    chamadas: list[str] = []
    monkeypatch.setattr(mod, "cache_parte_ativo", lambda topo=None: True)
    monkeypatch.setattr(mod, "cache_veredito_ativo", lambda topo=None: True)
    monkeypatch.setattr(mod, "ler_cache_parte", lambda chave: cache.get(chave))
    monkeypatch.setattr(
        mod, "gravar_cache_parte",
        lambda chave, resumo: cache.__setitem__(chave, "codex"))
    monkeypatch.setattr(
        mod, "cfg",
        lambda chave, padrao: padrao if chave == "maxPartes" else "1")
    monkeypatch.setattr(mod, "familia_revisor", lambda saida: "codex")

    def falso(prompt: str, cwd: str) -> tuple[str, str]:
        chamadas.append(prompt)
        return "APROVADO", "ok"

    monkeypatch.setattr(mod, "revisar_com_ia", falso)
    veredito, _, _ = mod.revisar_por_partes(
        texto, "repo", "abc1234", intencao=intencao)
    assert veredito == "APROVADO"
    assert len(chamadas) == 1


def test_parte_multipartes_nao_reaproveitada_como_diff_completo(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Parte aprovada em multipartes é revisada de novo como diff completo."""
    cache: dict[str, str] = {}
    chamadas: list[str] = []
    monkeypatch.setattr(mod, "MAX_DIFF_BYTES", 500)
    monkeypatch.setattr(mod, "cache_parte_ativo", lambda topo=None: True)
    monkeypatch.setattr(mod, "cache_veredito_ativo", lambda topo=None: True)
    monkeypatch.setattr(mod, "ler_cache_parte", lambda chave: cache.get(chave))
    monkeypatch.setattr(
        mod, "gravar_cache_parte",
        lambda chave, resumo: cache.__setitem__(chave, "codex"))
    monkeypatch.setattr(
        mod, "cfg",
        lambda chave, padrao: padrao if chave == "maxPartes" else "1")
    monkeypatch.setattr(mod, "familia_revisor", lambda saida: "codex")

    def falso(prompt: str, cwd: str) -> tuple[str, str]:
        chamadas.append(prompt)
        return "APROVADO", "ok"

    monkeypatch.setattr(mod, "revisar_com_ia", falso)
    intencao = f"feat: x\n{mod.MARCADOR_ACEITE}\n1. [abc1234] regra a"
    texto = ("commit " + "a" * 450 + "\n") + ("commit " + "b" * 450 + "\n")
    veredito, _, _ = mod.revisar_por_partes(
        texto, "repo", "abc1234", intencao=intencao)
    assert veredito == "APROVADO"
    # 2 partes + conferência final de aceite (multipartes com critérios).
    assert len(chamadas) == 3
    primeira = mod.dividir_em_partes(texto)[0]
    veredito, _, _ = mod.revisar_por_partes(
        primeira, "repo", "abc1234", intencao=intencao)
    assert veredito == "APROVADO"
    assert len(chamadas) == 4


def test_revisar_diff_cache_de_arvore_nao_cobre_aceite_novo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Árvore já aprovada volta ao revisor quando surgem critérios de aceite."""
    base = _commit(repo, {"a.txt": "1\n"}, "feat: base")
    ponta = _commit(repo, {"a.txt": "2\n"}, "feat: muda")
    chamadas: list[str | None] = []

    def falso(texto, topo, commits, **kw):
        chamadas.append(kw.get("intencao"))
        return "APROVADO", "ok", {"codex"}

    monkeypatch.setattr(mod, "revisar_por_partes", falso)
    com = f"feat: muda\n{mod.MARCADOR_ACEITE}\n1. [{ponta[:7]}] regra a"
    mod.revisar_diff(base, ponta, str(repo), "c")
    mod.revisar_diff(base, ponta, str(repo), "c")            # cache de árvore
    assert len(chamadas) == 1
    mod.revisar_diff(base, ponta, str(repo), "c", intencao=com)
    assert len(chamadas) == 2                                # aceite novo revisa
    mod.revisar_diff(base, ponta, str(repo), "c", intencao=com)
    assert len(chamadas) == 2                                # e fica em cache


# ------------------------------------------- conferência final de aceite

_RODAPE_CODEX = "--- codex-ask: modo=review gpt ---"
_OK_PARTE = ("- nenhum problema relevante\nVEREDITO: APROVADO\n"
             + _RODAPE_CODEX)


def _duas_partes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Força `dividir_em_partes` a devolver sempre 2 partes."""
    monkeypatch.setattr(mod, "dividir_em_partes",
                        lambda t: ["PARTE-A\n" + t, "PARTE-B\n" + t])


def _texto_conferencia() -> str:
    """Diff com dois arquivos, para a lista da conferência."""
    return ("diff --git a/a.txt b/a.txt\n+x\n"
            "diff --git a/b.txt b/b.txt\n+y\n")


def _intencao_regra_a() -> str:
    """Intenção com um critério de aceite."""
    return f"feat: x\n{mod.MARCADOR_ACEITE}\n1. [abc1234] regra a"


def test_conferencia_aprovada_faz_tres_chamadas(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2 partes + aceite aprovadas: 3 chamadas, a última é a conferência."""
    _duas_partes(monkeypatch)
    chamadas: list[str] = []
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, cwd: (chamadas.append(prompt),
                             ("APROVADO", _OK_PARTE))[1])
    veredito, saida, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234",
        intencao=_intencao_regra_a())
    assert veredito == "APROVADO"
    assert len(chamadas) == 3
    conf = chamadas[-1]
    assert "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" in conf
    assert "1. regra a" in conf
    assert "a.txt" in conf and "b.txt" in conf
    assert "aceite conferido" in saida


def test_conferencia_reprovada_reprova_o_conjunto(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Conferência REPROVADO: veredito REPROVADO e relatório é o dela."""
    _duas_partes(monkeypatch)
    reprovada = ("- [MEDIA] aceite 1 — falta X — correção Y\n"
                 "ACEITE: 0/1\nVEREDITO: REPROVADO\n" + _RODAPE_CODEX)
    chamadas: list[str] = []

    def falso(prompt: str, cwd: str) -> tuple[str, str]:
        chamadas.append(prompt)
        if "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" in prompt:
            return "REPROVADO", reprovada
        return "APROVADO", _OK_PARTE

    monkeypatch.setattr(mod, "revisar_com_ia", falso)
    veredito, saida, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234",
        intencao=_intencao_regra_a())
    assert veredito == "REPROVADO"
    assert saida == reprovada
    assert len(chamadas) == 3


def test_conferencia_sem_cota_vale_para_o_todo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Conferência SEM_COTA: o todo devolve SEM_COTA."""
    _duas_partes(monkeypatch)

    def falso(prompt: str, cwd: str) -> tuple[str, str]:
        if "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" in prompt:
            return "SEM_COTA", "x"
        return "APROVADO", _OK_PARTE

    monkeypatch.setattr(mod, "revisar_com_ia", falso)
    veredito, saida, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234",
        intencao=_intencao_regra_a())
    assert veredito == "SEM_COTA"
    assert saida == "x"


def test_duas_partes_sem_aceite_sem_conferencia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2 partes sem aceite: 2 chamadas, nenhuma conferência."""
    _duas_partes(monkeypatch)
    chamadas: list[str] = []
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, cwd: (chamadas.append(prompt),
                             ("APROVADO", _OK_PARTE))[1])
    veredito, _, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234",
        intencao="feat: sem seção")
    assert veredito == "APROVADO"
    assert len(chamadas) == 2
    assert not [p for p in chamadas
                if "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" in p]


def test_uma_parte_com_aceite_sem_conferencia(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1 parte + aceite: 1 chamada, nenhuma conferência."""
    chamadas: list[str] = []
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, cwd: (chamadas.append(prompt),
                             ("APROVADO", _OK_PARTE))[1])
    veredito, _, _ = mod.revisar_por_partes(
        "diff pequeno", str(repo), "abc1234", intencao=_intencao_regra_a())
    assert veredito == "APROVADO"
    assert len(chamadas) == 1
    assert "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" not in chamadas[0]


def test_conferencia_em_cache_na_segunda_chamada(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2ª chamada igual: partes e conferência em cache, sem chamar o revisor."""
    _duas_partes(monkeypatch)
    chamadas: list[str] = []
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, cwd: (chamadas.append(prompt),
                             ("APROVADO", _OK_PARTE))[1])
    intencao = _intencao_regra_a()
    veredito, saida, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234", intencao=intencao)
    assert veredito == "APROVADO"
    assert len(chamadas) == 3
    veredito, saida, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234", intencao=intencao)
    assert veredito == "APROVADO"
    assert "aceite conferido" in saida
    assert len(chamadas) == 3  # nada novo: partes e conferência em cache


def test_conferencia_roda_de_novo_com_outro_criterio(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mesmas 2 partes com outro critério: a conferência roda de novo."""
    _duas_partes(monkeypatch)
    chamadas: list[str] = []
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, cwd: (chamadas.append(prompt),
                             ("APROVADO", _OK_PARTE))[1])
    texto = _texto_conferencia()
    mod.revisar_por_partes(texto, str(repo), "abc1234",
                           intencao=_intencao_regra_a())
    assert len(chamadas) == 3
    outra = f"feat: x\n{mod.MARCADOR_ACEITE}\n1. [abc1234] regra b"
    veredito, _, _ = mod.revisar_por_partes(
        texto, str(repo), "abc1234", intencao=outra)
    assert veredito == "APROVADO"
    conferencias = [p for p in chamadas
                    if "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" in p]
    assert len(conferencias) == 2
    assert "1. regra b" in conferencias[-1]


def test_conferencia_no_ramo_sequencial(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com `revisor.paralelo 1`, a conferência acontece do mesmo jeito."""
    _git(repo, "config", "revisor.paralelo", "1")
    _duas_partes(monkeypatch)
    chamadas: list[str] = []
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, cwd: (chamadas.append(prompt),
                             ("APROVADO", _OK_PARTE))[1])
    veredito, saida, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234",
        intencao=_intencao_regra_a())
    assert veredito == "APROVADO"
    assert len(chamadas) == 3
    assert "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" in chamadas[-1]
    assert "aceite conferido" in saida


def test_conferencia_no_ramo_paralelo(
        repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ramo paralelo (padrão), a conferência acontece do mesmo jeito."""
    _duas_partes(monkeypatch)
    chamadas: list[str] = []
    monkeypatch.setattr(
        mod, "revisar_com_ia",
        lambda prompt, cwd: (chamadas.append(prompt),
                             ("APROVADO", _OK_PARTE))[1])
    veredito, saida, _ = mod.revisar_por_partes(
        _texto_conferencia(), str(repo), "abc1234",
        intencao=_intencao_regra_a())
    assert veredito == "APROVADO"
    assert len(chamadas) == 3
    assert "CONFERÊNCIA DE CRITÉRIOS DE ACEITE" in chamadas[-1]
    assert "aceite conferido" in saida


def test_arquivos_do_diff_dois_repeticao_e_rename() -> None:
    """Dois arquivos, repetição ignorada e rename devolve o destino."""
    texto = ("diff --git a/a.txt b/a.txt\n+x\n"
             "diff --git a/a.txt b/a.txt\n+y\n"
             "diff --git a/antigo.txt b/novo.txt\n+z\n")
    assert mod.arquivos_do_diff(texto) == ["a.txt", "novo.txt"]
    assert mod.arquivos_do_diff("sem diff aqui") == []


def test_montar_prompt_conferencia_mais_de_60() -> None:
    """Acima de 60 arquivos, a lista termina com ` e mais K`."""
    arquivos = [f"f{i}.txt" for i in range(65)]
    prompt = mod.montar_prompt_conferencia(
        "repo", "abc1234", "1. regra a", arquivos, 2)
    assert " e mais 5" in prompt
    assert "f59.txt" in prompt
    assert "f64.txt" not in prompt
    assert "em 2 partes separadas" in prompt
    assert "Repositório: repo  Commit(s): abc1234" in prompt
    assert "1. regra a" in prompt
